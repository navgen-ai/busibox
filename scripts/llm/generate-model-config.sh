#!/usr/bin/env bash
#
# Generate model_config.yml for LiteLLM/vLLM routing (non-interactive)
#
# EXECUTION CONTEXT:
#   - Proxmox host (recommended, as root)
#   - Admin workstation with SSH access to Proxmox-host-mounted repo
#
# PURPOSE:
#   Build provision/ansible/group_vars/all/model_config.yml from:
#   - model_registry.yml (purpose -> model mapping)
#   - local GPU inventory (nvidia-smi)
#   - existing model_config.yml (preserve technical metadata)
#
# INPUTS (optional env vars):
#   LLM_BACKEND           Default: auto-detect (vllm if nvidia-smi exists, else mlx)
#   LLM_TIER/MODEL_TIER   Memory tier (minimal/entry/standard/enhanced) — selects
#                          tier-appropriate models from model_registry.yml tiers section
#   GPU_COUNT             Override detected GPU count
#   MAX_VLLM_INSTANCES    Cap on assigned vLLM models (Docker=1, Proxmox=unlimited)
#   NETWORK_BASE_OCTETS   For informational output (e.g. 10.96.200)
#
# OUTPUT:
#   provision/ansible/group_vars/all/model_config.yml
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

REGISTRY_FILE="${REPO_ROOT}/provision/ansible/group_vars/all/model_registry.yml"
MODEL_CONFIG_FILE="${REPO_ROOT}/provision/ansible/group_vars/all/model_config.yml"

if [[ ! -f "${REGISTRY_FILE}" ]]; then
  echo "[ERROR] model_registry.yml not found: ${REGISTRY_FILE}" >&2
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "${PYTHON_BIN}" ]]; then
  if [[ -x "/opt/model-downloader/bin/python3" ]]; then
    PYTHON_BIN="/opt/model-downloader/bin/python3"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  else
    echo "[ERROR] python3 not found" >&2
    exit 1
  fi
fi

LLM_BACKEND="${LLM_BACKEND:-}"
if [[ -z "${LLM_BACKEND}" ]]; then
  if command -v nvidia-smi >/dev/null 2>&1; then
    LLM_BACKEND="vllm"
  else
    LLM_BACKEND="mlx"
  fi
fi

if [[ "${LLM_BACKEND}" != "vllm" ]]; then
  # For MLX/cloud backends, write a model_config.yml with purpose mappings only
  # (no GPU assignments or model entries). This keeps model_config.yml as the
  # single source of truth for what was deployed, regardless of backend.
  echo "[INFO] LLM_BACKEND=${LLM_BACKEND}; generating purpose-only model_config.yml" >&2

  export REPO_ROOT REGISTRY_FILE MODEL_CONFIG_FILE LLM_TIER="${LLM_TIER:-${MODEL_TIER:-}}" LLM_BACKEND

  "${PYTHON_BIN}" <<'PYEOF_MLX'
import os, sys
from pathlib import Path
try:
    import yaml
except Exception as exc:
    print(f"[ERROR] PyYAML unavailable: {exc}", file=sys.stderr)
    sys.exit(1)

registry_file = Path(os.environ["REGISTRY_FILE"])
model_config_file = Path(os.environ["MODEL_CONFIG_FILE"])
backend = os.environ.get("LLM_BACKEND", "mlx")

with registry_file.open("r", encoding="utf-8") as f:
    registry = yaml.safe_load(f) or {}

available_models = registry.get("available_models", {}) or {}
model_purposes = dict(registry.get("model_purposes", {}) or {})

