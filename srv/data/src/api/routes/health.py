"""
Health check endpoint.

Checks connectivity to all dependencies:
- PostgreSQL
- MinIO
- Redis
- Milvus
- Neo4j
- liteLLM

Returns status: healthy (all up), degraded (some down), unhealthy (critical down)
"""

import hashlib
import os
import time
from typing import Dict

import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from api.services.minio_service import MinIOService
from api.services.redis_service import RedisService
from shared.config import Config

logger = structlog.get_logger()

router = APIRouter()


async def check_postgres() -> Dict[str, any]:
    """Check PostgreSQL connectivity."""
    try:
        import asyncpg
        config = Config().to_dict()
        
        start = time.time()
        conn = await asyncpg.connect(
            host=config.get("postgres_host", "postgres"),
            port=config.get("postgres_port", 5432),
            database=config.get("postgres_db", "busibox"),
            user=config.get("postgres_user", "postgres"),
            password=config.get("postgres_password", ""),
            timeout=2,
        )
        await conn.close()
        response_time = round((time.time() - start) * 1000, 2)
        
        return {
            "status": "healthy",
            "response_time_ms": response_time,
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
        }


async def check_minio() -> Dict[str, any]:
    """Check MinIO connectivity."""
    try:
        config = Config().to_dict()
        minio_service = MinIOService(config)
        
        start = time.time()
        await minio_service.check_health()
        response_time = round((time.time() - start) * 1000, 2)
        
        return {
            "status": "healthy",
            "response_time_ms": response_time,
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
        }


async def check_redis() -> Dict[str, any]:
    """Check Redis connectivity."""
    try:
        config = Config().to_dict()
        redis_service = RedisService(config)
        
        start = time.time()
        await redis_service.check_health()
        response_time = round((time.time() - start) * 1000, 2)
        
        return {
            "status": "healthy",
            "response_time_ms": response_time,
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
        }


async def check_milvus() -> Dict[str, any]:
    """Check Milvus connectivity with timeout protection."""
    import asyncio
    
    def _check_milvus_sync():
        """Synchronous Milvus check to be run in executor."""
        from pymilvus import connections, utility
        
        config = Config().to_dict()
        
        start = time.time()
        try:
            connections.connect(
                "health_check",
                host=config.get("milvus_host", "milvus"),
                port=config.get("milvus_port", "19530"),
                timeout=2,
            )
            
            collections = utility.list_collections(using="health_check")
            connections.disconnect("health_check")
            
            response_time = round((time.time() - start) * 1000, 2)
            
            return {
                "status": "healthy",
                "response_time_ms": response_time,
                "collections": len(collections),
            }
        except Exception as e:
            try:
                connections.disconnect("health_check")
            except:
                pass
            raise e
    
    try:
        loop = asyncio.get_event_loop()
        # Run with a 3-second timeout
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _check_milvus_sync),
            timeout=3.0
        )
        return result
    except asyncio.TimeoutError:
        return {
            "status": "unhealthy",
            "error": "Milvus check timed out after 3 seconds",
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
        }


async def check_litellm() -> Dict[str, any]:
    """Check liteLLM connectivity."""
    try:
        import httpx
        
        config = Config().to_dict()
        litellm_url = config.get("litellm_base_url", "http://10.96.200.207:4000")
        
        start = time.time()
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{litellm_url}/health/liveliness")
            response.raise_for_status()
        
        response_time = round((time.time() - start) * 1000, 2)
        
        return {
            "status": "healthy",
            "response_time_ms": response_time,
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
        }


async def check_neo4j(request: Request | None = None) -> Dict[str, any]:
    """Check Neo4j connectivity via graph service singleton."""
    try:
        graph_service = None
        if request:
            graph_service = getattr(request.app.state, "graph_service", None)

        if not graph_service:
            uri = os.getenv("NEO4J_URI", "")
            if not uri:
                return {"status": "not_configured", "reason": "NEO4J_URI not set"}
            return {"status": "unavailable", "reason": "graph service not initialized"}

        if not graph_service.available:
            return {"status": "unavailable", "reason": "connection failed at startup"}

        start = time.time()
        await graph_service._driver.verify_connectivity()
        response_time = round((time.time() - start) * 1000, 2)
        return {
            "status": "healthy",
            "response_time_ms": response_time,
        }
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


