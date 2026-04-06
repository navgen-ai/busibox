"""
Chat load tests for the agent-api.

Exercises the agentic streaming chat endpoint at configurable concurrency
levels and reports latency distributions (p50/p95/p99), error rates, and
throughput.

Usage:
    # Run against a live agent-api (requires AUTH_TOKEN env var)
    make test-docker SERVICE=agent ARGS="tests/load/chat_load_test.py"

    # Or directly:
    AUTH_TOKEN=<jwt> AGENT_API_URL=http://localhost:8000 \
        pytest tests/load/chat_load_test.py -v -s
"""

import asyncio
import json
import logging
import statistics
import time
from dataclasses import dataclass, field
from typing import List, Optional

import httpx
import pytest

from .conftest import AGENT_API_URL, AUTH_TOKEN, LOAD_TEST_QUERIES

logger = logging.getLogger(__name__)

CONCURRENCY_LEVELS = [1, 2, 4, 8]


@dataclass
class RequestMetrics:
    """Metrics for a single chat request."""
    ttft_ms: Optional[float] = None
    total_latency_ms: float = 0.0
    error: Optional[str] = None
    event_count: int = 0
    content_length: int = 0
    status_code: int = 0


@dataclass
class LoadTestResult:
    """Aggregate metrics for a load test run at a given concurrency level."""
    concurrency: int = 0
    total_requests: int = 0
    successful: int = 0
    failed: int = 0
    error_rate: float = 0.0
    ttft_p50_ms: float = 0.0
    ttft_p95_ms: float = 0.0
    ttft_p99_ms: float = 0.0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    latency_p99_ms: float = 0.0
    throughput_rps: float = 0.0
    errors: List[str] = field(default_factory=list)