# Apply tier-based overrides
llm_tier = os.environ.get("LLM_TIER", "").strip("'\"").strip()
if llm_tier:
    tiers = registry.get("tiers", {}) or {}
    tier_cfg = tiers.get(llm_tier, {})
    backend_models = tier_cfg.get(backend, {}) or {}
    if backend_models:
        print(f"[INFO] Applying tier '{llm_tier}' {backend} model overrides:", file=sys.stderr)
        for role, model_key in backend_models.items():
            old = model_purposes.get(role, "(unset)")
            model_purposes[role] = model_key
            print(f"[INFO]   {role}: {old} -> {model_key}", file=sys.stderr)

        # Propagate tier's agent/fast to common aliases
        tier_agent = backend_models.get("agent", "")
        tier_fast = backend_models.get("fast", "")
        propagate = {
            "default": tier_agent, "chat": tier_agent, "tool_calling": tier_agent,
            "parsing": tier_agent, "cleanup": tier_agent, "vision": tier_agent,
            "research": tier_agent, "classify": tier_fast, "test": tier_fast,
        }
        for role, fallback in propagate.items():
            if role not in backend_models and fallback:
                entry = available_models.get(fallback, {})
                provider = (entry.get("provider", "") or "").lower()
                if provider == backend or (provider == "" and backend == "mlx"):
                    old = model_purposes.get(role, "(unset)")
                    model_purposes[role] = fallback
                    print(f"[INFO]   {role}: {old} -> {fallback} (propagated from tier)", file=sys.stderr)
    elif llm_tier not in tiers:
        print(f"[WARN] Unknown tier '{llm_tier}' — using default model_purposes", file=sys.stderr)

# Apply PURPOSE_<ROLE> env overrides (highest priority)
for key, val in os.environ.items():
    if key.startswith("PURPOSE_"):
        role = key[len("PURPOSE_"):].lower()
        model_purposes[role] = val.strip("'\"")
        print(f"[INFO] Purpose override: {role} -> {val.strip(chr(39)+chr(34))}", file=sys.stderr)

# Build models section with unique model keys referenced by purposes
models = {}
for _purpose, model_key in model_purposes.items():
    if model_key in models:
        continue
    entry = available_models.get(model_key, {})
    if not entry:
        continue
    provider = entry.get("provider", backend)
    models[model_key] = {
        "provider": provider,
        "model_key": model_key,
        "model_name": entry.get("model_name", model_key),
        "assigned": True,
    }

result = {"models": models, "model_purposes": model_purposes}
model_config_file.parent.mkdir(parents=True, exist_ok=True)
with model_config_file.open("w", encoding="utf-8") as f:
    yaml.safe_dump(result, f, sort_keys=False)

print(f"[INFO] Wrote model_config.yml with {len(models)} {backend} model(s)", file=sys.stderr)
print(f"[INFO] Output: {model_config_file}", file=sys.stderr)
PYEOF_MLX

  echo "[SUCCESS] model_config.yml generation complete" >&2
  exit 0
fi

GPU_COUNT_ENV="${GPU_COUNT:-}"
if [[ -z "${GPU_COUNT_ENV}" ]]; then
  if command -v nvidia-smi >/dev/null 2>&1; then
    GPU_COUNT_ENV="$(nvidia-smi --query-gpu=index --format=csv,noheader 2>/dev/null | wc -l | tr -d ' ')"
  else
    GPU_COUNT_ENV="0"
  fi
fi

if [[ -z "${GPU_COUNT_ENV}" || "${GPU_COUNT_ENV}" -lt 1 ]]; then
  echo "[WARN] No GPUs detected; writing empty vLLM assignments." >&2
fi

echo "[INFO] Generating model_config.yml" >&2
echo "[INFO] Backend: ${LLM_BACKEND}" >&2
echo "[INFO] GPU count: ${GPU_COUNT_ENV}" >&2
echo "[INFO] Tier: ${LLM_TIER:-${MODEL_TIER:-unset}}" >&2
echo "[INFO] Network base: ${NETWORK_BASE_OCTETS:-unset}" >&2

export REPO_ROOT REGISTRY_FILE MODEL_CONFIG_FILE GPU_COUNT_ENV LLM_TIER="${LLM_TIER:-${MODEL_TIER:-}}" MAX_VLLM_INSTANCES="${MAX_VLLM_INSTANCES:-0}"

"${PYTHON_BIN}" <<'PYEOF'
import os
import re
import sys
from pathlib import Path
from typing import Dict, Any, List, Tuple

try:
    import yaml  # type: ignore
except Exception as exc:  # pragma: no cover
    print(f"[ERROR] PyYAML unavailable: {exc}", file=sys.stderr)
    sys.exit(1)

repo_root = Path(os.environ["REPO_ROOT"])
registry_file = Path(os.environ["REGISTRY_FILE"])
model_config_file = Path(os.environ["MODEL_CONFIG_FILE"])
gpu_count = int(os.environ.get("GPU_COUNT_ENV", "0") or "0")

