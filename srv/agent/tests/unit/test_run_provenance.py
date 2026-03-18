"""
Tests for agent run provenance hash chain computation and verification.
"""

import hashlib
import json

import pytest

from app.services.run_provenance import (
    GENESIS_HASH,
    _chain_hash,
    _content_hash,
    build_run_provenance,
    verify_run_provenance,
)


# ── _content_hash ──────────────────────────────────────────────────

class TestContentHash:
    def test_string_input(self):
        result = _content_hash("hello")
        expected = hashlib.sha256(b"hello").hexdigest()
        assert result == expected

    def test_bytes_input(self):
        result = _content_hash(b"raw bytes")
        expected = hashlib.sha256(b"raw bytes").hexdigest()
        assert result == expected

    def test_dict_input_sorted(self):
        """Dicts are serialized with sorted keys for determinism."""
        h1 = _content_hash({"b": 2, "a": 1})
        h2 = _content_hash({"a": 1, "b": 2})
        assert h1 == h2

    def test_list_input(self):
        result = _content_hash([1, 2, 3])
        expected_json = json.dumps([1, 2, 3], sort_keys=True, default=str)
        expected = hashlib.sha256(expected_json.encode("utf-8")).hexdigest()
        assert result == expected

    def test_empty_string(self):
        result = _content_hash("")
        assert isinstance(result, str)
        assert len(result) == 64

    def test_different_inputs_different_hashes(self):
        assert _content_hash("a") != _content_hash("b")


# ── _chain_hash ────────────────────────────────────────────────────

class TestChainHash:
    def test_deterministic(self):
        h1 = _chain_hash("parent", "step", "output")
        h2 = _chain_hash("parent", "step", "output")
        assert h1 == h2

    def test_different_parent_different_hash(self):
        h1 = _chain_hash("parent_a", "step", "output")
        h2 = _chain_hash("parent_b", "step", "output")
        assert h1 != h2

    def test_different_step_different_hash(self):
        h1 = _chain_hash("parent", "step_a", "output")
        h2 = _chain_hash("parent", "step_b", "output")
        assert h1 != h2

    def test_format(self):
        """Chain hash is SHA-256 of 'parent|step|output'."""
        expected = hashlib.sha256(b"p|s|o").hexdigest()
        assert _chain_hash("p", "s", "o") == expected


# ── build_run_provenance ───────────────────────────────────────────

