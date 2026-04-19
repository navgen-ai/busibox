#!/bin/bash
# vLLM Docker entrypoint - builds command line from environment variables.
#
# In production these env vars are set from model_config.yml (which is itself
# generated from provision/ansible/group_vars/all/model_registry.yml). Do not
# hardcode model defaults here -- treat the registry as the source of truth.
#
# Required env vars:
#   VLLM_MODEL          - HuggingFace model name (e.g. Qwen/Qwen3.6-35B-A3B-FP8)
#
# Optional env vars:
#   VLLM_SERVED_MODEL_NAME       - Name exposed via OpenAI-compatible API
#   VLLM_MAX_MODEL_LEN           - Maximum context length (default: 8192)
#   VLLM_GPU_MEMORY_UTILIZATION  - Fraction of GPU memory to use (default: 0.9)
#   VLLM_TENSOR_PARALLEL_SIZE    - Number of GPUs for tensor parallelism (default: 1)
#   VLLM_QUANTIZATION            - Quantization method (e.g. awq, gptq, fp8)
#   VLLM_TOOL_CALL_PARSER        - Tool call parser (e.g. hermes, qwen3_coder)
#   VLLM_TOOL_CHAT_TEMPLATE      - Path to custom chat template
#   VLLM_MAX_NUM_SEQS            - Max concurrent sequences (default: 32)
#   VLLM_MAX_NUM_BATCHED_TOKENS  - Token batch budget for chunked prefill
#   VLLM_CPU_OFFLOAD_GB          - CPU offload in GB (default: 0)
#   VLLM_PORT                    - Listen port (default: 8000)
#   VLLM_EXTRA_ARGS              - Additional vLLM arguments (space-separated)
#   VLLM_KV_CACHE_DTYPE          - e.g. "fp8" for the Qwen3.6 FP8 MoE recipe
#   VLLM_REASONING_PARSER        - e.g. "qwen3" so vLLM splits <think> blocks
#   VLLM_ENABLE_CHUNKED_PREFILL  - Set to "1"/"true" to enable chunked prefill
#   VLLM_ENABLE_PREFIX_CACHING   - Set to "1"/"true" to enable prefix caching
#   VLLM_ENABLE_EXPERT_PARALLEL  - Set to "1"/"true" for MoE expert parallel
set -euo pipefail

if [[ -z "${VLLM_MODEL:-}" ]]; then
    echo "[ERROR] VLLM_MODEL environment variable is required" >&2
    exit 1
fi

CMD_ARGS=(
    --model "${VLLM_MODEL}"
    --host 0.0.0.0
    --port "${VLLM_PORT:-8000}"
    --max-model-len "${VLLM_MAX_MODEL_LEN:-8192}"
    --gpu-memory-utilization "${VLLM_GPU_MEMORY_UTILIZATION:-0.9}"
    --tensor-parallel-size "${VLLM_TENSOR_PARALLEL_SIZE:-1}"
    --max-num-seqs "${VLLM_MAX_NUM_SEQS:-32}"
)

if [[ -n "${VLLM_SERVED_MODEL_NAME:-}" ]]; then
    CMD_ARGS+=(--served-model-name "${VLLM_SERVED_MODEL_NAME}")
fi

if [[ -n "${VLLM_QUANTIZATION:-}" ]]; then
    CMD_ARGS+=(--quantization "${VLLM_QUANTIZATION}")
fi

if [[ -n "${VLLM_TOOL_CALL_PARSER:-}" ]]; then
    CMD_ARGS+=(--enable-auto-tool-choice --tool-call-parser "${VLLM_TOOL_CALL_PARSER}")
fi

if [[ -n "${VLLM_TOOL_CHAT_TEMPLATE:-}" ]]; then
    CMD_ARGS+=(--chat-template "${VLLM_TOOL_CHAT_TEMPLATE}")
fi

if [[ "${VLLM_CPU_OFFLOAD_GB:-0}" != "0" ]]; then
    CMD_ARGS+=(--cpu-offload-gb "${VLLM_CPU_OFFLOAD_GB}")
fi

if [[ -n "${VLLM_MAX_NUM_BATCHED_TOKENS:-}" ]]; then
    CMD_ARGS+=(--max-num-batched-tokens "${VLLM_MAX_NUM_BATCHED_TOKENS}")
fi

if [[ -n "${VLLM_KV_CACHE_DTYPE:-}" ]]; then
    CMD_ARGS+=(--kv-cache-dtype "${VLLM_KV_CACHE_DTYPE}")
fi

if [[ -n "${VLLM_REASONING_PARSER:-}" ]]; then
    CMD_ARGS+=(--enable-reasoning --reasoning-parser "${VLLM_REASONING_PARSER}")
fi

# Boolean-style flags. Accept 1/true/yes (case-insensitive).
_is_truthy() {
    case "${1,,}" in
        1|true|yes|on) return 0 ;;
        *) return 1 ;;
    esac
}

if _is_truthy "${VLLM_ENABLE_CHUNKED_PREFILL:-}"; then
    CMD_ARGS+=(--enable-chunked-prefill)
fi

if _is_truthy "${VLLM_ENABLE_PREFIX_CACHING:-}"; then
    CMD_ARGS+=(--enable-prefix-caching)
fi

if _is_truthy "${VLLM_ENABLE_EXPERT_PARALLEL:-}"; then
    CMD_ARGS+=(--enable-expert-parallel)
fi

# Append any extra args
if [[ -n "${VLLM_EXTRA_ARGS:-}" ]]; then
    read -ra extra <<< "${VLLM_EXTRA_ARGS}"
    CMD_ARGS+=("${extra[@]}")
fi

# Clean up .no_exist sentinel directories that prevent offline model loading.
# HF hub creates these when it looks up files missing from a cached snapshot;
# on subsequent offline starts they cause LocalEntryNotFoundError even though
# the core model files are present.
_hf_hub="${HF_HOME:-/root/.cache/huggingface}/hub"
if [ -d "$_hf_hub" ]; then
  _removed=$(find "$_hf_hub" -type d -name .no_exist -print -exec rm -rf {} + 2>/dev/null || true)
  if [ -n "$_removed" ]; then
    echo "[vllm-entrypoint] Cleaned stale .no_exist cache entries" >&2
  fi
fi

echo "[vllm-entrypoint] Starting vLLM with model: ${VLLM_MODEL}" >&2
echo "[vllm-entrypoint] Args: ${CMD_ARGS[*]}" >&2

exec python3 -m vllm.entrypoints.openai.api_server "${CMD_ARGS[@]}"
