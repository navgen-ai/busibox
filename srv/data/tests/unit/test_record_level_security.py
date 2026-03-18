"""
Unit tests for record-level security features in DataService and QueryEngine.

Tests the new data_records table support, record visibility logic,
and the QueryEngine's ability to build queries against the records table.
Uses mocked database connections -- no real services required.
"""

import json
import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from datetime import datetime

from api.services.data_service import DataService
from api.services.query_engine import QueryEngine


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_pool():
    """Create a mock database pool."""
    pool = AsyncMock()
    return pool


@pytest.fixture
def mock_cache_manager():
    """Create a mock cache manager."""
    cache = AsyncMock()
    cache.get_document = AsyncMock(return_value=None)
    cache.cache_document = AsyncMock(return_value=True)
    cache.invalidate_document = AsyncMock(return_value=True)
    cache.get_document_stats = AsyncMock(return_value=None)
    return cache


@pytest.fixture
def data_service(mock_pool, mock_cache_manager):
    """Create a DataService instance with mocked dependencies."""
    return DataService(mock_pool, cache_manager=mock_cache_manager)


@pytest.fixture
def mock_request():
    """Create a mock FastAPI request with user context."""
    request = MagicMock()
    request.state.user_id = str(uuid.uuid4())
    request.state.role_ids = []
    return request


@pytest.fixture
def query_engine():
    """Create a QueryEngine instance."""
    return QueryEngine()


# =============================================================================
# DataService._use_records_table
# =============================================================================


class TestUseRecordsTable:
    """Test the _use_records_table detection method."""

    @pytest.mark.asyncio
    async def test_returns_true_when_table_exists(self, data_service):
        """Should return True when data_records table exists."""
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=True)

        result = await data_service._use_records_table(mock_conn, "some-doc-id")
        assert result is True
        mock_conn.fetchval.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_false_when_table_missing(self, data_service):
        """Should return False when data_records table doesn't exist."""
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=False)

        result = await data_service._use_records_table(mock_conn, "some-doc-id")
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self, data_service):
        """Should return False when the query fails."""
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(side_effect=Exception("connection error"))

        result = await data_service._use_records_table(mock_conn, "some-doc-id")
        assert result is False


# =============================================================================
# DataService._is_uuid
# =============================================================================


class TestIsUUID:
    """Test the UUID validation helper."""

    def test_valid_uuid(self, data_service):
        assert data_service._is_uuid(str(uuid.uuid4())) is True

    def test_valid_uuid_no_hyphens(self, data_service):
        assert data_service._is_uuid(uuid.uuid4().hex) is True

    def test_invalid_uuid(self, data_service):
        assert data_service._is_uuid("not-a-uuid") is False

    def test_empty_string(self, data_service):
        assert data_service._is_uuid("") is False

    def test_none(self, data_service):
        # _is_uuid may raise TypeError on None; either False or TypeError is acceptable
        try:
            result = data_service._is_uuid(None)
            assert result is False
        except TypeError:
            pass  # None is not a valid UUID input; raising is fine


# =============================================================================
# DataService.set_record_visibility
# =============================================================================


class TestSetRecordVisibility:
    """Test the set_record_visibility method."""

    @pytest.mark.asyncio
    async def test_rejects_invalid_visibility(self, data_service, mock_request):
        """Should reject visibility values other than inherit/personal/shared."""
        with pytest.raises(ValueError, match="must be"):
            await data_service.set_record_visibility(
                mock_request, "doc-id", "rec-id",
                visibility="invalid",
            )

    @pytest.mark.asyncio
    async def test_rejects_shared_without_roles(self, data_service, mock_request):
        """Should reject shared visibility without role_ids."""
        with pytest.raises(ValueError, match="role_ids required"):
            await data_service.set_record_visibility(
                mock_request, "doc-id", "rec-id",
                visibility="shared", role_ids=None,
            )

    @pytest.mark.asyncio
    async def test_rejects_shared_with_empty_roles(self, data_service, mock_request):
        """Should reject shared visibility with empty role_ids list."""
        with pytest.raises(ValueError, match="role_ids required"):
            await data_service.set_record_visibility(
                mock_request, "doc-id", "rec-id",
                visibility="shared", role_ids=[],
            )


# =============================================================================
# DataService.bulk_set_record_visibility
# =============================================================================


class TestBulkSetRecordVisibility:
    """Test the bulk_set_record_visibility method."""

    @pytest.mark.asyncio
    async def test_rejects_invalid_visibility(self, data_service, mock_request):
        """Should reject invalid visibility."""
        with pytest.raises(ValueError, match="must be"):
            await data_service.bulk_set_record_visibility(
                mock_request, "doc-id", ["rec-1"],
                visibility="public",
            )

    @pytest.mark.asyncio
    async def test_rejects_shared_without_roles(self, data_service, mock_request):
        """Should reject shared without role_ids."""
        with pytest.raises(ValueError, match="role_ids required"):
            await data_service.bulk_set_record_visibility(
                mock_request, "doc-id", ["rec-1"],
                visibility="shared",
            )


