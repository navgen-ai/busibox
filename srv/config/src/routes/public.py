"""
Public routes — no authentication required.

Serves branding, public feature flags, and any tier=public config entries.
"""

from fastapi import APIRouter

from services import config_store

router = APIRouter(tags=["public"])


@router.get("/config/branding")
async def get_branding():
    """
    Return portal branding configuration.

    This endpoint is intentionally unauthenticated so that login pages,
    public docs, and the SSO flow can display correct branding.
    """
    entries = await config_store.list_entries(scope="branding", tier="public")
    branding = {}
    for entry in entries:
        branding[entry["key"]] = entry["value"]
    return {"branding": branding}


@router.get("/config/public")
async def get_public_config():
    """Return all tier=public config entries (feature flags, etc.)."""
    entries = await config_store.list_entries(tier="public")
    result = {}
    for entry in entries:
        if not entry["encrypted"]:
            result[entry["key"]] = entry["value"]
    return {"config": result}