with registry_file.open("r", encoding="utf-8") as f:
    registry = yaml.safe_load(f) or {}

existing: Dict[str, Any] = {}
if model_config_file.exists():
    with model_config_file.open("r", encoding="utf-8") as f:
        existing = yaml.safe_load(f) or {}

available_models: Dict[str, Any] = registry.get("available_models", {}) or {}
model_purposes: Dict[str, str] = registry.get("model_purposes", {}) or {}
existing_models: Dict[str, Any] = (existing.get("models", {}) or {})

# Apply tier-based model purpose overrides when LLM_TIER is set.
# Tier entries from model_registry.yml (e.g. tiers.entry.vllm) map roles to model
# keys. These override the default model_purposes for vLLM-backed roles, ensuring
# the selected models match the available VRAM.
llm_tier = os.environ.get("LLM_TIER", "").strip("'\"").strip()
if llm_tier:
    tiers = registry.get("tiers", {}) or {}
    tier_cfg = tiers.get(llm_tier, {})
    vllm_tier_models = tier_cfg.get("vllm", {}) or {}
    if vllm_tier_models:
        print(f"[INFO] Applying tier '{llm_tier}' vLLM model overrides:", file=sys.stderr)
        for role, model_key in vllm_tier_models.items():
            old = model_purposes.get(role, "(unset)")
            model_purposes[role] = model_key
            print(f"[INFO]   {role}: {old} -> {model_key}", file=sys.stderr)

        # For roles not explicitly in the tier, propagate the tier's agent/fast
        # model to cover common aliases (chat, tool_calling, parsing, etc.)
        # This avoids deploying models that won't fit in the available VRAM.
        tier_agent = vllm_tier_models.get("agent", "")
        tier_fast = vllm_tier_models.get("fast", "")
        propagate_roles = {
            "default": tier_agent,
            "chat": tier_agent,
            "tool_calling": tier_agent,
            "parsing": tier_agent,
            "cleanup": tier_agent,
            "vision": tier_agent,
            "research": tier_agent,
            "classify": tier_fast,
            "test": tier_fast,
        }
        for role, fallback_key in propagate_roles.items():
            if role not in vllm_tier_models and fallback_key:
                entry = available_models.get(fallback_key, {})
                if entry.get("provider", "").lower() == "vllm":
                    old = model_purposes.get(role, "(unset)")
                    model_purposes[role] = fallback_key
                    print(f"[INFO]   {role}: {old} -> {fallback_key} (propagated from tier)", file=sys.stderr)
    elif llm_tier not in tiers:
        print(f"[WARN] Unknown tier '{llm_tier}' — using default model_purposes", file=sys.stderr)
    else:
        print(f"[INFO] Tier '{llm_tier}' has no vLLM-specific models — using defaults", file=sys.stderr)

# Apply PURPOSE_<ROLE>=<model_key> overrides from environment (highest priority).
# e.g. PURPOSE_AGENT=qwen3.6-35b-a3b-vllm-fp8 -> agent: qwen3.6-35b-a3b-vllm-fp8
# e.g. PURPOSE_FAST=qwen3.5-0.8b-vllm        -> fast:  qwen3.5-0.8b-vllm
for key, val in os.environ.items():
    if key.startswith("PURPOSE_"):
        role = key[len("PURPOSE_"):].lower()
        model_purposes[role] = val.strip("'\"")
        print(f"[INFO] Purpose override: {role} -> {val.strip(chr(39)+chr(34))}", file=sys.stderr)

def estimate_params_billions(model_key: str, model_name: str) -> float:
    joined = f"{model_key} {model_name}".lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*b", joined)
    if m:
        return float(m.group(1))
    # Fallbacks for known tiny models
    if "0.6b" in joined:
        return 0.6
    if "phi-4" in joined:
        return 4.0
    return 12.0

def classify_size(model_key: str, model_name: str) -> str:
    return "small" if estimate_params_billions(model_key, model_name) < 10 else "large"

def _normalize_model_key(key: str) -> str:
    return key.replace(".", "-").replace("_", "-").lower()

