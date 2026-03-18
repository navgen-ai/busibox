"""
Provenance tracking for agent runs.

Computes SHA-256 hash chains for agent execution steps, linking
inputs -> tool calls -> outputs with cryptographic proof.

The provenance data is stored in the run_record.events list alongside
existing lifecycle events, using a special "provenance" event type.

Chain structure:
    INPUT_HASH -> TOOL_CALL_1_HASH -> TOOL_CALL_2_HASH -> ... -> OUTPUT_HASH

Each node: chain_hash = SHA-256(parent_chain_hash | step_type | output_hash)
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

GENESIS_HASH = "0" * 64


def _content_hash(content: str | bytes | dict | list) -> str:
    """Compute SHA-256 hash of content, normalizing dicts/lists to JSON."""
    if isinstance(content, (dict, list)):
        content = json.dumps(content, sort_keys=True, default=str)
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _chain_hash(parent_hash: str, step_type: str, output_hash: str) -> str:
    data = f"{parent_hash}|{step_type}|{output_hash}"
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def build_run_provenance(
    run_input: Dict[str, Any],
    tool_calls: List[Dict[str, Any]],
    run_output: Any,
    agent_id: str,
    model_version: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build a complete provenance chain for a finished agent run.

    Args:
        run_input: The run's input payload (prompt, etc.)
        tool_calls: List of tool call records [{name, args, result}, ...]
        run_output: The final output of the run
        agent_id: The agent that executed the run
        model_version: The LLM model used

    Returns:
        A provenance record with the full hash chain, suitable for
        storage in run_record.events as a "provenance" event.
    """
    nodes = []

    input_hash = _content_hash(run_input)
    input_chain = _chain_hash(GENESIS_HASH, "run_input", input_hash)
    nodes.append({
        "step_type": "run_input",
        "content_hash": input_hash,
        "chain_hash": input_chain,
    })

    current_chain = input_chain

    for i, tc in enumerate(tool_calls):
        tool_name = tc.get("name", "unknown")
        tool_args_hash = _content_hash(tc.get("args", {}))
        tool_result_hash = _content_hash(tc.get("result", ""))

        call_chain = _chain_hash(current_chain, f"tool_call:{tool_name}", tool_result_hash)
        nodes.append({
            "step_type": f"tool_call:{tool_name}",
            "input_hash": tool_args_hash,
            "output_hash": tool_result_hash,
            "chain_hash": call_chain,
            "tool_index": i,
        })
        current_chain = call_chain

    output_hash = _content_hash(run_output if run_output else "")
    output_chain = _chain_hash(current_chain, "run_output", output_hash)
    nodes.append({
        "step_type": "run_output",
        "content_hash": output_hash,
        "chain_hash": output_chain,
    })

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": "provenance",
        "data": {
            "agent_id": agent_id,
            "model_version": model_version,
            "input_hash": input_hash,
            "output_hash": output_hash,
            "chain_hash": output_chain,
            "chain_length": len(nodes),
            "nodes": nodes,
        },
    }


def verify_run_provenance(provenance_event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Verify the integrity of a run's provenance chain.

    Args:
        provenance_event: The provenance event from run_record.events

    Returns:
        Verification report with pass/fail per node.
    """
    data = provenance_event.get("data", {})
    nodes = data.get("nodes", [])

    if not nodes:
        return {"valid": False, "error": "No provenance nodes found"}

    results = []
    all_valid = True
    current_chain = GENESIS_HASH

    for node in nodes:
        step_type = node["step_type"]
        content_hash = node.get("content_hash") or node.get("output_hash", "")
        expected = _chain_hash(current_chain, step_type, content_hash)
        is_valid = expected == node["chain_hash"]

        if not is_valid:
            all_valid = False

        results.append({
            "step_type": step_type,
            "chain_hash": node["chain_hash"],
            "expected_hash": expected,
            "valid": is_valid,
        })
        current_chain = node["chain_hash"]

    return {
        "valid": all_valid,
        "chain_length": len(nodes),
        "nodes": results,
    }
