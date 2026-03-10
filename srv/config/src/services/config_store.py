"""
Config Store — DB operations for config_entries table.
"""

from typing import List, Optional
import structlog

logger = structlog.get_logger()

# Module-level pool reference, set from main.py at startup
_pool = None


def set_pool(pool):
    global _pool
    _pool = pool


def _get_pool():
    if _pool is None:
        raise RuntimeError("Config store pool not initialised")
    return _pool


async def list_entries(
    *,
    tier: Optional[str] = None,
    scope: Optional[str] = None,
    app_id: Optional[str] = None,
    category: Optional[str] = None,
) -> List[dict]:
    """List config entries with optional filters."""
    pool = _get_pool()
    conditions = []
    params = []
    idx = 1

    if tier:
        conditions.append(f"tier = ${idx}")
        params.append(tier)
        idx += 1
    if scope:
        conditions.append(f"scope = ${idx}")
        params.append(scope)
        idx += 1
    if app_id is not None:
        conditions.append(f"app_id = ${idx}")
        params.append(app_id)
        idx += 1
    if category:
        conditions.append(f"category = ${idx}")
        params.append(category)
        idx += 1

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    sql = f"""
        SELECT id, key, value, encrypted, scope, app_id, tier, category, description,
               created_at, updated_at
        FROM config_entries
        {where}
        ORDER BY category NULLS LAST, key
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [dict(r) for r in rows]


async def get_entry(key: str, app_id: Optional[str] = None) -> Optional[dict]:
    """Get a single config entry by key (and optional app_id)."""
    pool = _get_pool()
    if app_id:
        sql = "SELECT * FROM config_entries WHERE key = $1 AND app_id = $2"
        args = (key, app_id)
    else:
        sql = "SELECT * FROM config_entries WHERE key = $1 AND app_id IS NULL"
        args = (key,)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
    return dict(row) if row else None


async def upsert_entry(
    key: str,
    value: str,
    *,
    encrypted: bool = False,
    scope: str = "platform",
    app_id: Optional[str] = None,
    tier: str = "admin",
    category: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    """Insert or update a config entry."""
    pool = _get_pool()
    sql = """
        INSERT INTO config_entries (key, value, encrypted, scope, app_id, tier, category, description)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (key, COALESCE(app_id, ''))
        DO UPDATE SET value = $2, encrypted = $3, scope = $4, tier = $6,
                      category = $7, description = $8, updated_at = NOW()
        RETURNING *
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            sql, key, value, encrypted, scope, app_id, tier, category, description
        )
    return dict(row)


async def delete_entry(key: str, app_id: Optional[str] = None) -> bool:
    """Delete a config entry. Returns True if a row was deleted."""
    pool = _get_pool()
    if app_id:
        sql = "DELETE FROM config_entries WHERE key = $1 AND app_id = $2"
        args = (key, app_id)
    else:
        sql = "DELETE FROM config_entries WHERE key = $1 AND app_id IS NULL"
        args = (key,)
    async with pool.acquire() as conn:
        result = await conn.execute(sql, *args)
    return result.endswith("1")


async def list_categories() -> List[dict]:
    """List all categories with key counts."""
    pool = _get_pool()
    sql = """
        SELECT category, array_agg(key) AS keys, count(*) AS count
        FROM config_entries
        WHERE category IS NOT NULL
        GROUP BY category
        ORDER BY category
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql)
    return [{"category": r["category"], "keys": list(r["keys"]), "count": r["count"]} for r in rows]


async def bulk_upsert(entries: List[dict]) -> int:
    """Bulk upsert config entries. Returns count of rows affected."""
    pool = _get_pool()
    count = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            for entry in entries:
                await conn.execute(
                    """
                    INSERT INTO config_entries (key, value, encrypted, scope, app_id, tier, category, description)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (key, COALESCE(app_id, ''))
                    DO UPDATE SET value = $2, encrypted = $3, scope = $4, tier = $6,
                                  category = $7, description = $8, updated_at = NOW()
                    """,
                    entry["key"],
                    entry.get("value", ""),
                    entry.get("encrypted", False),
                    entry.get("scope", "platform"),
                    entry.get("app_id"),
                    entry.get("tier", "admin"),
                    entry.get("category"),
                    entry.get("description"),
                )
                count += 1
    return count


async def export_all() -> List[dict]:
    """Export all config entries (including raw encrypted values). Admin backup."""
    pool = _get_pool()
    sql = "SELECT * FROM config_entries ORDER BY scope, category NULLS LAST, key"
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql)
    return [dict(r) for r in rows]