def get_explicit_gpu_assignments() -> Dict[str, str]:
    """Read GPU_ASSIGN_<MODEL_KEY>=<GPU> from environment variables."""
    assignments: Dict[str, str] = {}
    for key, val in os.environ.items():
        if key.startswith("GPU_ASSIGN_"):
            model_key = key[len("GPU_ASSIGN_"):].lower().replace("_", "-")
            assignments[model_key] = val.strip("'\"")
    return assignments

def get_explicit_tp_overrides() -> Dict[str, int]:
    """Read GPU_TP_<MODEL_KEY>=<tp_count> from environment variables."""
    overrides: Dict[str, int] = {}
    for key, val in os.environ.items():
        if key.startswith("GPU_TP_"):
            model_key = key[len("GPU_TP_"):].lower().replace("_", "-")
            try:
                overrides[model_key] = int(val.strip("'\""))
            except ValueError:
                pass
    return overrides

def get_registry_tp(entry: Dict[str, Any]) -> int | None:
    """Read tensor_parallel_size from a registry entry, if set."""
    tp = entry.get("tensor_parallel_size")
    if tp is None:
        return None
    try:
        tp_int = int(tp)
        return tp_int if tp_int > 0 else None
    except (TypeError, ValueError):
        return None

def find_explicit_gpu(model_key: str, explicit: Dict[str, str]) -> str | None:
    """Match a model key against explicit GPU assignments, handling naming variants."""
    if model_key in explicit:
        return explicit[model_key]
    normalized = _normalize_model_key(model_key)
    for k, v in explicit.items():
        if _normalize_model_key(k) == normalized:
            return v
    return None

def find_explicit_tp(model_key: str, tp_overrides: Dict[str, int]) -> int | None:
    """Match a model key against explicit TP overrides."""
    if model_key in tp_overrides:
        return tp_overrides[model_key]
    normalized = _normalize_model_key(model_key)
    for k, v in tp_overrides.items():
        if _normalize_model_key(k) == normalized:
            return v
    return None

