"""
Provenance tracking API endpoints.

Provides endpoints for:
- GET /files/{fileId}/provenance: Get full provenance chain for a file
- POST /files/{fileId}/provenance/verify: Verify integrity of a file's provenance chain
- GET /files/{fileId}/provenance/export: Export provenance chain in W3C PROV-JSON format
"""

import structlog
from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import JSONResponse

from api.middleware.jwt_auth import ScopeChecker
from services.provenance_service import ProvenanceService, to_w3c_prov

logger = structlog.get_logger()


def _get_postgres_service():
    """Lazy import to avoid circular import with api.main."""
    from api.main import pg_service
    return pg_service


router = APIRouter()
provenance_service = ProvenanceService()


@router.get(
    "/{fileId}/provenance",
    summary="Get provenance chain",
    description="Returns the full cryptographic provenance chain for a file, "
                "including all derived entities (chunks, embeddings, images, records).",
    dependencies=[Depends(ScopeChecker("data:read"))],
)
async def get_provenance(
    fileId: str,
    request: Request,
):
    """Get the provenance chain for a file."""
    pg = _get_postgres_service()

    try:
        chain = await provenance_service.get_chain_for_file(pg, fileId, request)

        if not chain:
            return JSONResponse(
                status_code=200,
                content={
                    "file_id": fileId,
                    "chain_length": 0,
                    "nodes": [],
                    "message": "No provenance data recorded for this file",
                },
            )

        nodes = [
            {
                "id": n.id,
                "entity_type": n.entity_type,
                "entity_id": n.entity_id,
                "parent_id": n.parent_id,
                "step_type": n.step_type,
                "input_hash": n.input_hash,
                "output_hash": n.output_hash,
                "chain_hash": n.chain_hash,
                "model_version": n.model_version,
                "processor_version": n.processor_version,
                "metadata": n.metadata,
                "created_at": n.created_at,
            }
            for n in chain
        ]

        return JSONResponse(
            status_code=200,
            content={
                "file_id": fileId,
                "chain_length": len(chain),
                "nodes": nodes,
            },
        )
    except Exception as e:
        logger.error("Failed to get provenance chain", file_id=fileId, error=str(e))
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to get provenance chain: {str(e)}"},
        )


@router.post(
    "/{fileId}/provenance/verify",
    summary="Verify provenance chain integrity",
    description="Recomputes all chain hashes and verifies they match the stored values. "
                "Returns a per-node verification report.",
    dependencies=[Depends(ScopeChecker("data:read"))],
)
async def verify_provenance(
    fileId: str,
    request: Request,
):
    """Verify the integrity of a file's provenance chain."""
    pg = _get_postgres_service()

    try:
        result = await provenance_service.verify_file_chain(pg, fileId, request)
        return JSONResponse(status_code=200, content=result)
    except Exception as e:
        logger.error("Failed to verify provenance chain", file_id=fileId, error=str(e))
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to verify provenance chain: {str(e)}"},
        )


@router.get(
    "/{fileId}/provenance/export",
    summary="Export provenance in W3C PROV format",
    description="Exports the provenance chain in W3C PROV-JSON format "
                "(https://www.w3.org/TR/prov-json/) for compliance and interoperability.",
    dependencies=[Depends(ScopeChecker("data:read"))],
)
async def export_provenance(
    fileId: str,
    request: Request,
):
    """Export provenance chain in W3C PROV-JSON format."""
    pg = _get_postgres_service()

    try:
        chain = await provenance_service.get_chain_for_file(pg, fileId, request)

        if not chain:
            return JSONResponse(
                status_code=200,
                content={
                    "message": "No provenance data recorded for this file",
                    "prefix": {},
                    "entity": {},
                    "activity": {},
                    "wasGeneratedBy": {},
                    "wasDerivedFrom": {},
                },
            )

        prov_doc = to_w3c_prov(chain, fileId)
        return JSONResponse(status_code=200, content=prov_doc)
    except Exception as e:
        logger.error("Failed to export provenance", file_id=fileId, error=str(e))
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to export provenance: {str(e)}"},
        )
