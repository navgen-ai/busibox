"""
Tool calling load tests for the agent-api.

Exercises the /runs/invoke endpoint with tool-using agents at configurable
concurrency to stress-test data-api interactions and verify the system
stays stable under tool-heavy parallel workloads.

Usage:
    AUTH_TOKEN=<jwt> pytest tests/load/tool_load_test.py -v -s
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional

import httpx
import pytest

from .conftest import AGENT_API_URL, AUTH_TOKEN, TOOL_TEST_QUERIES

logger = logging.getLogger(__name__)

CONCURRENCY_LEVELS = [1, 2, 4, 8]


@dataclass
class RunMetrics:
    """Metrics for a single /runs/invoke request."""
    total_latency_ms: float = 0.0
    error: Optional[str] = None
    output_length: int = 0
    status_code: int = 0


@dataclass
class ToolLoadResult:
    """Aggregate metrics for tool load test at a given concurrency."""
    concurrency: int = 0
    total_requests: int = 0
    successful: int = 0
    failed: int = 0
    error_rate: float = 0.0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    latency_p99_ms: float = 0.0
    throughput_rps: float = 0.0
    errors: List[str] = field(default_factory=list)


def percentile(data: List[float], pct: float) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = min(int(len(sorted_data) * pct / 100), len(sorted_data) - 1)
    return sorted_data[idx]


async def send_run_invoke(
    client: httpx.AsyncClient,
    url: str,
    headers: dict,
    query: str,
) -> RunMetrics:
    """Send a single /runs/invoke request and collect metrics."""
    metrics = RunMetrics()
    payload = {
        "agent_name": "record-extractor",
        "input": {"prompt": query},
        "agent_tier": "simple",
    }

    start = time.monotonic()
    try:
        response = await client.post(
            url,
            json=payload,
            headers=headers,
            timeout=120.0,
        )
        metrics.status_code = response.status_code
        if response.status_code != 200:
            metrics.error = f"HTTP {response.status_code}"
        else:
            body = response.json()
            if body.get("error"):
                metrics.error = body["error"]
            else:
                output = body.get("output", "")
                metrics.output_length = len(str(output))
    except httpx.ReadTimeout:
        metrics.error = "ReadTimeout"
    except httpx.ConnectError as e:
        metrics.error = f"ConnectError: {e}"
    except Exception as e:
        metrics.error = f"{type(e).__name__}: {e}"

    metrics.total_latency_ms = (time.monotonic() - start) * 1000
    return metrics


async def run_tool_load(
    concurrency: int,
    num_requests: Optional[int] = None,
) -> ToolLoadResult:
    """Fire concurrent /runs/invoke requests."""
    if not AUTH_TOKEN:
        pytest.skip("AUTH_TOKEN not set")

    total = num_requests or concurrency * 2
    url = f"{AGENT_API_URL}/runs/invoke"
    headers = {
        "Authorization": f"Bearer {AUTH_TOKEN}",
        "Content-Type": "application/json",
    }

    sem = asyncio.Semaphore(concurrency)
    all_metrics: List[RunMetrics] = []

    async def bounded_request(query: str) -> RunMetrics:
        async with sem:
            async with httpx.AsyncClient() as client:
                return await send_run_invoke(client, url, headers, query)

    queries = [TOOL_TEST_QUERIES[i % len(TOOL_TEST_QUERIES)] for i in range(total)]
    wall_start = time.monotonic()
    results = await asyncio.gather(*[bounded_request(q) for q in queries])
    wall_elapsed = time.monotonic() - wall_start

    all_metrics.extend(results)
    latency_values = [m.total_latency_ms for m in all_metrics if m.error is None]
    errors = [m.error for m in all_metrics if m.error is not None]

    return ToolLoadResult(
        concurrency=concurrency,
        total_requests=total,
        successful=len(latency_values),
        failed=len(errors),
        error_rate=len(errors) / total if total > 0 else 0.0,
        latency_p50_ms=percentile(latency_values, 50),
        latency_p95_ms=percentile(latency_values, 95),
        latency_p99_ms=percentile(latency_values, 99),
        throughput_rps=len(latency_values) / wall_elapsed if wall_elapsed > 0 else 0.0,
        errors=errors,
    )


def print_tool_report(results: List[ToolLoadResult]) -> None:
    header = (
        f"{'Conc':>5} | {'Total':>5} | {'OK':>4} | {'Fail':>4} | {'Err%':>6} | "
        f"{'Lat p50':>10} | {'Lat p95':>10} | {'Lat p99':>10} | {'RPS':>6}"
    )
    print("\n" + "=" * len(header))
    print("TOOL CALLING LOAD TEST RESULTS")
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r.concurrency:>5} | {r.total_requests:>5} | {r.successful:>4} | "
            f"{r.failed:>4} | {r.error_rate:>5.1%} | "
            f"{r.latency_p50_ms:>8.0f}ms | {r.latency_p95_ms:>8.0f}ms | "
            f"{r.latency_p99_ms:>8.0f}ms | {r.throughput_rps:>5.1f}"
        )
    print("=" * len(header) + "\n")


@pytest.mark.load
class TestToolLoad:
    """Load tests for tool-calling via /runs/invoke."""

    @pytest.mark.asyncio
    async def test_single_run_baseline(self, auth_headers, agent_api_url):
        """Baseline: single /runs/invoke latency."""
        result = await run_tool_load(concurrency=1, num_requests=1)
        print_tool_report([result])
        assert result.successful >= 1, f"Single run failed: {result.errors}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("concurrency", CONCURRENCY_LEVELS)
    async def test_concurrent_tool_load(self, concurrency, auth_headers, agent_api_url):
        """Test /runs/invoke at various concurrency levels."""
        result = await run_tool_load(concurrency=concurrency)
        print_tool_report([result])
        assert result.error_rate < 0.5, (
            f"Error rate {result.error_rate:.0%} too high at concurrency={concurrency}"
        )

    @pytest.mark.asyncio
    async def test_tool_load_sweep(self, auth_headers, agent_api_url):
        """Run load sweep and report degradation curve."""
        results = []
        for level in CONCURRENCY_LEVELS:
            r = await run_tool_load(concurrency=level, num_requests=level * 2)
            results.append(r)
        print_tool_report(results)

        for r in results:
            assert r.error_rate < 0.5, (
                f"Error rate too high at concurrency={r.concurrency}: {r.error_rate:.0%}"
            )