@router.get("")
async def health_check(request: Request):
    """
    Health check endpoint.
    
    Returns status of all dependencies with response times.
    Overall status: healthy (all up), degraded (some down), unhealthy (critical down)
    """
    checks = {
        "postgres": await check_postgres(),
        "minio": await check_minio(),
        "redis": await check_redis(),
        "milvus": await check_milvus(),
        "neo4j": await check_neo4j(request),
        "litellm": await check_litellm(),
    }
    
    # Determine overall status
    healthy_count = sum(1 for c in checks.values() if c["status"] == "healthy")
    total_count = len(checks)
    
    # Critical services: postgres, minio, redis
    critical_services = ["postgres", "minio", "redis"]
    critical_down = any(
        checks[svc]["status"] != "healthy" for svc in critical_services
    )
    
    if critical_down:
        overall_status = "unhealthy"
        http_status = status.HTTP_503_SERVICE_UNAVAILABLE
    elif healthy_count == total_count:
        overall_status = "healthy"
        http_status = status.HTTP_200_OK
    else:
        overall_status = "degraded"
        http_status = status.HTTP_200_OK
    
    response_data = {
        "status": overall_status,
        "checks": checks,
        "healthy": f"{healthy_count}/{total_count}",
    }
    
    return JSONResponse(
        status_code=http_status,
        content=response_data,
    )


def _secret_fingerprint(value: str | None) -> str:
    """SHA256 prefix of a secret value for comparison without exposing it."""
    if not value:
        return "empty"
    return hashlib.sha256(value.encode()).hexdigest()[:12]


@router.get("/secrets")
async def secrets_check():
    """
    Reports the status of deployed secrets.

    Returns for each secret:
    - whether it is set
    - whether it uses an insecure/default value
    - a SHA256 fingerprint (first 12 hex chars) for comparing against vault
    - the configured host/endpoint (non-secret) for infrastructure vars

    Does NOT return actual secret values.
    """
    try:
        from busibox_common.secrets import is_insecure_value
    except ImportError:
        is_insecure_value = lambda v: not v  # noqa: E731

    secret_vars = {
        "POSTGRES_PASSWORD": os.getenv("POSTGRES_PASSWORD"),
        "MINIO_ACCESS_KEY": os.getenv("MINIO_ACCESS_KEY") or os.getenv("MINIO_USER"),
        "MINIO_SECRET_KEY": os.getenv("MINIO_SECRET_KEY") or os.getenv("MINIO_PASS"),
        "NEO4J_PASSWORD": os.getenv("NEO4J_PASSWORD"),
        "LITELLM_API_KEY": os.getenv("LITELLM_API_KEY"),
    }

    infra_vars = {
        "POSTGRES_HOST": os.getenv("POSTGRES_HOST", "not set"),
        "MINIO_ENDPOINT": os.getenv("MINIO_ENDPOINT", "not set"),
        "MILVUS_HOST": os.getenv("MILVUS_HOST", "not set"),
        "NEO4J_URI": os.getenv("NEO4J_URI", "not set"),
        "LITELLM_BASE_URL": os.getenv("LITELLM_BASE_URL", "not set"),
        "REDIS_HOST": os.getenv("REDIS_HOST", "not set"),
        "AUTHZ_BASE_URL": os.getenv("AUTHZ_BASE_URL", "not set"),
        "EMBEDDING_API_URL": os.getenv("EMBEDDING_API_URL", "not set"),
    }

    secrets_status = {}
    insecure_count = 0
    for name, value in secret_vars.items():
        is_set = bool(value)
        insecure = is_insecure_value(value)
        if insecure:
            insecure_count += 1
        secrets_status[name] = {
            "set": is_set,
            "insecure": insecure,
            "fingerprint": _secret_fingerprint(value) if is_set else None,
        }

    return JSONResponse(content={
        "secrets": secrets_status,
        "infrastructure": infra_vars,
        "insecure_count": insecure_count,
        "total_secrets": len(secret_vars),
    })

