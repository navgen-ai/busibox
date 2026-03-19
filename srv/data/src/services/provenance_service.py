"""
Provenance tracking service for cryptographic data lineage.

Provides SHA-256 hash chain computation and storage for tracking the
complete lineage of data through the processing pipeline:

    DOCUMENT -> OCR_RESULT -> CHUNK -> EMBEDDING
                            -> IMAGE -> VLM_DESCRIPTION
                            -> AGENT_EXTRACTION -> RECORD

Each node in the chain stores:
    chain_hash = SHA-256(parent.chain_hash || step_type || output_hash)

This allows any output to be verified back to its original source document.
"""

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


GENESIS_HASH = "0" * 64


@dataclass
class ProvenanceNode:
    """A single node in the provenance chain."""
    id: str
    entity_type: str
    entity_id: str
    parent_id: Optional[str]
    step_type: str
    input_hash: str
    output_hash: str
    chain_hash: str
    model_version: Optional[str] = None
    processor_version: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[str] = None


def compute_content_hash(content: bytes | str) -> str:
    """Compute SHA-256 hash of content."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def compute_chain_hash(
    parent_chain_hash: str,
    step_type: str,
    output_hash: str,
) -> str:
    """
    Compute the chain hash linking this step to its parent.

    chain_hash = SHA-256(parent_chain_hash || step_type || output_hash)
    """
    data = f"{parent_chain_hash}|{step_type}|{output_hash}"
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def verify_chain_hash(node: ProvenanceNode, parent_chain_hash: str) -> bool:
    """Verify that a node's chain_hash is correct given its parent."""
    expected = compute_chain_hash(parent_chain_hash, node.step_type, node.output_hash)
    return expected == node.chain_hash