class TestBuildRunProvenance:
    def test_minimal_run_no_tools(self):
        result = build_run_provenance(
            run_input={"prompt": "hello"},
            tool_calls=[],
            run_output="response",
            agent_id="agent-1",
        )

        assert result["type"] == "provenance"
        data = result["data"]
        assert data["agent_id"] == "agent-1"
        assert data["chain_length"] == 2  # input + output
        assert len(data["nodes"]) == 2
        assert data["nodes"][0]["step_type"] == "run_input"
        assert data["nodes"][1]["step_type"] == "run_output"

    def test_run_with_tool_calls(self):
        tool_calls = [
            {"name": "web_search", "args": {"query": "test"}, "result": "found"},
            {"name": "doc_search", "args": {"q": "x"}, "result": {"docs": []}},
        ]
        result = build_run_provenance(
            run_input={"prompt": "search something"},
            tool_calls=tool_calls,
            run_output="final answer",
            agent_id="agent-2",
            model_version="gpt-4",
        )

        data = result["data"]
        assert data["chain_length"] == 4  # input + 2 tools + output
        assert data["model_version"] == "gpt-4"
        assert data["nodes"][1]["step_type"] == "tool_call:web_search"
        assert data["nodes"][2]["step_type"] == "tool_call:doc_search"
        assert data["nodes"][1]["tool_index"] == 0
        assert data["nodes"][2]["tool_index"] == 1

    def test_chain_hashes_are_linked(self):
        """Each node's chain hash should incorporate the previous node's hash."""
        result = build_run_provenance(
            run_input={"prompt": "test"},
            tool_calls=[{"name": "t1", "args": {}, "result": "r1"}],
            run_output="out",
            agent_id="a",
        )

        nodes = result["data"]["nodes"]
        assert len(nodes) == 3

        # Input node uses GENESIS_HASH as parent
        input_node = nodes[0]
        expected_input_chain = _chain_hash(
            GENESIS_HASH, "run_input", input_node["content_hash"]
        )
        assert input_node["chain_hash"] == expected_input_chain

        # Tool node uses input's chain hash as parent
        tool_node = nodes[1]
        expected_tool_chain = _chain_hash(
            input_node["chain_hash"], "tool_call:t1", tool_node["output_hash"]
        )
        assert tool_node["chain_hash"] == expected_tool_chain

        # Output node uses tool's chain hash as parent
        output_node = nodes[2]
        expected_output_chain = _chain_hash(
            tool_node["chain_hash"], "run_output", output_node["content_hash"]
        )
        assert output_node["chain_hash"] == expected_output_chain

    def test_none_output_handled(self):
        result = build_run_provenance(
            run_input={"prompt": "x"},
            tool_calls=[],
            run_output=None,
            agent_id="a",
        )
        assert result["data"]["chain_length"] == 2

    def test_final_chain_hash_matches_data(self):
        result = build_run_provenance(
            run_input={"prompt": "q"},
            tool_calls=[],
            run_output="answer",
            agent_id="a",
        )
        data = result["data"]
        last_node = data["nodes"][-1]
        assert data["chain_hash"] == last_node["chain_hash"]

    def test_timestamp_present(self):
        result = build_run_provenance(
            run_input={}, tool_calls=[], run_output="", agent_id="a",
        )
        assert "timestamp" in result
        assert "T" in result["timestamp"]  # ISO format


# ── verify_run_provenance ──────────────────────────────────────────

class TestVerifyRunProvenance:
    def test_valid_chain(self):
        event = build_run_provenance(
            run_input={"prompt": "test"},
            tool_calls=[{"name": "t", "args": {}, "result": "ok"}],
            run_output="done",
            agent_id="a",
        )
        verification = verify_run_provenance(event)
        assert verification["valid"] is True
        assert verification["chain_length"] == 3
        for node in verification["nodes"]:
            assert node["valid"] is True

    def test_tampered_chain_hash_detected(self):
        event = build_run_provenance(
            run_input={"prompt": "test"},
            tool_calls=[],
            run_output="done",
            agent_id="a",
        )
        # Tamper with the output node's chain hash
        event["data"]["nodes"][-1]["chain_hash"] = "0" * 64

        verification = verify_run_provenance(event)
        assert verification["valid"] is False
        assert verification["nodes"][-1]["valid"] is False
        assert verification["nodes"][0]["valid"] is True

    def test_empty_nodes(self):
        verification = verify_run_provenance({"data": {"nodes": []}})
        assert verification["valid"] is False
        assert "error" in verification

    def test_missing_data_key(self):
        verification = verify_run_provenance({})
        assert verification["valid"] is False

    def test_all_tool_calls_verified(self):
        event = build_run_provenance(
            run_input={"prompt": "search"},
            tool_calls=[
                {"name": "search", "args": {"q": "a"}, "result": "r1"},
                {"name": "scrape", "args": {"url": "b"}, "result": "r2"},
                {"name": "analyze", "args": {"x": "c"}, "result": "r3"},
            ],
            run_output="final",
            agent_id="a",
        )
        verification = verify_run_provenance(event)
        assert verification["valid"] is True
        assert verification["chain_length"] == 5  # input + 3 tools + output

    def test_roundtrip_stability(self):
        """Build, serialize to JSON, deserialize, verify -- should still pass."""
        event = build_run_provenance(
            run_input={"prompt": "roundtrip"},
            tool_calls=[{"name": "t", "args": {"k": "v"}, "result": [1, 2]}],
            run_output={"summary": "ok"},
            agent_id="a",
        )
        serialized = json.dumps(event)
        deserialized = json.loads(serialized)
        verification = verify_run_provenance(deserialized)
        assert verification["valid"] is True
