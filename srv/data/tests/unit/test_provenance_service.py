"""
Tests for provenance hash chain computation and data model.

Tests the pure functions (no database) from provenance_service.py:
  - compute_content_hash
  - compute_chain_hash
  - verify_chain_hash
  - to_w3c_prov
"""

import hashlib
import json

import pytest

from services.provenance_service import (
    GENESIS_HASH,
    ProvenanceNode,
    ProvenanceService,
    compute_chain_hash,
    compute_content_hash,
    to_w3c_prov,
    verify_chain_hash,
)


# ── compute_content_hash ───────────────────────────────────────────

class TestComputeContentHash:
    def test_string_input(self):
        result = compute_content_hash("hello world")
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert result == expected

    def test_bytes_input(self):
        result = compute_content_hash(b"\x00\x01\x02")
        expected = hashlib.sha256(b"\x00\x01\x02").hexdigest()
        assert result == expected

    def test_empty_string(self):
        result = compute_content_hash("")
        assert len(result) == 64
        assert result == hashlib.sha256(b"").hexdigest()

    def test_deterministic(self):
        assert compute_content_hash("test") == compute_content_hash("test")

    def test_different_inputs(self):
        assert compute_content_hash("a") != compute_content_hash("b")


# ── compute_chain_hash ─────────────────────────────────────────────

class TestComputeChainHash:
    def test_deterministic(self):
        h1 = compute_chain_hash("parent", "step", "output")
        h2 = compute_chain_hash("parent", "step", "output")
        assert h1 == h2

    def test_pipe_delimited_format(self):
        expected = hashlib.sha256(b"parent|step|output").hexdigest()
        assert compute_chain_hash("parent", "step", "output") == expected

    def test_different_parents(self):
        h1 = compute_chain_hash("parent_a", "ocr", "out")
        h2 = compute_chain_hash("parent_b", "ocr", "out")
        assert h1 != h2

    def test_genesis_hash_as_parent(self):
        result = compute_chain_hash(GENESIS_HASH, "upload", "file_hash")
        assert isinstance(result, str) and len(result) == 64


# ── verify_chain_hash ──────────────────────────────────────────────

class TestVerifyChainHash:
    def _make_node(self, parent_hash, step_type, output_hash) -> ProvenanceNode:
        chain = compute_chain_hash(parent_hash, step_type, output_hash)
        return ProvenanceNode(
            id="node-1",
            entity_type="file",
            entity_id="file-123",
            parent_id=None,
            step_type=step_type,
            input_hash="input-hash",
            output_hash=output_hash,
            chain_hash=chain,
        )

    def test_valid_node(self):
        node = self._make_node(GENESIS_HASH, "upload", "abc123")
        assert verify_chain_hash(node, GENESIS_HASH) is True

    def test_tampered_chain_hash(self):
        node = self._make_node(GENESIS_HASH, "upload", "abc123")
        node.chain_hash = "0" * 64
        assert verify_chain_hash(node, GENESIS_HASH) is False

    def test_wrong_parent(self):
        node = self._make_node(GENESIS_HASH, "upload", "abc123")
        assert verify_chain_hash(node, "wrong_parent_hash") is False


# ── ProvenanceNode dataclass ───────────────────────────────────────

class TestProvenanceNode:
    def test_defaults(self):
        node = ProvenanceNode(
            id="1", entity_type="file", entity_id="f-1",
            parent_id=None, step_type="upload",
            input_hash="ih", output_hash="oh", chain_hash="ch",
        )
        assert node.model_version is None
        assert node.processor_version is None
        assert node.metadata == {}
        assert node.created_at is None

    def test_with_metadata(self):
        node = ProvenanceNode(
            id="1", entity_type="chunk", entity_id="c-1",
            parent_id="p-1", step_type="chunk",
            input_hash="ih", output_hash="oh", chain_hash="ch",
            model_version="v1", metadata={"page": 3},
        )
        assert node.model_version == "v1"
        assert node.metadata["page"] == 3


# ── to_w3c_prov ────────────────────────────────────────────────────

