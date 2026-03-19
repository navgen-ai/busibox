"""
Provenance tracker for the ingest worker pipeline.

Wraps ProvenanceService to provide convenient methods for tracking
provenance during document processing. Non-fatal: if provenance
recording fails, processing continues normally.
"""

import logging
from typing import Any, Dict, List, Optional

from services.provenance_service import (
    ProvenanceNode,
    ProvenanceService,
    compute_content_hash,
)

logger = logging.getLogger(__name__)

# Processor version for the current pipeline
PIPELINE_VERSION = "progressive-v2"


class ProvenanceTracker:
    """
    Tracks provenance during document processing.

    All methods are non-fatal: failures are logged but don't interrupt
    processing. The tracker maintains a reference to the root (upload)
    provenance node so child steps can link back to it.
    """

    def __init__(self, postgres_service):
        self._pg = postgres_service
        self._service = ProvenanceService(postgres_service)
        self._root_node: Optional[ProvenanceNode] = None
        self._nodes: Dict[str, ProvenanceNode] = {}

    def _get_conn(self, rls_context=None):
        return self._pg._get_connection(rls_context)

    def _return_conn(self, conn):
        self._pg._return_connection(conn)

    def record_upload(
        self,
        file_id: str,
        content_hash: str,
        original_filename: str,
        mime_type: str,
        size_bytes: int,
        rls_context=None,
    ) -> Optional[ProvenanceNode]:
        """Record the initial file upload as the root of the provenance chain.

        On reprocessing, clears any existing provenance for this file first
        so the new chain starts fresh.
        """
        try:
            conn = self._get_conn(rls_context)
            try:
                # Clear existing provenance for this file so reprocessing
                # doesn't create duplicate chains.
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        DELETE FROM data_provenance
                        WHERE entity_id = %s
                           OR entity_id LIKE %s
                        """,
                        (file_id, f"{file_id}:%"),
                    )
                    conn.commit()

                node = self._service.record_step_sync(
                    conn=conn,
                    entity_type="file",
                    entity_id=file_id,
                    step_type="upload",
                    input_content=f"upload:{original_filename}",
                    output_content=content_hash,
                    model_version=None,
                    processor_version=PIPELINE_VERSION,
                    metadata={
                        "filename": original_filename,
                        "mime_type": mime_type,
                        "size_bytes": size_bytes,
                        "content_hash": content_hash,
                    },
                )
                self._root_node = node
                self._nodes[node.id] = node
                return node
            finally:
                self._return_conn(conn)
        except Exception as e:
            logger.warning(
                "Failed to record upload provenance (non-fatal)",
                extra={"file_id": file_id, "error": str(e)},
            )
            return None

    def load_root_node(self, file_id: str, rls_context=None) -> Optional[ProvenanceNode]:
        """Load the existing upload provenance node for a file.

        Used by pass 2/3 continuation jobs to chain new provenance steps
        onto the existing tree instead of creating duplicate upload records.
        """
        try:
            conn = self._get_conn(rls_context)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, entity_type, entity_id, step_type,
                               input_hash, output_hash, chain_hash,
                               parent_id
                        FROM data_provenance
                        WHERE entity_id = %s AND step_type = 'upload'
                        ORDER BY created_at DESC
                        LIMIT 1
                        """,
                        (file_id,),
                    )
                    row = cur.fetchone()
                    if row:
                        node = ProvenanceNode(
                            id=str(row[0]),
                            entity_type=row[1],
                            entity_id=row[2],
                            step_type=row[3],
                            input_hash=row[4],
                            output_hash=row[5],
                            chain_hash=row[6],
                            parent_id=str(row[7]) if row[7] else None,
                        )
                        self._root_node = node
                        self._nodes[node.id] = node
                        return node
                    return None
            finally:
                self._return_conn(conn)
        except Exception as e:
            logger.warning(
                "Failed to load root provenance node (non-fatal)",
                extra={"file_id": file_id, "error": str(e)},
            )
            return None

    def record_ocr(
        self,
        file_id: str,
        pass_number: int,
        input_text: str,
        output_text: str,
        model_version: Optional[str] = None,
        page_count: int = 0,
        rls_context=None,
    ) -> Optional[ProvenanceNode]:
        """Record an OCR/text extraction pass."""
        parent = self._root_node
        if not parent:
            return None

        try:
            conn = self._get_conn(rls_context)
            try:
                node = self._service.record_step_sync(
                    conn=conn,
                    entity_type="file",
                    entity_id=file_id,
                    step_type="ocr",
                    input_content=input_text or "",
                    output_content=output_text or "",
                    parent_provenance_id=parent.id,
                    parent_chain_hash=parent.chain_hash,
                    model_version=model_version,
                    processor_version=PIPELINE_VERSION,
                    metadata={
                        "pass_number": pass_number,
                        "page_count": page_count,
                        "input_chars": len(input_text) if input_text else 0,
                        "output_chars": len(output_text) if output_text else 0,
                    },
                )
                self._nodes[node.id] = node
                return node
            finally:
                self._return_conn(conn)
        except Exception as e:
            logger.warning(
                "Failed to record OCR provenance (non-fatal)",
                extra={"file_id": file_id, "error": str(e)},
            )
            return None

    def record_chunks(
        self,
        file_id: str,
        ocr_node: Optional[ProvenanceNode],
        chunks: List,
        processing_pass: int,
        rls_context=None,
    ) -> List[ProvenanceNode]:
        """Record provenance for each chunk produced from OCR output."""
        if not ocr_node:
            ocr_node = self._root_node
        if not ocr_node:
            return []

        chunk_nodes = []
        conn = None
        try:
            conn = self._get_conn(rls_context)
            for chunk in chunks:
                chunk_text = chunk.text if hasattr(chunk, "text") else str(chunk)
                chunk_index = getattr(chunk, "chunk_index", getattr(chunk, "index", 0))
                chunk_id = getattr(chunk, "chunk_id", f"{file_id}:{chunk_index}")

                try:
                    node = self._service.record_step_sync(
                        conn=conn,
                        entity_type="chunk",
                        entity_id=str(chunk_id),
                        step_type="chunk",
                        input_content=ocr_node.output_hash,
                        output_content=chunk_text,
                        parent_provenance_id=ocr_node.id,
                        parent_chain_hash=ocr_node.chain_hash,
                        processor_version=PIPELINE_VERSION,
                        metadata={
                            "chunk_index": chunk_index,
                            "processing_pass": processing_pass,
                            "token_count": getattr(chunk, "token_count", None),
                            "page_number": getattr(chunk, "page_number", None),
                        },
                    )
                    self._nodes[node.id] = node
                    chunk_nodes.append(node)
                except Exception as e:
                    logger.warning(
                        "Failed to record chunk provenance (non-fatal)",
                        extra={"file_id": file_id, "chunk_index": chunk_index, "error": str(e)},
                    )
        except Exception as e:
            logger.warning(
                "Failed to get connection for chunk provenance (non-fatal)",
                extra={"file_id": file_id, "error": str(e)},
            )
        finally:
            if conn:
                self._return_conn(conn)

        return chunk_nodes

    def record_embedding(
        self,
        file_id: str,
        chunk_node: ProvenanceNode,
        chunk_index: int,
        embedding_dim: int,
        model_version: Optional[str] = None,
        rls_context=None,
    ) -> Optional[ProvenanceNode]:
        """Record provenance for an embedding generated from a chunk."""
        try:
            conn = self._get_conn(rls_context)
            try:
                embedding_id = f"{file_id}:emb:{chunk_index}"
                node = self._service.record_step_sync(
                    conn=conn,
                    entity_type="embedding",
                    entity_id=embedding_id,
                    step_type="embedding",
                    input_content=chunk_node.output_hash,
                    output_content=f"embedding:{embedding_dim}d:{chunk_index}",
                    parent_provenance_id=chunk_node.id,
                    parent_chain_hash=chunk_node.chain_hash,
                    model_version=model_version,
                    processor_version=PIPELINE_VERSION,
                    metadata={
                        "chunk_index": chunk_index,
                        "embedding_dim": embedding_dim,
                    },
                )
                self._nodes[node.id] = node
                return node
            finally:
                self._return_conn(conn)
        except Exception as e:
            logger.warning(
                "Failed to record embedding provenance (non-fatal)",
                extra={"file_id": file_id, "error": str(e)},
            )
            return None

    def record_image(
        self,
        file_id: str,
        image_index: int,
        image_data: bytes,
        page_number: Optional[int] = None,
        rls_context=None,
    ) -> Optional[ProvenanceNode]:
        """Record provenance for an extracted image."""
        parent = self._root_node
        if not parent:
            return None

        try:
            conn = self._get_conn(rls_context)
            try:
                image_id = f"{file_id}:img:{image_index}"
                node = self._service.record_step_sync(
                    conn=conn,
                    entity_type="image",
                    entity_id=image_id,
                    step_type="image_extract",
                    input_content=parent.output_hash,
                    output_content=image_data,
                    parent_provenance_id=parent.id,
                    parent_chain_hash=parent.chain_hash,
                    processor_version=PIPELINE_VERSION,
                    metadata={
                        "image_index": image_index,
                        "page_number": page_number,
                        "size_bytes": len(image_data),
                    },
                )
                self._nodes[node.id] = node
                return node
            finally:
                self._return_conn(conn)
        except Exception as e:
            logger.warning(
                "Failed to record image provenance (non-fatal)",
                extra={"file_id": file_id, "error": str(e)},
            )
            return None

    def record_vision(
        self,
        file_id: str,
        page_number: int,
        vision_mode: str,
        description: str,
        model_version: Optional[str] = None,
        rls_context=None,
    ) -> Optional[ProvenanceNode]:
        """Record provenance for a vision description of a page."""
        parent = self._root_node
        if not parent:
            return None

        try:
            conn = self._get_conn(rls_context)
            try:
                vision_id = f"{file_id}:vision:{page_number}"
                node = self._service.record_step_sync(
                    conn=conn,
                    entity_type="file",
                    entity_id=vision_id,
                    step_type="vlm_describe",
                    input_content=parent.output_hash,
                    output_content=description,
                    parent_provenance_id=parent.id,
                    parent_chain_hash=parent.chain_hash,
                    model_version=model_version,
                    processor_version=PIPELINE_VERSION,
                    metadata={
                        "page_number": page_number,
                        "vision_mode": vision_mode,
                        "description_length": len(description),
                    },
                )
                self._nodes[node.id] = node
                return node
            finally:
                self._return_conn(conn)
        except Exception as e:
            logger.warning(
                "Failed to record vision provenance (non-fatal)",
                extra={"file_id": file_id, "error": str(e)},
            )
            return None

    @property
    def root_node(self) -> Optional[ProvenanceNode]:
        return self._root_node

    def get_node(self, node_id: str) -> Optional[ProvenanceNode]:
        return self._nodes.get(node_id)