def assign_models(
    vllm_models: List[Tuple[str, Dict[str, Any]]],
    gpus: int,
    max_instances: int = 0,
    purposes: Dict[str, str] | None = None,
) -> Dict[str, Dict[str, Any]]:
    assigned: Dict[str, Dict[str, Any]] = {}
    port = 8000
    explicit = get_explicit_gpu_assignments()
    tp_overrides = get_explicit_tp_overrides()

    # First pass: assign models with explicit GPU assignments.
    # Precedence for tensor_parallel: GPU_TP_<KEY> env > #GPUs in GPU_ASSIGN
    # > registry's tensor_parallel_size > 1.
    explicitly_assigned = set()
    for model_key, entry in vllm_models:
        gpu_val = find_explicit_gpu(model_key, explicit)
        if gpu_val is not None:
            tp_override = find_explicit_tp(model_key, tp_overrides)
            if tp_override is not None:
                tp = tp_override
            elif "," in gpu_val:
                tp = len(gpu_val.split(","))
            else:
                tp = get_registry_tp(entry) or 1
            assigned[model_key] = {"gpu": gpu_val, "port": port, "tensor_parallel": tp}
            port += 1
            explicitly_assigned.add(model_key)

    def _at_cap() -> bool:
        return max_instances > 0 and len(assigned) >= max_instances

    # Second pass: auto-assign remaining models. Models that declare
    # tensor_parallel_size in the registry will reserve that many contiguous
    # GPUs starting from the highest unused index.
    remaining = [(k, e) for k, e in vllm_models if k not in explicitly_assigned]

    # Track which GPU indices are already claimed by explicitly-assigned models
    # so the auto-allocator doesn't double-book them.
    used_gpus: set[int] = set()
    for cfg in assigned.values():
        for g in str(cfg.get("gpu", "")).split(","):
            g = g.strip()
            if g.isdigit():
                used_gpus.add(int(g))

    def _claim_gpus(count: int, prefer_high: bool = True) -> str | None:
        """Reserve `count` contiguous-by-listing free GPUs; return CSV or None."""
        free = [i for i in range(gpus) if i not in used_gpus]
        if len(free) < count:
            return None
        chosen = free[-count:] if prefer_high else free[:count]
        for g in chosen:
            used_gpus.add(g)
        return ",".join(str(g) for g in chosen)

    # When limited to a single instance, directly assign the model that serves
    # the most important purpose (agent > default > chat) regardless of size.
    if max_instances == 1 and not _at_cap() and purposes and len(remaining) > 1:
        priority_key = None
        priority_entry: Dict[str, Any] = {}
        for role in ("agent", "default", "chat"):
            candidate = purposes.get(role, "")
            for k, e in remaining:
                if k == candidate:
                    priority_key = k
                    priority_entry = e
                    break
            if priority_key:
                break
        if priority_key:
            tp_hint = get_registry_tp(priority_entry) or 1
            gpu_csv = _claim_gpus(tp_hint, prefer_high=False) or "0"
            assigned[priority_key] = {"gpu": gpu_csv, "port": port, "tensor_parallel": tp_hint}
            port += 1
            remaining = [(k, e) for k, e in remaining if k != priority_key]

    small = []
    large = []
    for model_key, entry in remaining:
        model_name = entry.get("model_name", "")
        (small if classify_size(model_key, model_name) == "small" else large).append((model_key, entry))

    # Small models: prefer GPU 0, then 1. Always TP=1.
    small_index = 0
    for model_key, entry in small:
        if gpus <= 0 or _at_cap():
            break
        if small_index == 0 and 0 not in used_gpus:
            gpu = "0"
        elif gpus >= 2 and 1 not in used_gpus:
            gpu = "1"
        else:
            # Fall back to any free GPU, otherwise share GPU 0.
            free = [i for i in range(gpus) if i not in used_gpus]
            gpu = str(free[0]) if free else "0"
        assigned[model_key] = {"gpu": gpu, "port": port, "tensor_parallel": 1}
        port += 1
        small_index += 1

    # Large models: honour registry-declared tensor_parallel_size; otherwise
    # fall back to the legacy heuristic (TP=2 when 4+ GPUs available).
    for model_key, entry in large:
        if _at_cap():
            break
        registry_tp = get_registry_tp(entry)
        if registry_tp and registry_tp >= 1:
            gpu_csv = _claim_gpus(registry_tp, prefer_high=True)
            if gpu_csv is None:
                # Not enough free GPUs; skip rather than misconfigure
                print(
                    f"[WARN] Cannot place {model_key}: needs TP={registry_tp} GPUs "
                    f"but only {sum(1 for i in range(gpus) if i not in used_gpus)} free.",
                    file=sys.stderr,
                )
                continue
            assigned[model_key] = {
                "gpu": gpu_csv,
                "port": port,
                "tensor_parallel": registry_tp,
            }
        elif gpus >= 4:
            assigned[model_key] = {"gpu": "2,3", "port": port, "tensor_parallel": 2}
            used_gpus.update({2, 3})
        elif gpus >= 3:
            assigned[model_key] = {"gpu": "2", "port": port, "tensor_parallel": 1}
            used_gpus.add(2)
        elif gpus >= 2:
            assigned[model_key] = {"gpu": "1", "port": port, "tensor_parallel": 1}
            used_gpus.add(1)
        elif gpus == 1:
            assigned[model_key] = {"gpu": "0", "port": port, "tensor_parallel": 1}
            used_gpus.add(0)
        else:
            continue
        port += 1
    return assigned

# Build unique list of vLLM model keys referenced by production purposes
vllm_model_keys = []
for _purpose, model_key in model_purposes.items():
    if model_key in vllm_model_keys:
        continue
    model_entry = available_models.get(model_key, {})
    if (model_entry.get("provider", "") or "").lower() == "vllm":
        vllm_model_keys.append(model_key)

vllm_entries = [(k, available_models.get(k, {})) for k in vllm_model_keys]
max_vllm_instances = int(os.environ.get("MAX_VLLM_INSTANCES", "0") or "0")
if max_vllm_instances > 0:
    print(f"[INFO] MAX_VLLM_INSTANCES={max_vllm_instances} — limiting assigned models", file=sys.stderr)
routing = assign_models(vllm_entries, gpu_count, max_instances=max_vllm_instances, purposes=model_purposes)