class ProvenanceService:
    """
    Service for recording and querying provenance chains.

    Uses either sync (psycopg2) or async (asyncpg) depending on context.
    The worker uses sync; the API uses async.
    """

    def __init__(self, postgres_service=None):
        self._pg = postgres_service

    # ── Sync methods (used by the ingest worker) ─────────────────────

    def record_step_sync(
        self,
        conn,
        entity_type: str,
        entity_id: str,
        step_type: str,
        input_content: bytes | str,
        output_content: bytes | str,
        parent_provenance_id: Optional[str] = None,
        parent_chain_hash: Optional[str] = None,
        model_version: Optional[str] = None,
        processor_version: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ProvenanceNode:
        """
        Record a provenance step synchronously.

        Args:
            conn: psycopg2 connection
            entity_type: Type of entity (file, chunk, record, embedding, image, agent_run)
            entity_id: ID of the entity
            step_type: Type of processing step
            input_content: Raw input content for hashing
            output_content: Raw output content for hashing
            parent_provenance_id: UUID of the parent provenance node
            parent_chain_hash: Chain hash of the parent (if no parent_provenance_id)
            model_version: Version of the model used
            processor_version: Version of the processor used
            metadata: Additional metadata

        Returns:
            ProvenanceNode with the computed hashes
        """
        input_hash = compute_content_hash(input_content)
        output_hash = compute_content_hash(output_content)

        if parent_chain_hash is None:
            parent_chain_hash = GENESIS_HASH

        chain_hash = compute_chain_hash(parent_chain_hash, step_type, output_hash)

        node_id = str(uuid.uuid4())

        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO data_provenance
                        (id, entity_type, entity_id, parent_id, step_type,
                         input_hash, output_hash, chain_hash,
                         model_version, processor_version, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        node_id,
                        entity_type,
                        entity_id,
                        parent_provenance_id,
                        step_type,
                        input_hash,
                        output_hash,
                        chain_hash,
                        model_version,
                        processor_version,
                        json.dumps(metadata or {}),
                    ),
                )
                conn.commit()
        except Exception as e:
            conn.rollback()
            logger.warning(
                "Failed to record provenance step",
                extra={
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "step_type": step_type,
                    "error": str(e),
                },
            )
            raise

        return ProvenanceNode(
            id=node_id,
            entity_type=entity_type,
            entity_id=entity_id,
            parent_id=parent_provenance_id,
            step_type=step_type,
            input_hash=input_hash,
            output_hash=output_hash,
            chain_hash=chain_hash,
            model_version=model_version,
            processor_version=processor_version,
            metadata=metadata or {},
        )

    def get_chain_sync(self, conn, entity_type: str, entity_id: str) -> List[ProvenanceNode]:
        """
        Get the full provenance chain for an entity (walking up to the root).

        Returns nodes from the entity back to the genesis (root first).
        """
        nodes = []
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH RECURSIVE chain AS (
                    SELECT id, entity_type, entity_id, parent_id, step_type,
                           input_hash, output_hash, chain_hash,
                           model_version, processor_version, metadata, created_at
                    FROM data_provenance
                    WHERE entity_type = %s AND entity_id = %s
                    
                    UNION ALL
                    
                    SELECT p.id, p.entity_type, p.entity_id, p.parent_id, p.step_type,
                           p.input_hash, p.output_hash, p.chain_hash,
                           p.model_version, p.processor_version, p.metadata, p.created_at
                    FROM data_provenance p
                    JOIN chain c ON p.id = c.parent_id
                )
                SELECT * FROM chain ORDER BY created_at ASC
                """,
                (entity_type, entity_id),
            )
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]

            for row in rows:
                row_dict = dict(zip(columns, row))
                nodes.append(
                    ProvenanceNode(
                        id=str(row_dict["id"]),
                        entity_type=row_dict["entity_type"],
                        entity_id=row_dict["entity_id"],
                        parent_id=str(row_dict["parent_id"]) if row_dict["parent_id"] else None,
                        step_type=row_dict["step_type"],
                        input_hash=row_dict["input_hash"],
                        output_hash=row_dict["output_hash"],
                        chain_hash=row_dict["chain_hash"],
                        model_version=row_dict.get("model_version"),
                        processor_version=row_dict.get("processor_version"),
                        metadata=row_dict.get("metadata") or {},
                        created_at=str(row_dict["created_at"]) if row_dict.get("created_at") else None,
                    )
                )

        return nodes

    def get_descendants_sync(self, conn, provenance_id: str) -> List[ProvenanceNode]:
        """Get all provenance nodes descended from a given node."""
        nodes = []
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH RECURSIVE descendants AS (
                    SELECT id, entity_type, entity_id, parent_id, step_type,
                           input_hash, output_hash, chain_hash,
                           model_version, processor_version, metadata, created_at
                    FROM data_provenance
                    WHERE parent_id = %s
                    
                    UNION ALL
                    
                    SELECT p.id, p.entity_type, p.entity_id, p.parent_id, p.step_type,
                           p.input_hash, p.output_hash, p.chain_hash,
                           p.model_version, p.processor_version, p.metadata, p.created_at
                    FROM data_provenance p
                    JOIN descendants d ON p.parent_id = d.id
                )
                SELECT * FROM descendants ORDER BY created_at ASC
                """,
                (provenance_id,),
            )
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]

            for row in rows:
                row_dict = dict(zip(columns, row))
                nodes.append(
                    ProvenanceNode(
                        id=str(row_dict["id"]),
                        entity_type=row_dict["entity_type"],
                        entity_id=row_dict["entity_id"],
                        parent_id=str(row_dict["parent_id"]) if row_dict["parent_id"] else None,
                        step_type=row_dict["step_type"],
                        input_hash=row_dict["input_hash"],
                        output_hash=row_dict["output_hash"],
                        chain_hash=row_dict["chain_hash"],
                        model_version=row_dict.get("model_version"),
                        processor_version=row_dict.get("processor_version"),
                        metadata=row_dict.get("metadata") or {},
                        created_at=str(row_dict["created_at"]) if row_dict.get("created_at") else None,
                    )
                )

        return nodes

    def verify_chain_sync(self, conn, entity_type: str, entity_id: str) -> Dict[str, Any]:
        """
        Verify the integrity of a provenance chain.

        Returns a verification report with pass/fail status per node.
        """
        chain = self.get_chain_sync(conn, entity_type, entity_id)

        if not chain:
            return {"valid": False, "error": "No provenance chain found", "nodes": []}

        results = []
        all_valid = True

        for node in chain:
            if node.parent_id is None:
                parent_hash = GENESIS_HASH
            else:
                parent_nodes = [n for n in chain if n.id == node.parent_id]
                if parent_nodes:
                    parent_hash = parent_nodes[0].chain_hash
                else:
                    parent_hash = GENESIS_HASH

            is_valid = verify_chain_hash(node, parent_hash)
            if not is_valid:
                all_valid = False

            results.append({
                "id": node.id,
                "entity_type": node.entity_type,
                "entity_id": node.entity_id,
                "step_type": node.step_type,
                "chain_hash": node.chain_hash,
                "valid": is_valid,
            })

        return {
            "valid": all_valid,
            "chain_length": len(chain),
            "nodes": results,
        }

    # ── Async methods (used by the API) ──────────────────────────────

    async def get_chain_for_file(self, pg_service, file_id: str, request=None) -> List[ProvenanceNode]:
        """Get the full provenance tree for a file (all nodes rooted at the file upload)."""
        async with pg_service.acquire(request=request) as conn:
            rows = await conn.fetch(
                """
                WITH RECURSIVE tree AS (
                    SELECT id, entity_type, entity_id, parent_id, step_type,
                           input_hash, output_hash, chain_hash,
                           model_version, processor_version, metadata, created_at
                    FROM data_provenance
                    WHERE entity_type = 'file' AND entity_id = $1

                    UNION ALL

                    SELECT p.id, p.entity_type, p.entity_id, p.parent_id, p.step_type,
                           p.input_hash, p.output_hash, p.chain_hash,
                           p.model_version, p.processor_version, p.metadata, p.created_at
                    FROM data_provenance p
                    JOIN tree t ON p.parent_id = t.id
                )
                SELECT * FROM tree ORDER BY created_at ASC
                """,
                file_id,
            )

        return [
            ProvenanceNode(
                id=str(r["id"]),
                entity_type=r["entity_type"],
                entity_id=r["entity_id"],
                parent_id=str(r["parent_id"]) if r["parent_id"] else None,
                step_type=r["step_type"],
                input_hash=r["input_hash"],
                output_hash=r["output_hash"],
                chain_hash=r["chain_hash"],
                model_version=r.get("model_version"),
                processor_version=r.get("processor_version"),
                metadata=r.get("metadata") or {},
                created_at=str(r["created_at"]) if r.get("created_at") else None,
            )
            for r in rows
        ]

    async def verify_file_chain(self, pg_service, file_id: str, request=None) -> Dict[str, Any]:
        """Verify the full provenance tree for a file."""
        chain = await self.get_chain_for_file(pg_service, file_id, request)

        if not chain:
            return {"valid": False, "error": "No provenance chain found", "nodes": []}

        chain_by_id = {n.id: n for n in chain}
        results = []
        all_valid = True

        for node in chain:
            if node.parent_id and node.parent_id in chain_by_id:
                parent_hash = chain_by_id[node.parent_id].chain_hash
            else:
                parent_hash = GENESIS_HASH

            is_valid = verify_chain_hash(node, parent_hash)
            if not is_valid:
                all_valid = False

            results.append({
                "id": node.id,
                "entity_type": node.entity_type,
                "entity_id": node.entity_id,
                "step_type": node.step_type,
                "chain_hash": node.chain_hash,
                "valid": is_valid,
                "model_version": node.model_version,
                "processor_version": node.processor_version,
                "created_at": node.created_at,
            })

        return {
            "valid": all_valid,
            "file_id": file_id,
            "chain_length": len(chain),
            "nodes": results,
        }


