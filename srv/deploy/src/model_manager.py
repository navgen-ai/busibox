"""
Model lifecycle helpers for vLLM/LiteLLM management.

This module centralizes:
- model registry browsing
- GPU detection on vLLM host
- model assignment read/write in model_config.yml
- lightweight auto-assignment heuristic
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml


def busibox_root() -> Path:
    return Path(os.getenv("BUSIBOX_HOST_PATH", "/root/busibox"))


def model_registry_path() -> Path:
    return busibox_root() / "provision/ansible/group_vars/all/model_registry.yml"


def model_overrides_path() -> Path:
    return busibox_root() / "provision/ansible/group_vars/all/model_overrides.yml"


def model_config_path() -> Path:
    return busibox_root() / "provision/ansible/group_vars/all/model_config.yml"


def vllm_host() -> str:
    return os.getenv("VLLM_HOST", "").strip()


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def _write_yaml(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)


def load_registry_with_overrides() -> Dict[str, Any]:
    base = _read_yaml(model_registry_path())
    overrides = _read_yaml(model_overrides_path())
    if not overrides:
        return base

    merged = dict(base)
    base_models = dict(base.get("available_models", {}) or {})
    override_models = dict(overrides.get("available_models", {}) or {})
    base_models.update(override_models)
    merged["available_models"] = base_models

    base_purposes = dict(base.get("model_purposes", {}) or {})
    override_purposes = dict(overrides.get("model_purposes", {}) or {})
    base_purposes.update(override_purposes)
    merged["model_purposes"] = base_purposes
    return merged


def load_model_config() -> Dict[str, Any]:
    data = _read_yaml(model_config_path())
    if "models" not in data or not isinstance(data["models"], dict):
        data["models"] = {}
    return data


def save_model_config(data: Dict[str, Any]) -> None:
    if "models" not in data or not isinstance(data["models"], dict):
        data["models"] = {}
    _write_yaml(model_config_path(), data)


async def ssh_exec_raw(host: str, command: str, timeout: int = 30) -> Tuple[int, str, str]:
    if not host:
        return 1, "", "VLLM_HOST not configured"
    proc = await asyncio.create_subprocess_exec(
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "ConnectTimeout=10",
        host,
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 1, "", f"ssh timeout after {timeout}s"
    return proc.returncode or 0, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")


async def detect_vllm_gpus() -> List[Dict[str, Any]]:
    host = vllm_host()
    cmd = (
        "nvidia-smi --query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu "
        "--format=csv,noheader,nounits"
    )
    code, out, _err = await ssh_exec_raw(host, cmd, timeout=20)
    if code != 0:
        return []
    reader = csv.reader(io.StringIO(out))
    gpus: List[Dict[str, Any]] = []
    for row in reader:
        if len(row) < 6:
            continue
        try:
            idx, name, total, used, free, util = [x.strip() for x in row]
            gpus.append(
                {
                    "index": int(idx),
                    "name": name,
                    "memory_total_mb": float(total),
                    "memory_used_mb": float(used),
                    "memory_free_mb": float(free),
                    "utilization_pct": float(util),
                }
            )
        except Exception:
            continue
    return gpus


async def _list_cached_models_vllm() -> List[str]:
    host = vllm_host()
    cmd = (
        "python3 - <<'PY'\n"
        "import glob, os\n"
        "root='/var/lib/llm-models/huggingface/hub'\n"
        "vals=[]\n"
        "for p in glob.glob(os.path.join(root,'models--*')):\n"
        "  name=os.path.basename(p).replace('models--','').replace('--','/')\n"
        "  vals.append(name)\n"
        "print('\\n'.join(sorted(vals)))\n"
        "PY"
    )
    code, out, _err = await ssh_exec_raw(host, cmd, timeout=30)
    if code != 0:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _resolve_host_agent_token() -> str:
    """Resolve host-agent token, checking env file as fallback."""
    token = os.getenv("HOST_AGENT_TOKEN", "")
    if token:
        return token
    busibox_path = os.getenv("BUSIBOX_HOST_PATH", "")
    prefix = os.getenv("CONTAINER_PREFIX", "dev")
    if busibox_path:
        env_file = os.path.join(busibox_path, f".env.{prefix}")
        try:
            with open(env_file) as f:
                for line in f:
                    if line.startswith("HOST_AGENT_TOKEN="):
                        return line.split("=", 1)[1].strip()
        except FileNotFoundError:
            pass
    return ""


async def _list_cached_models_mlx() -> List[str]:
    """Query host-agent for cached models on an MLX (Apple Silicon) system."""
    import httpx

    host_agent_url = os.getenv("HOST_AGENT_URL", "http://host.docker.internal:8089")
    token = _resolve_host_agent_token()

    headers: Dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{host_agent_url}/mlx/models", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                cached: List[str] = []
                for tier in data.get("tiers", []):
                    for _role, model_info in (tier.get("models", {}) or {}).items():
                        name = model_info.get("name", "")
                        if name and model_info.get("cached"):
                            if name not in cached:
                                cached.append(name)
                return sorted(cached)
    except Exception:
        pass
    return []


async def list_cached_models() -> List[str]:
    backend = os.getenv("LLM_BACKEND", "").lower()
    if backend == "mlx":
        return await _list_cached_models_mlx()
    return await _list_cached_models_vllm()


def _estimate_params_b(model_key: str, model_name: str) -> float:
    joined = f"{model_key} {model_name}".lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*b", joined)
    if m:
        return float(m.group(1))
    if "phi-4" in joined:
        return 4.0
    if "0.6b" in joined:
        return 0.6
    return 12.0


def _size_class(model_key: str, model_name: str) -> str:
    return "small" if _estimate_params_b(model_key, model_name) < 10 else "large"


def _assigned_entry(gpu: str, port: int, tp: int) -> Dict[str, Any]:
    return {"gpu": gpu, "port": int(port), "tensor_parallel": int(tp), "assigned": True}


def auto_assign_models(registry: Dict[str, Any], gpu_count: int, existing: Dict[str, Any] | None = None) -> Dict[str, Any]:
    existing = existing or {"models": {}}
    existing_models = dict(existing.get("models", {}) or {})
    available = dict(registry.get("available_models", {}) or {})
    purposes = dict(registry.get("model_purposes", {}) or {})

    vllm_keys: List[str] = []
    for _purpose, model_key in purposes.items():
        if model_key in vllm_keys:
            continue
        entry = available.get(model_key, {})
        if (entry.get("provider", "") or "").lower() == "vllm":
            vllm_keys.append(model_key)

    small: List[str] = []
    large: List[str] = []
    for key in vllm_keys:
        entry = available.get(key, {})
        model_name = entry.get("model_name", "")
        (small if _size_class(key, model_name) == "small" else large).append(key)

    routing: Dict[str, Dict[str, Any]] = {}
    port = 8000

    for i, key in enumerate(small):
        if gpu_count <= 0:
            break
        if i == 0:
            gpu = "0"
        elif gpu_count >= 2:
            gpu = "1"
        else:
            gpu = "0"
        routing[key] = _assigned_entry(gpu, port, 1)
        port += 1

    for key in large:
        if gpu_count >= 4:
            routing[key] = _assigned_entry("2,3", port, 2)
        elif gpu_count >= 3:
            routing[key] = _assigned_entry("2", port, 1)
        elif gpu_count >= 2:
            routing[key] = _assigned_entry("1", port, 1)
        elif gpu_count == 1:
            routing[key] = _assigned_entry("0", port, 1)
        else:
            continue
        port += 1

    out_models = dict(existing_models)
    for key in vllm_keys:
        entry = available.get(key, {})
        model_name = entry.get("model_name")
        if not model_name:
            continue
        merged = dict(out_models.get(model_name, {}))
        merged["provider"] = "vllm"
        merged["model_key"] = key
        for cfg_key in (
            "gpu_memory_utilization",
            "max_model_len",
            "max_num_seqs",
            "cpu_offload_gb",
            "hf_overrides",
            "tool_calling",
            "tool_call_parser",
            "tool_chat_template",
        ):
            if cfg_key in entry:
                merged[cfg_key] = entry[cfg_key]
        assigned = routing.get(key)
        if assigned:
            merged.update(assigned)
        else:
            merged["assigned"] = False
            merged.pop("gpu", None)
            merged.pop("port", None)
            merged.pop("tensor_parallel", None)
        out_models[model_name] = merged

    return {"models": out_models}


def get_assignments(config_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for model_name, cfg in (config_data.get("models", {}) or {}).items():
        if (cfg.get("provider", "") or "").lower() != "vllm":
            continue
        rows.append(
            {
                "model_name": model_name,
                "model_key": cfg.get("model_key"),
                "assigned": bool(cfg.get("assigned", False)),
                "gpu": cfg.get("gpu"),
                "port": cfg.get("port"),
                "tensor_parallel": cfg.get("tensor_parallel", 1),
            }
        )
    rows.sort(key=lambda x: (0 if x.get("assigned") else 1, x.get("port") or 9999, x["model_name"]))
    return rows


def update_assignment(
    config_data: Dict[str, Any],
    registry: Dict[str, Any],
    model_key: str,
    gpu_ids: List[int],
    port: int | None,
    tensor_parallel: int | None,
) -> Dict[str, Any]:
    available = dict(registry.get("available_models", {}) or {})
    model_entry = dict(available.get(model_key, {}) or {})
    if not model_entry:
        raise ValueError(f"Unknown model_key: {model_key}")
    if (model_entry.get("provider", "") or "").lower() != "vllm":
        raise ValueError(f"Model {model_key} is not a vLLM model")
    model_name = model_entry.get("model_name")
    if not model_name:
        raise ValueError(f"Model {model_key} missing model_name")

    models = dict(config_data.get("models", {}) or {})
    current_ports = {
        int(cfg.get("port"))
        for _name, cfg in models.items()
        if (cfg.get("provider", "") or "").lower() == "vllm"
        and bool(cfg.get("assigned", False))
        and cfg.get("port") is not None
        and _name != model_name
    }
    if port is None:
        for p in range(8000, 8006):
            if p not in current_ports:
                port = p
                break
    if port is None:
        raise ValueError("No available vLLM port in range 8000-8005")
    if port in current_ports:
        raise ValueError(f"Port {port} already assigned")
    if not gpu_ids:
        raise ValueError("gpu_ids must not be empty")
    if tensor_parallel is None:
        tensor_parallel = max(1, len(gpu_ids))

    cfg = dict(models.get(model_name, {}))
    cfg["provider"] = "vllm"
    cfg["model_key"] = model_key
    cfg["assigned"] = True
    cfg["gpu"] = ",".join(str(x) for x in gpu_ids)
    cfg["port"] = int(port)
    cfg["tensor_parallel"] = int(tensor_parallel)
    models[model_name] = cfg
    config_data["models"] = models
    return config_data


def unassign_model(config_data: Dict[str, Any], registry: Dict[str, Any], model_key: str) -> Dict[str, Any]:
    available = dict(registry.get("available_models", {}) or {})
    model_entry = dict(available.get(model_key, {}) or {})
    if not model_entry:
        raise ValueError(f"Unknown model_key: {model_key}")
    model_name = model_entry.get("model_name")
    if not model_name:
        raise ValueError(f"Model {model_key} missing model_name")
    models = dict(config_data.get("models", {}) or {})
    cfg = dict(models.get(model_name, {}))
    cfg["provider"] = "vllm"
    cfg["model_key"] = model_key
    cfg["assigned"] = False
    cfg.pop("gpu", None)
    cfg.pop("port", None)
    cfg.pop("tensor_parallel", None)
    models[model_name] = cfg
    config_data["models"] = models
    return config_data


async def _list_active_models_vllm(host: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not host:
        return rows
    host_ip = host.split("@")[-1] if "@" in host else host
    import httpx

    for port in range(8000, 8006):
        row = {"port": port, "running": False, "healthy": False, "model": None}
        code, out, _err = await ssh_exec_raw(host, f"systemctl is-active vllm-{port}", timeout=8)
        row["running"] = code == 0 and out.strip() == "active"
        if row["running"]:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(f"http://{host_ip}:{port}/v1/models")
                    if resp.status_code == 200:
                        payload = resp.json()
                        data = payload.get("data", [])
                        row["healthy"] = True
                        if data:
                            row["model"] = data[0].get("id")
            except Exception:
                pass
        rows.append(row)
    return rows


async def _list_active_models_mlx() -> List[Dict[str, Any]]:
    """Query host-agent /mlx/status?target=all for active MLX servers."""
    import httpx

    host_agent_url = os.getenv("HOST_AGENT_URL", "http://host.docker.internal:8089")
    token = _resolve_host_agent_token()

    headers: Dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    rows: List[Dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{host_agent_url}/mlx/status",
                params={"target": "all"},
                headers=headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                for target in ("primary", "fast"):
                    srv = data.get(target, {})
                    if srv:
                        rows.append({
                            "port": srv.get("port"),
                            "running": srv.get("running", False),
                            "healthy": srv.get("healthy", False),
                            "model": srv.get("model"),
                        })
    except Exception:
        pass
    return rows


async def list_active_models(host: str) -> List[Dict[str, Any]]:
    backend = os.getenv("LLM_BACKEND", "").lower()
    if backend == "mlx":
        return await _list_active_models_mlx()
    return await _list_active_models_vllm(host)


async def run_make_install_litellm() -> Tuple[int, str]:
    root = busibox_root()
    cmd = f"cd {root} && USE_MANAGER=0 make install SERVICE=litellm"
    proc = await asyncio.create_subprocess_exec(
        "bash",
        "-lc",
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    combined = (out or b"").decode("utf-8", errors="replace") + (err or b"").decode("utf-8", errors="replace")
    return proc.returncode or 0, combined


async def remote_download_model(model_name: str) -> Tuple[int, str]:
    host = vllm_host()
    escaped = json.dumps(model_name)
    cmd = (
        "python3 - <<'PY'\n"
        "from huggingface_hub import snapshot_download\n"
        f"snapshot_download({escaped}, local_dir_use_symlinks=True)\n"
        "print('download complete')\n"
        "PY"
    )
    code, out, err = await ssh_exec_raw(host, cmd, timeout=1800)
    return code, out + err


async def restart_vllm_service(port: int) -> Tuple[int, str]:
    host = vllm_host()
    return await ssh_exec_raw(host, f"sudo systemctl restart vllm-{int(port)} && systemctl is-active vllm-{int(port)}", timeout=120)
