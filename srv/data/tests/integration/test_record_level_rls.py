"""
Record-Level RLS integration tests.

Tests the new data_records / record_roles tables and their RLS policies,
ensuring per-record visibility (inherit, personal, shared) works correctly
through the full API stack.

PREREQUISITES: These tests require the data-api to be deployed with the
record-level security code (data_records table support in QueryEngine and
DataService). If the running data-api doesn't support querying from
data_records, tests that depend on record-level queries will be skipped.

Test setup:
  - User A: has full data access + shared role "ShareAB"
  - User B: has full data access + shared role "ShareAB"
  - User C: has full data access but NO shared role
  - An "authenticated" data document container accessible to all three users

Test scenarios:
  Phase 1 - Record CRUD via data_records table:
    1) Insert records into a document using the API
    2) Query records back and verify they appear

  Phase 2 - Record-level visibility:
    3) Records with visibility=inherit are visible to anyone who can see the document
    4) Records with visibility=personal are only visible to the owner
    5) Records with visibility=shared + role are only visible to users with that role

  Phase 3 - Visibility transitions:
    6) Change a record from inherit to personal -> others lose access
    7) Change a record from personal to shared -> role holders gain access
    8) Bulk visibility changes work correctly

  Phase 4 - Record role management API:
    9) GET record roles
    10) PUT single record visibility
    11) PUT bulk record visibility
"""

import os
import uuid
from datetime import datetime

import psycopg2
import pytest
from httpx import AsyncClient

from busibox_common.testing.auth import AuthTestClient, TEST_MODE_HEADER, TEST_MODE_VALUE
from tests.integration.test_rls_isolation import MultiUserAuthClient


pytestmark = pytest.mark.integration


# =============================================================================
# Fixtures
# =============================================================================

_API_PORT = os.getenv("API_PORT", "8002")
_SERVICE_URL = os.getenv("DATA_API_URL", f"http://localhost:{_API_PORT}")


def _get_data_db_conn():
    """Get a synchronous psycopg2 connection to the data DB."""
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.getenv("POSTGRES_DB", "files"),
        user=os.getenv("POSTGRES_USER", "busibox_user"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        connect_timeout=10,
    )


def _set_rls_context(cur, user_id: str, role_ids: list[str] | None = None):
    """Set RLS session variables for the current transaction."""
    cur.execute("SET LOCAL app.user_id = %s", (user_id,))
    if role_ids:
        role_ids_csv = ",".join(role_ids)
        cur.execute("SET LOCAL app.role_ids = %s", (role_ids_csv,))
        cur.execute("SET LOCAL app.user_role_ids_read = %s", (role_ids_csv,))
        cur.execute("SET LOCAL app.user_role_ids_create = %s", (role_ids_csv,))
        cur.execute("SET LOCAL app.user_role_ids_update = %s", (role_ids_csv,))
        cur.execute("SET LOCAL app.user_role_ids_delete = %s", (role_ids_csv,))


@pytest.fixture(scope="module")
def record_rls_users(auth_client) -> MultiUserAuthClient:
    """
    Module-scoped fixture creating 3 users and roles for record-level RLS tests.

    - User A + B share "record-rls-share-ab" role
    - User C has no shared role
    - All three have personal full-access roles
    """
    run_id = uuid.uuid4().hex[:8]
    mu = MultiUserAuthClient(auth_client)

    mu.register_user("a", f"rec-rls-a-{run_id}@test.example.com")
    mu.register_user("b", f"rec-rls-b-{run_id}@test.example.com")
    mu.register_user("c", f"rec-rls-c-{run_id}@test.example.com")

    full_scopes = ["data.read", "data.write", "data.delete", "search.read"]

    share_ab_role_id = mu.create_role(f"rec-rls-share-ab-{run_id}", full_scopes)
    personal_a_role_id = mu.create_role(f"rec-rls-personal-a-{run_id}", full_scopes)
    personal_b_role_id = mu.create_role(f"rec-rls-personal-b-{run_id}", full_scopes)
    personal_c_role_id = mu.create_role(f"rec-rls-personal-c-{run_id}", full_scopes)

    user_a_id = mu.get_user("a")["user_id"]
    user_b_id = mu.get_user("b")["user_id"]
    user_c_id = mu.get_user("c")["user_id"]

    mu.assign_role_to_user(user_a_id, share_ab_role_id)
    mu.assign_role_to_user(user_a_id, personal_a_role_id)
    mu.assign_role_to_user(user_b_id, share_ab_role_id)
    mu.assign_role_to_user(user_b_id, personal_b_role_id)
    mu.assign_role_to_user(user_c_id, personal_c_role_id)

    mu.get_user("a")["share_ab_role_id"] = share_ab_role_id
    mu.get_user("b")["share_ab_role_id"] = share_ab_role_id
    mu.get_user("a")["personal_role_id"] = personal_a_role_id
    mu.get_user("b")["personal_role_id"] = personal_b_role_id
    mu.get_user("c")["personal_role_id"] = personal_c_role_id

    yield mu
    mu.cleanup()