class TestToW3cProv:
    def _build_chain(self) -> list:
        root_output = compute_content_hash("file content")
        root_chain = compute_chain_hash(GENESIS_HASH, "upload", root_output)
        root = ProvenanceNode(
            id="root-id", entity_type="file", entity_id="file-1",
            parent_id=None, step_type="upload",
            input_hash=compute_content_hash("upload"),
            output_hash=root_output, chain_hash=root_chain,
            model_version=None, processor_version="pymupdf4llm",
            created_at="2026-01-01T00:00:00Z",
        )

        ocr_output = compute_content_hash("ocr text")
        ocr_chain = compute_chain_hash(root_chain, "ocr", ocr_output)
        ocr = ProvenanceNode(
            id="ocr-id", entity_type="chunk", entity_id="chunk-1",
            parent_id="root-id", step_type="ocr",
            input_hash=root_output, output_hash=ocr_output,
            chain_hash=ocr_chain, model_version="tesseract",
        )

        return [root, ocr]

    def test_basic_structure(self):
        chain = self._build_chain()
        doc = to_w3c_prov(chain, "file-1")

        assert "prefix" in doc
        assert "entity" in doc
        assert "activity" in doc
        assert "wasGeneratedBy" in doc
        assert "wasDerivedFrom" in doc

    def test_entities_match_chain(self):
        chain = self._build_chain()
        doc = to_w3c_prov(chain, "file-1")

        assert len(doc["entity"]) == 2
        assert "busibox:file:file-1" in doc["entity"]
        assert "busibox:chunk:chunk-1" in doc["entity"]

    def test_activities_have_step_type(self):
        chain = self._build_chain()
        doc = to_w3c_prov(chain, "file-1")

        activities = list(doc["activity"].values())
        step_types = [a["prov:type"] for a in activities]
        assert "upload" in step_types
        assert "ocr" in step_types

    def test_derivations_link_parent(self):
        chain = self._build_chain()
        doc = to_w3c_prov(chain, "file-1")

        derivations = list(doc["wasDerivedFrom"].values())
        assert len(derivations) == 1  # OCR derives from file
        assert derivations[0]["prov:usedEntity"] == "busibox:file:file-1"
        assert derivations[0]["prov:generatedEntity"] == "busibox:chunk:chunk-1"

    def test_model_version_in_activity(self):
        chain = self._build_chain()
        doc = to_w3c_prov(chain, "file-1")

        ocr_activity = None
        for act in doc["activity"].values():
            if act["prov:type"] == "ocr":
                ocr_activity = act
                break
        assert ocr_activity is not None
        assert ocr_activity["busibox:modelVersion"] == "tesseract"

    def test_processor_version_in_activity(self):
        chain = self._build_chain()
        doc = to_w3c_prov(chain, "file-1")

        upload_activity = None
        for act in doc["activity"].values():
            if act["prov:type"] == "upload":
                upload_activity = act
                break
        assert upload_activity is not None
        assert upload_activity["busibox:processorVersion"] == "pymupdf4llm"

    def test_empty_chain(self):
        doc = to_w3c_prov([], "file-1")
        assert doc["entity"] == {}
        assert doc["activity"] == {}


# ── ProvenanceService (pure-logic, mocked DB) ─────────────────────

class TestProvenanceServiceVerifyChainSync:
    """Test verify_chain_sync logic using manually constructed node lists."""

    def test_valid_single_node_chain(self):
        """A chain with one root node should verify."""
        output_hash = compute_content_hash("content")
        chain_hash = compute_chain_hash(GENESIS_HASH, "upload", output_hash)
        root = ProvenanceNode(
            id="r1", entity_type="file", entity_id="f1",
            parent_id=None, step_type="upload",
            input_hash=compute_content_hash("upload"),
            output_hash=output_hash, chain_hash=chain_hash,
        )

        service = ProvenanceService()
        # Bypass DB by directly testing verify logic
        chain = [root]

        # Replicate verify_chain_sync logic without DB
        results = []
        all_valid = True
        for node in chain:
            parent_hash = GENESIS_HASH
            is_valid = verify_chain_hash(node, parent_hash)
            if not is_valid:
                all_valid = False
            results.append({"id": node.id, "valid": is_valid})

        assert all_valid is True

    def test_valid_two_node_chain(self):
        out1 = compute_content_hash("file bytes")
        ch1 = compute_chain_hash(GENESIS_HASH, "upload", out1)
        root = ProvenanceNode(
            id="r1", entity_type="file", entity_id="f1",
            parent_id=None, step_type="upload",
            input_hash="ih", output_hash=out1, chain_hash=ch1,
        )

        out2 = compute_content_hash("ocr text")
        ch2 = compute_chain_hash(ch1, "ocr", out2)
        child = ProvenanceNode(
            id="c1", entity_type="chunk", entity_id="ck1",
            parent_id="r1", step_type="ocr",
            input_hash=out1, output_hash=out2, chain_hash=ch2,
        )

        chain = [root, child]
        chain_by_id = {n.id: n for n in chain}

        all_valid = True
        for node in chain:
            if node.parent_id and node.parent_id in chain_by_id:
                parent_hash = chain_by_id[node.parent_id].chain_hash
            else:
                parent_hash = GENESIS_HASH
            if not verify_chain_hash(node, parent_hash):
                all_valid = False

        assert all_valid is True

    def test_tampered_child_detected(self):
        out1 = compute_content_hash("file")
        ch1 = compute_chain_hash(GENESIS_HASH, "upload", out1)
        root = ProvenanceNode(
            id="r1", entity_type="file", entity_id="f1",
            parent_id=None, step_type="upload",
            input_hash="ih", output_hash=out1, chain_hash=ch1,
        )

        child = ProvenanceNode(
            id="c1", entity_type="chunk", entity_id="ck1",
            parent_id="r1", step_type="ocr",
            input_hash=out1, output_hash="out2",
            chain_hash="tampered_hash_0000000000000000000000000000000",
        )

        chain_by_id = {n.id: n for n in [root, child]}
        parent_hash = chain_by_id[child.parent_id].chain_hash
        assert verify_chain_hash(child, parent_hash) is False
