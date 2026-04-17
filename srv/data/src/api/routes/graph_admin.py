"""
Graph Database Admin API routes.

Provides diagnostic and management endpoints for the Neo4j graph service,
used by the admin UI to:
- See connection config and status (URI, user, indexes, APOC, version)
- Run a step-by-step reachability check to identify connection problems
- Browse nodes by label / drill into neighbors
- Execute arbitrary Cypher (read by default, write gated)
- Compare per-user visibility vs global totals (permissions debugger)
- Reconnect the driver, rebuild indexes, purge orphan nodes

All endpoints require the ``data.admin`` scope (the ``Admin`` role's
wildcard ``*`` grants this).
"""

from typing import Any, Dict, List, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.middleware.jwt_auth import ScopeChecker
from services.graph_service import GraphService

logger = structlog.get_logger()

router = APIRouter()

require_data_admin = ScopeChecker("data.admin")


def _get_graph_service(request: Request) -> Optional[GraphService]:
    """Return the graph service singleton from app state (may be None)."""
    return getattr(request.app.state, "graph_service", None)


def _actor_id(request: Request) -> Optional[str]:
    return getattr(request.state, "user_id", None)


# =============================================================================
# Connection / Diagnostics
# =============================================================================


@router.get("/connection", dependencies=[Depends(require_data_admin)])
async def get_connection(request: Request) -> Dict[str, Any]:
    """Return driver/connection metadata (no password)."""
    gs = _get_graph_service(request)
    if gs is None:
        return {
            "available": False,
            "uri": None,
            "user": None,
            "password_set": False,
            "driver_installed": False,
            "last_connect_error": "graph_service singleton missing from app state",
        }
    return await gs.get_config()


@router.post("/reachability", dependencies=[Depends(require_data_admin)])
async def run_reachability(request: Request) -> Dict[str, Any]:
    """Run the 7-step reachability diagnostic."""
    gs = _get_graph_service(request)
    if gs is None:
        return {
            "ok": False,
            "steps": [{
                "step": "singleton",
                "ok": False,
                "message": "graph_service not initialized",
                "duration_ms": 0,
            }],
        }
    return await gs.reachability_check()


@router.post("/reconnect", dependencies=[Depends(require_data_admin)])
async def reconnect_driver(request: Request) -> Dict[str, Any]:
    """Close and re-open the Neo4j driver."""
    gs = _get_graph_service(request)
    if gs is None:
        raise HTTPException(status_code=503, detail="graph_service not initialized")
    actor = _actor_id(request)
    logger.info("[GRAPH ADMIN] reconnect requested", actor=actor)
    return await gs.reconnect()


@router.get("/errors", dependencies=[Depends(require_data_admin)])
async def get_recent_errors(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
) -> Dict[str, Any]:
    """Return the tail of the shared error ring buffer."""
    return {"errors": GraphService.recent_errors(limit=limit)}


@router.delete("/errors", dependencies=[Depends(require_data_admin)])
async def clear_recent_errors(request: Request) -> Dict[str, Any]:
    """Clear the shared error ring buffer."""
    actor = _actor_id(request)
    n = GraphService.clear_recent_errors()
    logger.info("[GRAPH ADMIN] error buffer cleared", actor=actor, removed=n)
    return {"removed": n}


# =============================================================================
# Stats / Browse
# =============================================================================


@router.get("/stats", dependencies=[Depends(require_data_admin)])
async def get_stats(request: Request) -> Dict[str, Any]:
    """Extended stats: labels, rel types, orphans, totals."""
    gs = _get_graph_service(request)
    if gs is None or not gs.available:
        return {
            "available": False,
            "labels": [],
            "relationship_types": [],
            "orphans": {"no_node_id": 0, "no_relationships": 0, "dangling_rels": 0},
            "total_nodes": 0,
            "total_relationships": 0,
        }
    labels = await gs.list_labels_with_counts()
    rel_types = await gs.list_rel_types_with_counts()
    orphans = await gs.find_orphans()
    total_nodes = sum(l.get("count", 0) for l in labels)
    total_rels = sum(r.get("count", 0) for r in rel_types)
    return {
        "available": True,
        "labels": labels,
        "relationship_types": rel_types,
        "orphans": orphans,
        "total_nodes": total_nodes,
        "total_relationships": total_rels,
    }