# When capped (e.g. Docker), remap unassigned vLLM purposes to an assigned model
if max_vllm_instances > 0 and routing:
    assigned_keys = set(routing.keys())
    # Pick the best assigned model: prefer "agent" or "default" purpose model, else first assigned
    primary_key = None
    for preferred in ("agent", "default", "chat"):
        candidate = model_purposes.get(preferred, "")
        if candidate in assigned_keys:
            primary_key = candidate
            break
    if not primary_key:
        primary_key = next(iter(assigned_keys))
    remapped = 0
    for purpose, mk in list(model_purposes.items()):
        if mk in vllm_model_keys and mk not in assigned_keys:
            model_purposes[purpose] = primary_key
            remapped += 1
    if remapped:
        print(f"[INFO] Remapped {remapped} purpose(s) to assigned model '{primary_key}'", file=sys.stderr)
    # Remove unassigned vLLM models from the build list
    vllm_model_keys = [k for k in vllm_model_keys if k in assigned_keys]

output_models: Dict[str, Any] = {}

# Collect model_keys that are part of the CURRENT registry purpose set.
# Also build a set of model_keys from existing entries (keyed by model_key).
current_model_keys = set(vllm_model_keys)

# Preserve existing entries, re-keying vLLM entries by model_key to migrate
# from old HF-name-keyed format.  Stale vLLM entries (not in current purpose
# set) are dropped to avoid port collisions.
for entry_key, cfg in existing_models.items():
    cfg = dict(cfg or {})
    is_vllm = cfg.get("provider", "").lower() == "vllm"
    mk = cfg.get("model_key", entry_key)
    if is_vllm and mk not in current_model_keys:
        print(f"[INFO] Removing stale vLLM entry: {entry_key}", file=sys.stderr)
        continue
    # Re-key vLLM entries by their model_key field (migrates old HF-name keys)
    canonical_key = mk if is_vllm else entry_key
    if canonical_key in output_models:
        continue  # already seen under canonical key
    output_models[canonical_key] = cfg

for model_key in vllm_model_keys:
    entry = available_models.get(model_key, {}) or {}
    model_name = entry.get("model_name", "")
    if not model_name:
        continue
    merged = dict(output_models.get(model_key, {}))
    merged["provider"] = "vllm"
    merged["model_key"] = model_key
    merged["model_name"] = model_name

    # Preserve technical/tuning hints from registry when present.
    # These get picked up by provision/ansible/roles/vllm_<port>/tasks/main.yml
    # and rendered into the systemd service template (vllm.service.j2).
    for key in (
        "gpu_memory_utilization",
        "max_model_len",
        "max_num_seqs",
        "max_num_batched_tokens",
        "cpu_offload_gb",
        "hf_overrides",
        "tool_calling",
        "tool_call_parser",
        "tool_chat_template",
        # Newer vLLM tunables (Qwen3.6 / FP8 MoE recipes)
        "tensor_parallel_size",
        "kv_cache_dtype",
        "reasoning_parser",
        "enable_chunked_prefill",
        "enable_prefix_caching",
        "enable_expert_parallel",
        "multimodal",
        "limit_mm_per_prompt",
    ):
        if key in entry and entry[key] is not None:
            merged[key] = entry[key]

    # served_model_name: the "model" field from registry (what API clients see)
    served = entry.get("model", "")
    if served:
        merged["served_model_name"] = served

    assigned = routing.get(model_key)
    if assigned:
        merged["assigned"] = True
        merged["gpu"] = assigned["gpu"]
        merged["port"] = int(assigned["port"])
        merged["tensor_parallel"] = int(assigned["tensor_parallel"])
    else:
        merged["assigned"] = False
        merged.pop("gpu", None)
        merged.pop("port", None)
        merged.pop("tensor_parallel", None)

    output_models[model_key] = merged

result = {"models": output_models, "model_purposes": dict(model_purposes)}
model_config_file.parent.mkdir(parents=True, exist_ok=True)
with model_config_file.open("w", encoding="utf-8") as f:
    yaml.safe_dump(result, f, sort_keys=False)

assigned_count = sum(
    1 for key in vllm_model_keys
    if key in output_models and output_models[key].get("assigned") is True
)
print(f"[INFO] Wrote model_config.yml with {assigned_count} assigned vLLM model(s)", file=sys.stderr)
print(f"[INFO] Output: {model_config_file}", file=sys.stderr)
PYEOF

echo "[SUCCESS] model_config.yml generation complete" >&2