def to_w3c_prov(chain: List[ProvenanceNode], file_id: str) -> Dict[str, Any]:
    """
    Export a provenance chain in W3C PROV-JSON format.

    See: https://www.w3.org/TR/prov-json/
    """
    doc: Dict[str, Any] = {
        "prefix": {
            "busibox": "https://busibox.local/provenance/",
            "prov": "http://www.w3.org/ns/prov#",
        },
        "entity": {},
        "activity": {},
        "wasGeneratedBy": {},
        "wasDerivedFrom": {},
    }

    chain_by_id = {n.id: n for n in chain}

    for node in chain:
        entity_key = f"busibox:{node.entity_type}:{node.entity_id}"
        doc["entity"][entity_key] = {
            "prov:type": node.entity_type,
            "busibox:entityId": node.entity_id,
            "busibox:chainHash": node.chain_hash,
            "busibox:outputHash": node.output_hash,
        }

        activity_key = f"busibox:step:{node.id}"
        activity = {
            "prov:type": node.step_type,
            "busibox:inputHash": node.input_hash,
            "busibox:outputHash": node.output_hash,
        }
        if node.model_version:
            activity["busibox:modelVersion"] = node.model_version
        if node.processor_version:
            activity["busibox:processorVersion"] = node.processor_version
        if node.created_at:
            activity["prov:startTime"] = node.created_at
        doc["activity"][activity_key] = activity

        gen_key = f"busibox:gen:{node.id}"
        doc["wasGeneratedBy"][gen_key] = {
            "prov:entity": entity_key,
            "prov:activity": activity_key,
        }

        if node.parent_id and node.parent_id in chain_by_id:
            parent = chain_by_id[node.parent_id]
            parent_entity_key = f"busibox:{parent.entity_type}:{parent.entity_id}"
            deriv_key = f"busibox:deriv:{node.id}"
            doc["wasDerivedFrom"][deriv_key] = {
                "prov:generatedEntity": entity_key,
                "prov:usedEntity": parent_entity_key,
                "prov:activity": activity_key,
            }

    return doc
