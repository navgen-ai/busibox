"""
Authenticated routes — require a valid JWT (any user).

Serves app registry and tier=authenticated config entries.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from auth import require_authenticated
from services import app_registry, config_store

router = APIRouter(tags=["authenticated"])


@router.get("/config/apps")
async def list_apps(
    include_inactive: bool = Query(False),
    type: Optional[str] = Query(None, alias="type"),
    _user: dict = Depends(require_authenticated),
):
    """List registered applications (active by default)."""
    apps = await app_registry.list_apps(active_only=not include_inactive, app_type=type)
    return {"apps": apps}


@router.get("/config/apps/{app_id}")
async def get_app(app_id: str, _user: dict = Depends(require_authenticated)):
    """Get a single app's registration info."""
    app = await app_registry.get_app(app_id)
    if not app:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"App not found: {app_id}")
    return {"app": app}


@router.get("/config/authenticated")
async def get_authenticated_config(_user: dict = Depends(require_authenticated)):
    """Return all tier=authenticated config entries (non-secret settings)."""
    entries = await config_store.list_entries(tier="authenticated")
    result = {}
    for entry in entries:
        if not entry["encrypted"]:
            result[entry["key"]] = entry["value"]
    return {"config": result}