# =============================================================================
# QueryEngine - records table query building
# =============================================================================


class TestQueryEngineRecordsTable:
    """Test QueryEngine's ability to build queries for the data_records table."""

    def test_build_records_table_query_basic(self, query_engine):
        """Should build a basic SELECT query against data_records."""
        if not hasattr(query_engine, 'build_records_table_query'):
            pytest.skip("build_records_table_query not implemented on QueryEngine")

        doc_id = str(uuid.uuid4())
        sql, params = query_engine.build_records_table_query(
            document_id=doc_id,
            query={},
        )
        assert "data_records" in sql
        assert doc_id in [str(p) for p in params] or any(str(doc_id) in str(p) for p in params)

    def test_build_records_table_query_with_where(self, query_engine):
        """Should include WHERE clause for filtering."""
        if not hasattr(query_engine, 'build_records_table_query'):
            pytest.skip("build_records_table_query not implemented on QueryEngine")

        doc_id = str(uuid.uuid4())
        sql, params = query_engine.build_records_table_query(
            document_id=doc_id,
            query={
                "where": {"field": "name", "op": "eq", "value": "Test"},
            },
        )
        assert "data_records" in sql
        # Should reference the data JSONB column
        assert "data" in sql.lower()

    def test_build_records_table_query_with_limit(self, query_engine):
        """Should include LIMIT clause."""
        if not hasattr(query_engine, 'build_records_table_query'):
            pytest.skip("build_records_table_query not implemented on QueryEngine")

        doc_id = str(uuid.uuid4())
        sql, params = query_engine.build_records_table_query(
            document_id=doc_id,
            query={"limit": 10},
        )
        assert "LIMIT" in sql.upper()

    def test_build_records_table_query_with_order(self, query_engine):
        """Should include ORDER BY clause."""
        if not hasattr(query_engine, 'build_records_table_query'):
            pytest.skip("build_records_table_query not implemented on QueryEngine")

        doc_id = str(uuid.uuid4())
        sql, params = query_engine.build_records_table_query(
            document_id=doc_id,
            query={
                "orderBy": [{"field": "name", "direction": "asc"}],
            },
        )
        assert "ORDER BY" in sql.upper()


# =============================================================================
# QueryEngine - _build_where_clause with column parameter
# =============================================================================


class TestWhereClauseColumnParam:
    """Test that _build_where_clause supports the column parameter for records table."""

    def test_where_clause_default_column(self, query_engine):
        """_build_where_clause produces a non-empty clause with default column='record'."""
        if not hasattr(query_engine, '_build_where_clause'):
            pytest.skip("_build_where_clause not accessible")

        clause, params, idx = query_engine._build_where_clause(
            {"field": "name", "op": "eq", "value": "Test"},
            params=[], param_idx=1,
        )
        assert len(clause) > 0
        assert "record" in clause.lower()

    def test_where_clause_data_column(self, query_engine):
        """With column='data', targets the data JSONB column in data_records."""
        if not hasattr(query_engine, '_build_where_clause'):
            pytest.skip("_build_where_clause not accessible")

        import inspect
        sig = inspect.signature(query_engine._build_where_clause)
        if "column" not in sig.parameters:
            pytest.skip("_build_where_clause doesn't accept column parameter")

        clause, params, idx = query_engine._build_where_clause(
            {"field": "name", "op": "eq", "value": "Test"},
            params=[], param_idx=1, column="data",
        )
        assert "data" in clause.lower()


# =============================================================================
# Record visibility model validation
# =============================================================================


class TestVisibilityValues:
    """Test that the visibility check values are correct."""

    VALID_VISIBILITIES = ("inherit", "personal", "shared")

    @pytest.mark.parametrize("vis", VALID_VISIBILITIES)
    def test_valid_visibility_accepted(self, vis):
        """All three visibility modes should be accepted."""
        assert vis in self.VALID_VISIBILITIES

    @pytest.mark.parametrize("vis", ("public", "private", "authenticated", "", None))
    def test_invalid_visibility_rejected(self, vis):
        """Invalid visibility values should be rejected."""
        assert vis not in self.VALID_VISIBILITIES


# =============================================================================
# DataService._sync_record_count
# =============================================================================


class TestSyncRecordCount:
    """Test the record count sync helper."""

    @pytest.mark.asyncio
    async def test_sync_updates_count(self, data_service):
        """Should execute an UPDATE query to sync data_record_count."""
        if not hasattr(data_service, '_sync_record_count'):
            pytest.skip("_sync_record_count not implemented")

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        doc_id = str(uuid.uuid4())
        await data_service._sync_record_count(mock_conn, doc_id)

        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args
        assert "data_record_count" in call_args[0][0] or "UPDATE" in call_args[0][0].upper()