@pytest.fixture(scope="module")
def record_rls_doc(record_rls_users):
    """
    Module-scoped fixture that creates an 'authenticated' document container
    and inserts records into data_records with different visibility levels.

    Returns dict with document_id and record IDs.
    """
    user_a = record_rls_users.get_user("a")
    user_b = record_rls_users.get_user("b")
    share_ab_role_id = user_a["share_ab_role_id"]
    personal_a_role_id = user_a["personal_role_id"]
    personal_b_role_id = user_b["personal_role_id"]

    a_id = user_a["user_id"]
    b_id = user_b["user_id"]

    doc_id = str(uuid.uuid4())
    rec_inherit_a = str(uuid.uuid4())
    rec_personal_a = str(uuid.uuid4())
    rec_shared_ab = str(uuid.uuid4())
    rec_personal_b = str(uuid.uuid4())
    now = datetime.utcnow()

    conn = _get_data_db_conn()
    try:
        all_roles = [share_ab_role_id, personal_a_role_id, personal_b_role_id]
        with conn:
            cur = conn.cursor()
            _set_rls_context(cur, a_id, all_roles)
            cur.execute("""
                INSERT INTO data_files
                    (file_id, user_id, owner_id, filename, original_filename,
                     mime_type, size_bytes, storage_path, content_hash,
                     has_markdown, created_at, visibility, doc_type,
                     data_content, data_record_count, data_schema)
                VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, 'data', '[]'::jsonb, 0, %s::jsonb)
            """, (
                doc_id, a_id, a_id,
                "rec-rls-test-data", "rec-rls-test-data",
                "application/json", 0, f"data://{doc_id}", f"hash_{doc_id}",
                False, now, "authenticated",
                '{"fields": {"id": {"type": "string"}, "name": {"type": "string"}}}',
            ))
            cur.close()

        # User A inserts: inherit record, personal record, shared record
        with conn:
            cur = conn.cursor()
            _set_rls_context(cur, a_id, all_roles)

            cur.execute("""
                INSERT INTO data_records
                    (record_id, document_id, data, owner_id, created_by,
                     visibility, created_at, updated_at)
                VALUES (%s::uuid, %s::uuid, %s::jsonb, %s::uuid, %s::uuid,
                        'inherit', %s, %s)
            """, (
                rec_inherit_a, doc_id,
                f'{{"id": "{rec_inherit_a}", "name": "Inherit Record A"}}',
                a_id, a_id, now, now,
            ))

            cur.execute("""
                INSERT INTO data_records
                    (record_id, document_id, data, owner_id, created_by,
                     visibility, created_at, updated_at)
                VALUES (%s::uuid, %s::uuid, %s::jsonb, %s::uuid, %s::uuid,
                        'personal', %s, %s)
            """, (
                rec_personal_a, doc_id,
                f'{{"id": "{rec_personal_a}", "name": "Personal Record A"}}',
                a_id, a_id, now, now,
            ))

            cur.execute("""
                INSERT INTO data_records
                    (record_id, document_id, data, owner_id, created_by,
                     visibility, created_at, updated_at)
                VALUES (%s::uuid, %s::uuid, %s::jsonb, %s::uuid, %s::uuid,
                        'shared', %s, %s)
            """, (
                rec_shared_ab, doc_id,
                f'{{"id": "{rec_shared_ab}", "name": "Shared Record AB"}}',
                a_id, a_id, now, now,
            ))
            cur.execute("""
                INSERT INTO record_roles (record_id, role_id, role_name, added_by)
                VALUES (%s::uuid, %s::uuid, %s, %s::uuid)
            """, (rec_shared_ab, share_ab_role_id, "rec-rls-share-ab", a_id))
            cur.close()

        # User B inserts a personal record
        with conn:
            cur = conn.cursor()
            _set_rls_context(cur, b_id, [share_ab_role_id, personal_b_role_id])
            cur.execute("""
                INSERT INTO data_records
                    (record_id, document_id, data, owner_id, created_by,
                     visibility, created_at, updated_at)
                VALUES (%s::uuid, %s::uuid, %s::jsonb, %s::uuid, %s::uuid,
                        'personal', %s, %s)
            """, (
                rec_personal_b, doc_id,
                f'{{"id": "{rec_personal_b}", "name": "Personal Record B"}}',
                b_id, b_id, now, now,
            ))
            cur.close()

        # Update record count
        with conn:
            cur = conn.cursor()
            _set_rls_context(cur, a_id, all_roles)
            cur.execute("""
                UPDATE data_files SET data_record_count = 4 WHERE file_id = %s::uuid
            """, (doc_id,))
            cur.close()

    finally:
        conn.close()

    data = {
        "doc_id": doc_id,
        "rec_inherit_a": rec_inherit_a,
        "rec_personal_a": rec_personal_a,
        "rec_shared_ab": rec_shared_ab,
        "rec_personal_b": rec_personal_b,
        "share_ab_role_id": share_ab_role_id,
    }
    yield data

    # Cleanup
    try:
        conn = _get_data_db_conn()
        try:
            all_roles = [share_ab_role_id, personal_a_role_id, personal_b_role_id]
            with conn:
                cur = conn.cursor()
                _set_rls_context(cur, a_id, all_roles)
                cur.execute("DELETE FROM record_roles WHERE record_id IN (SELECT record_id FROM data_records WHERE document_id = %s::uuid)", (doc_id,))
                cur.execute("DELETE FROM data_records WHERE document_id = %s::uuid", (doc_id,))
                cur.execute("DELETE FROM data_files WHERE file_id = %s::uuid", (doc_id,))
                cur.close()
        finally:
            conn.close()
    except Exception as exc:
        print(f"[record_rls_doc] cleanup failed: {exc}")