def percentile(data: List[float], pct: float) -> float:
    """Calculate percentile from a sorted list."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * pct / 100)
    idx = min(idx, len(sorted_data) - 1)
    return sorted_data[idx]


async def send_chat_request(
    client: httpx.AsyncClient,
    url: str,
    headers: dict,
    query: str,
) -> RequestMetrics:
    """Send a single agentic streaming chat request and collect metrics."""
    metrics = RequestMetrics()
    payload = {
        "message": query,
        "model": "auto",
        "enable_web_search": False,
        "enable_doc_search": False,
    }

    start = time.monotonic()
    first_content_received = False

    try:
        async with client.stream(
            "POST",
            url,
            json=payload,
            headers=headers,
            timeout=120.0,
        ) as response:
            metrics.status_code = response.status_code
            if response.status_code != 200:
                metrics.error = f"HTTP {response.status_code}"
                metrics.total_latency_ms = (time.monotonic() - start) * 1000
                return metrics

            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                if line.startswith("data: "):
                    metrics.event_count += 1
                    try:
                        data = json.loads(line[6:])
                        if not first_content_received and data.get("chunk"):
                            metrics.ttft_ms = (time.monotonic() - start) * 1000
                            first_content_received = True
                        if data.get("chunk"):
                            metrics.content_length += len(data["chunk"])
                    except json.JSONDecodeError:
                        pass

    except httpx.ReadTimeout:
        metrics.error = "ReadTimeout"
    except httpx.ConnectError as e:
        metrics.error = f"ConnectError: {e}"
    except Exception as e:
        metrics.error = f"{type(e).__name__}: {e}"

    metrics.total_latency_ms = (time.monotonic() - start) * 1000
    if metrics.ttft_ms is None and metrics.error is None:
        metrics.ttft_ms = metrics.total_latency_ms
    return metrics


async def run_concurrent_load(
    concurrency: int,
    num_requests: Optional[int] = None,
) -> LoadTestResult:
    """
    Fire concurrent chat requests and collect aggregate metrics.

    Args:
        concurrency: Number of simultaneous requests.
        num_requests: Total requests to send (defaults to concurrency * 2).
    """
    if not AUTH_TOKEN:
        pytest.skip("AUTH_TOKEN not set")

    total = num_requests or concurrency * 2
    url = f"{AGENT_API_URL}/chat/message/stream/agentic"
    headers = {
        "Authorization": f"Bearer {AUTH_TOKEN}",
        "Content-Type": "application/json",
    }

    sem = asyncio.Semaphore(concurrency)
    all_metrics: List[RequestMetrics] = []

    async def bounded_request(query: str) -> RequestMetrics:
        async with sem:
            async with httpx.AsyncClient() as client:
                return await send_chat_request(client, url, headers, query)

    queries = [LOAD_TEST_QUERIES[i % len(LOAD_TEST_QUERIES)] for i in range(total)]
    wall_start = time.monotonic()
    results = await asyncio.gather(*[bounded_request(q) for q in queries])
    wall_elapsed = time.monotonic() - wall_start

    all_metrics.extend(results)

    ttft_values = [m.ttft_ms for m in all_metrics if m.ttft_ms is not None and m.error is None]
    latency_values = [m.total_latency_ms for m in all_metrics if m.error is None]
    errors = [m.error for m in all_metrics if m.error is not None]

    result = LoadTestResult(
        concurrency=concurrency,
        total_requests=total,
        successful=len(latency_values),
        failed=len(errors),
        error_rate=len(errors) / total if total > 0 else 0.0,
        ttft_p50_ms=percentile(ttft_values, 50),
        ttft_p95_ms=percentile(ttft_values, 95),
        ttft_p99_ms=percentile(ttft_values, 99),
        latency_p50_ms=percentile(latency_values, 50),
        latency_p95_ms=percentile(latency_values, 95),
        latency_p99_ms=percentile(latency_values, 99),
        throughput_rps=len(latency_values) / wall_elapsed if wall_elapsed > 0 else 0.0,
        errors=errors,
    )
    return result


def print_load_report(results: List[LoadTestResult]) -> None:
    """Print a formatted load test report."""
    header = (
        f"{'Conc':>5} | {'Total':>5} | {'OK':>4} | {'Fail':>4} | {'Err%':>6} | "
        f"{'TTFT p50':>10} | {'TTFT p95':>10} | {'TTFT p99':>10} | "
        f"{'Lat p50':>10} | {'Lat p95':>10} | {'Lat p99':>10} | {'RPS':>6}"
    )
    print("\n" + "=" * len(header))
    print("CHAT LOAD TEST RESULTS")
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r.concurrency:>5} | {r.total_requests:>5} | {r.successful:>4} | "
            f"{r.failed:>4} | {r.error_rate:>5.1%} | "
            f"{r.ttft_p50_ms:>8.0f}ms | {r.ttft_p95_ms:>8.0f}ms | "
            f"{r.ttft_p99_ms:>8.0f}ms | "
            f"{r.latency_p50_ms:>8.0f}ms | {r.latency_p95_ms:>8.0f}ms | "
            f"{r.latency_p99_ms:>8.0f}ms | {r.throughput_rps:>5.1f}"
        )
    print("=" * len(header) + "\n")


@pytest.mark.load
class TestChatLoad:
    """Load tests for the chat streaming endpoint."""

    @pytest.mark.asyncio
    async def test_single_request_baseline(self, auth_headers, agent_api_url):
        """Baseline: single request latency."""
        result = await run_concurrent_load(concurrency=1, num_requests=1)
        print_load_report([result])
        assert result.successful >= 1, f"Single request failed: {result.errors}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("concurrency", CONCURRENCY_LEVELS)
    async def test_concurrent_chat_load(self, concurrency, auth_headers, agent_api_url):
        """Test chat endpoint at various concurrency levels."""
        result = await run_concurrent_load(concurrency=concurrency)
        print_load_report([result])
        assert result.error_rate < 0.5, (
            f"Error rate {result.error_rate:.0%} too high at concurrency={concurrency}: "
            f"{result.errors[:5]}"
        )

    @pytest.mark.asyncio
    async def test_load_sweep(self, auth_headers, agent_api_url):
        """Run load sweep across all concurrency levels and report degradation."""
        results = []
        for level in CONCURRENCY_LEVELS:
            r = await run_concurrent_load(concurrency=level, num_requests=level * 2)
            results.append(r)
        print_load_report(results)

        for r in results:
            assert r.error_rate < 0.5, (
                f"Error rate too high at concurrency={r.concurrency}: {r.error_rate:.0%}"
            )
