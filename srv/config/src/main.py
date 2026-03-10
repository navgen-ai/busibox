"""
Config API — Busibox Configuration Service.

Centralised configuration management with tiered access control:
  - Public: branding, feature flags (no auth)
  - Authenticated: app registry, non-secret settings (any JWT)
  - App-scoped: app-specific secrets (JWT + app role binding)
  - Admin: full CRUD on all config and app registry (Admin role)
"""

import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI

from busibox_common import AsyncPGPoolManager

from config import config
from schema import get_config_schema
from services import config_store, app_registry
from routes import public, authenticated, app_scoped, admin


@asynccontextmanager
async def lifespan(_app: FastAPI):
    pool_mgr = AsyncPGPoolManager.from_config(config.to_pool_config())
    await pool_mgr.connect()

    async with pool_mgr.acquire() as conn:
        schema = get_config_schema()
        await schema.apply(conn)
    print("[CONFIG-API] Database schema applied")

    config_store.set_pool(pool_mgr)
    app_registry.set_pool(pool_mgr)

    yield

    await pool_mgr.disconnect()
    print("[CONFIG-API] Shutdown complete")


app = FastAPI(title="Config API", version="1.0.0", lifespan=lifespan)


@app.get("/health/live")
async def live():
    return {"status": "ok"}


@app.get("/health/ready")
async def ready():
    return {"status": "ok"}


app.include_router(public.router)
app.include_router(authenticated.router)
app.include_router(app_scoped.router)
app.include_router(admin.router)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=config.port, reload=False)