@router.get("/labels", dependencies=[Depends(require_data_admin)])
async def get_labels(request: Request) -> Dict[str, Any]:
    gs = _get_graph_service(request)
    if gs is None or not gs.available:
        return {"labels": []}
    return {"labels": await gs.list_labels_with_counts()}


@router.get("/rel-types", dependencies=[Depends(require_data_admin)])
async def get_rel_types(request: Request) -> Dict[str, Any]:
    gs = _get_graph_service(request)
    if gs is None or not gs.available:
        return {"relationship_types": []}
    return {"relationship_types": await gs.list_rel_types_with_counts()}


@router.get("/browse", dependencies=[Depends(require_data_admin)])
async def browse_nodes(
    request: Request,
    label: Optional[str] = Query(None, description="Filter by node label"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    search: Optional[str] = Query(None, description="Substring search on name/node_id"),
    owner_id: Optional[str] = Query(None, description="Filter to a specific owner"),
) -> Dict[str, Any]:
    """Paginated admin browser over graph nodes."""
    gs = _get_graph_service(request)
    if gs is None or not gs.available:
        return {"nodes": [], "total": 0, "limit": limit, "offset": offset}
    return await gs.browse_nodes(
        label=label,
        limit=limit,
        offset=offset,
        search=search,
        owner_id=owner_id,
    )


@router.get("/permissions", dependencies=[Depends(require_data_admin)])
async def permissions_breakdown(
    request: Request,
    user_id: Optional[str] = Query(
        None,
        description="User to check; defaults to the caller",
    ),
) -> Dict[str, Any]:
    """Compare what a user can see vs the global total."""
    gs = _get_graph_service(request)
    if gs is None or not gs.available:
        return {"total_nodes": 0, "visible_to_user": 0, "per_label": []}
    effective_user = user_id or _actor_id(request)
    return await gs.visibility_breakdown(effective_user)


# =============================================================================
# Cypher
# =============================================================================


class CypherRequest(BaseModel):
    query: str = Field(..., description="Cypher query to execute")
    params: Optional[Dict[str, Any]] = Field(default=None)
    allow_write: bool = Field(
        default=False,
        description="Must be true for writes. Audit-logged.",
    )
    timeout_sec: float = Field(default=30.0, ge=1.0, le=300.0)


@router.post("/cypher", dependencies=[Depends(require_data_admin)])
async def run_cypher(
    request: Request,
    body: CypherRequest,
) -> Dict[str, Any]:
    """
    Execute a Cypher query.

    Reads use a READ transaction with a default 30s timeout. Writes require
    ``allow_write: true`` and are logged with the caller's user_id.
    """
    gs = _get_graph_service(request)
    if gs is None:
        raise HTTPException(status_code=503, detail="graph_service not initialized")

    actor = _actor_id(request)
    if body.allow_write:
        logger.warning(
            "[GRAPH ADMIN] write Cypher executed",
            actor=actor,
            query_preview=body.query[:200],
        )
    else:
        logger.info(
            "[GRAPH ADMIN] read Cypher executed",
            actor=actor,
            query_preview=body.query[:200],
        )

    return await gs.execute_cypher(
        cypher=body.query,
        params=body.params or {},
        allow_write=body.allow_write,
        timeout_sec=body.timeout_sec,
    )


# =============================================================================
# Maintenance Operations
# =============================================================================


@router.post("/rebuild-indexes", dependencies=[Depends(require_data_admin)])
async def rebuild_indexes(request: Request) -> Dict[str, Any]:
    gs = _get_graph_service(request)
    if gs is None or not gs.available:
        raise HTTPException(status_code=503, detail="graph_service not available")
    actor = _actor_id(request)
    logger.info("[GRAPH ADMIN] rebuild indexes requested", actor=actor)
    return await gs.rebuild_indexes()


class PurgeOrphansRequest(BaseModel):
    dry_run: bool = Field(default=True, description="If true, only reports counts")


@router.post("/purge-orphans", dependencies=[Depends(require_data_admin)])
async def purge_orphans(
    request: Request,
    body: PurgeOrphansRequest,
) -> Dict[str, Any]:
    gs = _get_graph_service(request)
    if gs is None or not gs.available:
        raise HTTPException(status_code=503, detail="graph_service not available")
    actor = _actor_id(request)
    logger.warning(
        "[GRAPH ADMIN] purge orphans requested",
        actor=actor,
        dry_run=body.dry_run,
    )
    return await gs.purge_orphans(dry_run=body.dry_run)