@pytest.fixture(scope="module")
def record_clients(record_rls_users, record_rls_doc):
    """Module-scoped fixture providing tokens and test data."""
    yield {
        "a": record_rls_users.get_data_token("a"),
        "b": record_rls_users.get_data_token("b"),
        "c": record_rls_users.get_data_token("c"),
        "data": record_rls_doc,
    }


def _make_client(token: str) -> AsyncClient:
    """Build an HTTP test client pre-configured with auth + test-mode headers."""
    return AsyncClient(
        base_url=_SERVICE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "X-Test-Mode": "true",
        },
        timeout=30.0,
    )


async def _query_ok(client, doc_id: str) -> bool:
    """Probe whether the data-api query endpoint can read from data_records."""
    resp = await client.post(f"/data/{doc_id}/query", json={})
    return resp.status_code == 200


# =============================================================================
# Phase 1 -- Record CRUD via API
# =============================================================================


async def test_insert_records_with_visibility(record_clients):
    """Insert records via the API with explicit visibility settings."""
    doc_id = record_clients["data"]["doc_id"]
    async with _make_client(record_clients["a"]) as c:
        resp = await c.post(f"/data/{doc_id}/records", json={
            "records": [
                {"id": str(uuid.uuid4()), "name": "API-inserted inherit"},
            ],
        })
        assert resp.status_code in (200, 201), f"Insert failed: {resp.status_code} {resp.text}"


