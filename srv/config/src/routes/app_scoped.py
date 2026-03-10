"""
App-scoped routes — require JWT with access to the specific app.

Serves app-specific configuration including secrets (API keys, etc.).
"""

from fastapi import APIRouter, Depends, HTTPException

from auth import require_app_access
from config import config as svc_config
from services import config_store
from services.encryption import decrypt_value

router = APIRouter(tags=["app-scoped"])


@router.get("/config/app/{app_id}")
async def get_app_config(
    app_id: str,
    user: dict = Depends(require_app_access()),
):
    """Return all config entries scoped to this app (values masked if encrypted)."""
    entries = await config_store.list_entries(app_id=app_id, tier="app")
    result = {}
    for entry in entries:
        if entry["encrypted"]:
            result[entry["key"]] = "********"
        else:
            result[entry["key"]] = entry["value"]
    return {"config": result, "app_id": app_id}


@router.get("/config/app/{app_id}/{key}")
async def get_app_config_key(
    app_id: str,
    key: str,
    user: dict = Depends(require_app_access()),
):
    """Get a specific app config value (masked if encrypted)."""
    entry = await config_store.get_entry(key, app_id=app_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Config key not found: {key}")
    if entry["tier"] != "app":
        raise HTTPException(status_code=403, detail="Not an app-scoped config key")

    value = "********" if entry["encrypted"] else entry["value"]
    return {"key": key, "value": value, "encrypted": entry["encrypted"]}


@router.get("/config/app/{app_id}/{key}/raw")
async def get_app_config_key_raw(
    app_id: str,
    key: str,
    user: dict = Depends(require_app_access()),
):
    """Get the raw (decrypted) value. Use only when the secret is needed at runtime."""
    entry = await config_store.get_entry(key, app_id=app_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Config key not found: {key}")
    if entry["tier"] != "app":
        raise HTTPException(status_code=403, detail="Not an app-scoped config key")

    value = entry["value"]
    if entry["encrypted"] and svc_config.encryption_key:
        value = decrypt_value(value, svc_config.encryption_key)

    return {"key": key, "value": value, "encrypted": entry["encrypted"]}