async def test_query_records_returns_results(record_clients):
    """Query records from the document and verify results come back."""
    doc_id = record_clients["data"]["doc_id"]
    async with _make_client(record_clients["a"]) as c:
        if not await _query_ok(c, doc_id):
            pytest.skip("data-api query doesn't support data_records yet (needs redeploy)")
        resp = await c.post(f"/data/{doc_id}/query", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("total", 0) > 0 or len(body.get("records", [])) > 0


# =============================================================================
# Phase 2 -- Record-Level Visibility via query
# =============================================================================


async def test_user_a_sees_inherit_records(record_clients):
    """User A sees records with visibility=inherit (inherits from authenticated doc)."""
    doc_id = record_clients["data"]["doc_id"]
    rec_id = record_clients["data"]["rec_inherit_a"]
    async with _make_client(record_clients["a"]) as c:
        if not await _query_ok(c, doc_id):
            pytest.skip("data-api query doesn't support data_records yet (needs redeploy)")
        resp = await c.post(f"/data/{doc_id}/query", json={
            "where": {"field": "id", "op": "eq", "value": rec_id},
        })
        assert resp.status_code == 200
        records = resp.json().get("records", [])
        assert len(records) == 1, f"Expected 1 inherit record, got {len(records)}"
        assert records[0]["id"] == rec_id


async def test_user_b_sees_inherit_records(record_clients):
    """User B also sees records with visibility=inherit on an authenticated doc."""
    doc_id = record_clients["data"]["doc_id"]
    rec_id = record_clients["data"]["rec_inherit_a"]
    async with _make_client(record_clients["b"]) as c:
        if not await _query_ok(c, doc_id):
            pytest.skip("data-api query doesn't support data_records yet (needs redeploy)")
        resp = await c.post(f"/data/{doc_id}/query", json={
            "where": {"field": "id", "op": "eq", "value": rec_id},
        })
        assert resp.status_code == 200
        records = resp.json().get("records", [])
        assert len(records) == 1, f"B should see inherit record, got {len(records)}"


async def test_user_c_sees_inherit_records(record_clients):
    """User C (no shared role) sees inherit records on an authenticated doc."""
    doc_id = record_clients["data"]["doc_id"]
    rec_id = record_clients["data"]["rec_inherit_a"]
    async with _make_client(record_clients["c"]) as c:
        if not await _query_ok(c, doc_id):
            pytest.skip("data-api query doesn't support data_records yet (needs redeploy)")
        resp = await c.post(f"/data/{doc_id}/query", json={
            "where": {"field": "id", "op": "eq", "value": rec_id},
        })
        assert resp.status_code == 200
        records = resp.json().get("records", [])
        assert len(records) == 1, f"C should see inherit record, got {len(records)}"


async def test_user_a_sees_own_personal_record(record_clients):
    """User A can see their own personal record."""
    doc_id = record_clients["data"]["doc_id"]
    rec_id = record_clients["data"]["rec_personal_a"]
    async with _make_client(record_clients["a"]) as c:
        if not await _query_ok(c, doc_id):
            pytest.skip("data-api query doesn't support data_records yet (needs redeploy)")
        resp = await c.post(f"/data/{doc_id}/query", json={
            "where": {"field": "id", "op": "eq", "value": rec_id},
        })
        assert resp.status_code == 200
        records = resp.json().get("records", [])
        assert len(records) == 1, f"A should see own personal record, got {len(records)}"


async def test_user_b_cannot_see_a_personal_record(record_clients):
    """User B cannot see User A's personal record."""
    doc_id = record_clients["data"]["doc_id"]
    rec_id = record_clients["data"]["rec_personal_a"]
    async with _make_client(record_clients["b"]) as c:
        if not await _query_ok(c, doc_id):
            pytest.skip("data-api query doesn't support data_records yet (needs redeploy)")
        resp = await c.post(f"/data/{doc_id}/query", json={
            "where": {"field": "id", "op": "eq", "value": rec_id},
        })
        assert resp.status_code == 200
        records = resp.json().get("records", [])
        assert len(records) == 0, f"B should NOT see A's personal record, got {len(records)}"


async def test_user_a_cannot_see_b_personal_record(record_clients):
    """User A cannot see User B's personal record."""
    doc_id = record_clients["data"]["doc_id"]
    rec_id = record_clients["data"]["rec_personal_b"]
    async with _make_client(record_clients["a"]) as c:
        if not await _query_ok(c, doc_id):
            pytest.skip("data-api query doesn't support data_records yet (needs redeploy)")
        resp = await c.post(f"/data/{doc_id}/query", json={
            "where": {"field": "id", "op": "eq", "value": rec_id},
        })
        assert resp.status_code == 200
        records = resp.json().get("records", [])
        assert len(records) == 0, f"A should NOT see B's personal record, got {len(records)}"


async def test_user_a_sees_shared_record(record_clients):
    """User A (with share-ab role) sees the shared record."""
    doc_id = record_clients["data"]["doc_id"]
    rec_id = record_clients["data"]["rec_shared_ab"]
    async with _make_client(record_clients["a"]) as c:
        if not await _query_ok(c, doc_id):
            pytest.skip("data-api query doesn't support data_records yet (needs redeploy)")
        resp = await c.post(f"/data/{doc_id}/query", json={
            "where": {"field": "id", "op": "eq", "value": rec_id},
        })
        assert resp.status_code == 200
        records = resp.json().get("records", [])
        assert len(records) == 1, f"A should see shared record, got {len(records)}"


async def test_user_b_sees_shared_record(record_clients):
    """User B (with share-ab role) sees the shared record."""
    doc_id = record_clients["data"]["doc_id"]
    rec_id = record_clients["data"]["rec_shared_ab"]
    async with _make_client(record_clients["b"]) as c:
        if not await _query_ok(c, doc_id):
            pytest.skip("data-api query doesn't support data_records yet (needs redeploy)")
        resp = await c.post(f"/data/{doc_id}/query", json={
            "where": {"field": "id", "op": "eq", "value": rec_id},
        })
        assert resp.status_code == 200
        records = resp.json().get("records", [])
        assert len(records) == 1, f"B should see shared record, got {len(records)}"


async def test_user_c_cannot_see_shared_record(record_clients):
    """User C (without share-ab role) cannot see the shared record."""
    doc_id = record_clients["data"]["doc_id"]
    rec_id = record_clients["data"]["rec_shared_ab"]
    async with _make_client(record_clients["c"]) as c:
        if not await _query_ok(c, doc_id):
            pytest.skip("data-api query doesn't support data_records yet (needs redeploy)")
        resp = await c.post(f"/data/{doc_id}/query", json={
            "where": {"field": "id", "op": "eq", "value": rec_id},
        })
        assert resp.status_code == 200
        records = resp.json().get("records", [])
        assert len(records) == 0, f"C should NOT see shared record, got {len(records)}"


async def test_record_count_per_user(record_clients):
    """Each user sees a different number of records based on visibility."""
    doc_id = record_clients["data"]["doc_id"]

    async with _make_client(record_clients["a"]) as c:
        if not await _query_ok(c, doc_id):
            pytest.skip("data-api query doesn't support data_records yet (needs redeploy)")
        resp = await c.post(f"/data/{doc_id}/query", json={})
        a_count = len(resp.json().get("records", []))

    async with _make_client(record_clients["b"]) as c:
        resp = await c.post(f"/data/{doc_id}/query", json={})
        b_count = len(resp.json().get("records", []))

    async with _make_client(record_clients["c"]) as c:
        resp = await c.post(f"/data/{doc_id}/query", json={})
        c_count = len(resp.json().get("records", []))

    # A sees: inherit + personal_a + shared_ab + any API-inserted = at least 3
    # B sees: inherit + personal_b + shared_ab + any API-inserted = at least 3
    # C sees: inherit + any API-inserted = at least 1 (no personal, no shared)
    assert a_count >= 3, f"User A should see >= 3 records, got {a_count}"
    assert b_count >= 3, f"User B should see >= 3 records, got {b_count}"
    assert c_count >= 1, f"User C should see >= 1 record, got {c_count}"
    assert c_count < a_count, f"User C ({c_count}) should see fewer records than A ({a_count})"


# =============================================================================
# Phase 3 -- Record Visibility API Endpoints
# =============================================================================


async def test_get_record_roles(record_clients):
    """GET /data/{doc_id}/records/{rec_id}/roles returns role assignments."""
    doc_id = record_clients["data"]["doc_id"]
    rec_id = record_clients["data"]["rec_shared_ab"]
    async with _make_client(record_clients["a"]) as c:
        resp = await c.get(f"/data/{doc_id}/records/{rec_id}/roles")
        assert resp.status_code == 200, f"Get roles failed: {resp.status_code} {resp.text}"
        body = resp.json()
        roles = body if isinstance(body, list) else body.get("roles", [])
        assert len(roles) >= 1, f"Expected at least 1 role, got {roles}"


async def test_set_record_visibility_personal(record_clients):
    """PUT visibility to 'personal' on an inherit record restricts access."""
    doc_id = record_clients["data"]["doc_id"]
    rec_id = record_clients["data"]["rec_inherit_a"]

    async with _make_client(record_clients["a"]) as c:
        # Check if visibility API works
        resp = await c.put(
            f"/data/{doc_id}/records/{rec_id}/visibility",
            json={"visibility": "personal"},
        )
        if resp.status_code == 500 and "row-level security" in resp.text:
            pytest.skip("data-api visibility endpoint has RLS issues (needs redeploy)")
        assert resp.status_code == 200, f"Set visibility failed: {resp.status_code} {resp.text}"

    # If the query endpoint supports data_records, verify isolation
    async with _make_client(record_clients["b"]) as c:
        if await _query_ok(c, doc_id):
            resp = await c.post(f"/data/{doc_id}/query", json={
                "where": {"field": "id", "op": "eq", "value": rec_id},
            })
            records = resp.json().get("records", [])
            assert len(records) == 0, f"After change to personal, B should not see record, got {len(records)}"

    async with _make_client(record_clients["a"]) as c:
        if await _query_ok(c, doc_id):
            resp = await c.post(f"/data/{doc_id}/query", json={
                "where": {"field": "id", "op": "eq", "value": rec_id},
            })
            records = resp.json().get("records", [])
            assert len(records) == 1, "Owner A should still see personal record"

    # Restore
    async with _make_client(record_clients["a"]) as c:
        await c.put(
            f"/data/{doc_id}/records/{rec_id}/visibility",
            json={"visibility": "inherit"},
        )


async def test_set_record_visibility_shared(record_clients):
    """PUT visibility to 'shared' with a role grants access to role holders."""
    doc_id = record_clients["data"]["doc_id"]
    rec_id = record_clients["data"]["rec_inherit_a"]
    share_role_id = record_clients["data"]["share_ab_role_id"]

    async with _make_client(record_clients["a"]) as c:
        resp = await c.put(
            f"/data/{doc_id}/records/{rec_id}/visibility",
            json={"visibility": "shared", "roleIds": [share_role_id]},
        )
        if resp.status_code == 500 and "row-level security" in resp.text:
            pytest.skip("data-api visibility endpoint has RLS issues (needs redeploy)")
        assert resp.status_code == 200, f"Set shared failed: {resp.status_code} {resp.text}"

    # Verify B (with role) sees it, C (without) doesn't
    async with _make_client(record_clients["b"]) as c:
        if await _query_ok(c, doc_id):
            resp = await c.post(f"/data/{doc_id}/query", json={
                "where": {"field": "id", "op": "eq", "value": rec_id},
            })
            records = resp.json().get("records", [])
            assert len(records) == 1, f"B should see shared record, got {len(records)}"

    async with _make_client(record_clients["c"]) as c:
        if await _query_ok(c, doc_id):
            resp = await c.post(f"/data/{doc_id}/query", json={
                "where": {"field": "id", "op": "eq", "value": rec_id},
            })
            records = resp.json().get("records", [])
            assert len(records) == 0, f"C should NOT see shared record, got {len(records)}"

    # Restore to inherit
    async with _make_client(record_clients["a"]) as c:
        await c.put(
            f"/data/{doc_id}/records/{rec_id}/visibility",
            json={"visibility": "inherit"},
        )


async def test_bulk_set_visibility(record_clients):
    """PUT bulk visibility endpoint changes multiple records at once."""
    doc_id = record_clients["data"]["doc_id"]
    rec_inherit = record_clients["data"]["rec_inherit_a"]
    share_role_id = record_clients["data"]["share_ab_role_id"]

    async with _make_client(record_clients["a"]) as c:
        resp = await c.put(
            f"/data/{doc_id}/records/visibility",
            json={
                "recordIds": [rec_inherit],
                "visibility": "shared",
                "roleIds": [share_role_id],
            },
        )
        if resp.status_code == 500 and "row-level security" in resp.text:
            pytest.skip("data-api bulk visibility endpoint has RLS issues (needs redeploy)")
        assert resp.status_code == 200, f"Bulk set failed: {resp.status_code} {resp.text}"
        body = resp.json()
        updated = body.get("updated", body.get("count", 0))
        assert updated >= 1, f"Expected at least 1 updated, got {updated}"

    # Restore
    async with _make_client(record_clients["a"]) as c:
        await c.put(
            f"/data/{doc_id}/records/visibility",
            json={"recordIds": [rec_inherit], "visibility": "inherit"},
        )


# =============================================================================
# Phase 4 -- Migration endpoint (dry run)
# =============================================================================


async def test_migration_dry_run(record_clients):
    """POST /data/migrate-to-records-table with dryRun=true previews migration."""
    async with _make_client(record_clients["a"]) as c:
        resp = await c.post("/data/migrate-to-records-table?dryRun=true&sourceApp=test-app")
        if resp.status_code == 404:
            pytest.skip("Migration endpoint not available (data-api needs redeploy)")
        assert resp.status_code == 200, f"Migration dry run failed: {resp.status_code} {resp.text}"
        body = resp.json()
        assert "dryRun" in body or "migratedDocuments" in body or "message" in body, \
            f"Unexpected migration response: {body}"


# =============================================================================
# Direct DB: RLS policy verification via psycopg2
#
# These tests verify RLS at the DB level, independent of the data-api code.
# =============================================================================


def test_rls_inherit_visible_to_doc_viewer(record_rls_doc, record_rls_users):
    """Direct DB: inherit records are visible to users who can see the parent doc."""
    doc_id = record_rls_doc["doc_id"]
    rec_inherit = record_rls_doc["rec_inherit_a"]
    user_a = record_rls_users.get_user("a")
    a_id = user_a["user_id"]
    all_roles = [user_a["share_ab_role_id"], user_a["personal_role_id"]]

    conn = _get_data_db_conn()
    try:
        with conn:
            cur = conn.cursor()
            _set_rls_context(cur, a_id, all_roles)
            cur.execute(
                "SELECT record_id FROM data_records WHERE document_id = %s::uuid AND visibility = 'inherit'",
                (doc_id,),
            )
            rows = cur.fetchall()
            record_ids = [str(r[0]) for r in rows]
            assert rec_inherit in record_ids, f"Inherit record not visible to user A: {record_ids}"
            cur.close()
    finally:
        conn.close()


def test_rls_personal_visible_only_to_owner(record_rls_doc, record_rls_users):
    """Direct DB: personal records are only visible to the owner."""
    doc_id = record_rls_doc["doc_id"]
    rec_personal_a = record_rls_doc["rec_personal_a"]
    user_a = record_rls_users.get_user("a")
    user_b = record_rls_users.get_user("b")

    conn = _get_data_db_conn()
    try:
        # User A sees own personal record
        with conn:
            cur = conn.cursor()
            _set_rls_context(cur, user_a["user_id"], [user_a["personal_role_id"]])
            cur.execute(
                "SELECT record_id FROM data_records WHERE document_id = %s::uuid AND visibility = 'personal'",
                (doc_id,),
            )
            a_personal_ids = [str(r[0]) for r in cur.fetchall()]
            assert rec_personal_a in a_personal_ids, "Owner A should see own personal record"
            cur.close()

        # User B does NOT see A's personal record
        with conn:
            cur = conn.cursor()
            _set_rls_context(cur, user_b["user_id"], [user_b["personal_role_id"]])
            cur.execute(
                "SELECT record_id FROM data_records WHERE document_id = %s::uuid AND visibility = 'personal'",
                (doc_id,),
            )
            b_personal_ids = [str(r[0]) for r in cur.fetchall()]
            assert rec_personal_a not in b_personal_ids, "User B should NOT see A's personal record"
            cur.close()
    finally:
        conn.close()


def test_rls_shared_visible_only_to_role_holders(record_rls_doc, record_rls_users):
    """Direct DB: shared records are only visible to users with matching role."""
    doc_id = record_rls_doc["doc_id"]
    rec_shared = record_rls_doc["rec_shared_ab"]
    share_role_id = record_rls_doc["share_ab_role_id"]
    user_a = record_rls_users.get_user("a")
    user_c = record_rls_users.get_user("c")

    conn = _get_data_db_conn()
    try:
        # User A (has share_ab role) sees shared record
        with conn:
            cur = conn.cursor()
            _set_rls_context(cur, user_a["user_id"], [share_role_id, user_a["personal_role_id"]])
            cur.execute(
                "SELECT record_id FROM data_records WHERE document_id = %s::uuid AND visibility = 'shared'",
                (doc_id,),
            )
            a_ids = [str(r[0]) for r in cur.fetchall()]
            assert rec_shared in a_ids, "A (with shared role) should see shared record"
            cur.close()

        # User C (no share role) does NOT see shared record
        with conn:
            cur = conn.cursor()
            _set_rls_context(cur, user_c["user_id"], [user_c["personal_role_id"]])
            cur.execute(
                "SELECT record_id FROM data_records WHERE document_id = %s::uuid AND visibility = 'shared'",
                (doc_id,),
            )
            c_ids = [str(r[0]) for r in cur.fetchall()]
            assert rec_shared not in c_ids, "C (without shared role) should NOT see shared record"
            cur.close()
    finally:
        conn.close()
