"""
Benchmark and A/B helpers for chat assistant optimization.

Includes single-flow benchmarking, A/B comparison, and concurrent
load benchmarking for measuring throughput and latency under parallel load.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional


@dataclass
class ChatRunMetrics:
    """Structured metrics for a single chat execution."""

    label: str
    time_to_first_response_ms: float
    time_to_plan_ms: float
    total_latency_ms: float
    event_count: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "time_to_first_response_ms": round(self.time_to_first_response_ms, 2),
            "time_to_plan_ms": round(self.time_to_plan_ms, 2),
            "total_latency_ms": round(self.total_latency_ms, 2),
            "event_count": self.event_count,
        }


@dataclass
class ConcurrentBenchmarkResult:
    """Aggregate metrics from running N chat flows in parallel."""

    concurrency: int
    total_runs: int
    successful: int
    failed: int
    error_rate: float
    ttft_p50_ms: float
    ttft_p95_ms: float
    ttft_p99_ms: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    wall_time_ms: float
    throughput_rps: float
    individual_metrics: List[ChatRunMetrics] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "concurrency": self.concurrency,
            "total_runs": self.total_runs,
            "successful": self.successful,
            "failed": self.failed,
            "error_rate": round(self.error_rate, 4),
            "ttft_p50_ms": round(self.ttft_p50_ms, 2),
            "ttft_p95_ms": round(self.ttft_p95_ms, 2),
            "ttft_p99_ms": round(self.ttft_p99_ms, 2),
            "latency_p50_ms": round(self.latency_p50_ms, 2),
            "latency_p95_ms": round(self.latency_p95_ms, 2),
            "latency_p99_ms": round(self.latency_p99_ms, 2),
            "wall_time_ms": round(self.wall_time_ms, 2),
            "throughput_rps": round(self.throughput_rps, 3),
        }


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(int(len(s) * pct / 100), len(s) - 1)
    return s[idx]


def compare_chat_runs(baseline: ChatRunMetrics, candidate: ChatRunMetrics) -> Dict[str, Any]:
    """Compare baseline and candidate metrics for A/B-style evaluations."""
    def pct_change(before: float, after: float) -> float:
        if before == 0:
            return 0.0
        return ((after - before) / before) * 100.0

    return {
        "baseline": baseline.as_dict(),
        "candidate": candidate.as_dict(),
        "delta_ms": {
            "time_to_first_response_ms": round(candidate.time_to_first_response_ms - baseline.time_to_first_response_ms, 2),
            "time_to_plan_ms": round(candidate.time_to_plan_ms - baseline.time_to_plan_ms, 2),
            "total_latency_ms": round(candidate.total_latency_ms - baseline.total_latency_ms, 2),
        },
        "delta_pct": {
            "time_to_first_response_ms": round(pct_change(baseline.time_to_first_response_ms, candidate.time_to_first_response_ms), 2),
            "time_to_plan_ms": round(pct_change(baseline.time_to_plan_ms, candidate.time_to_plan_ms), 2),
            "total_latency_ms": round(pct_change(baseline.total_latency_ms, candidate.total_latency_ms), 2),
        },
    }


async def benchmark_chat_flow(
    label: str,
    runner: Callable[[], Awaitable[List[Dict[str, Any]]]],
) -> ChatRunMetrics:
    """
    Run a benchmarked chat flow and extract standard latency metrics.

    The runner must return an ordered list of streamed events where each event has
    at least the keys: type and timestamp_ms (relative to start).
    """
    started = time.monotonic()
    events = await runner()
    total_latency_ms = (time.monotonic() - started) * 1000.0

    first_response = next(
        (event for event in events if event.get("type") in {"content", "interim"}),
        None,
    )
    first_plan = next((event for event in events if event.get("type") == "plan"), None)

    return ChatRunMetrics(
        label=label,
        time_to_first_response_ms=float(first_response.get("timestamp_ms", total_latency_ms) if first_response else total_latency_ms),
        time_to_plan_ms=float(first_plan.get("timestamp_ms", total_latency_ms) if first_plan else total_latency_ms),
        total_latency_ms=float(total_latency_ms),
        event_count=len(events),
    )


async def benchmark_concurrent_flows(
    concurrency: int,
    runner_factory: Callable[[int], Callable[[], Awaitable[List[Dict[str, Any]]]]],
    num_runs: Optional[int] = None,
) -> ConcurrentBenchmarkResult:
    """
    Run N chat flows in parallel and return aggregate latency metrics.

    Args:
        concurrency: Max simultaneous runners.
        runner_factory: Called with run index (0..N-1), returns an async runner
                        matching the ``benchmark_chat_flow`` contract.
        num_runs: Total runs to execute (defaults to ``concurrency``).

    Returns:
        Aggregate p50/p95/p99 TTFT and total-latency, error rate, throughput.
    """
    total = num_runs or concurrency
    sem = asyncio.Semaphore(concurrency)

    async def _guarded(idx: int) -> ChatRunMetrics:
        async with sem:
            return await benchmark_chat_flow(
                label=f"concurrent-{idx}",
                runner=runner_factory(idx),
            )

    wall_start = time.monotonic()
    results: List[ChatRunMetrics | BaseException] = await asyncio.gather(
        *[_guarded(i) for i in range(total)],
        return_exceptions=True,
    )
    wall_ms = (time.monotonic() - wall_start) * 1000.0

    metrics: List[ChatRunMetrics] = []
    errors: List[str] = []
    for r in results:
        if isinstance(r, BaseException):
            errors.append(f"{type(r).__name__}: {r}")
        else:
            metrics.append(r)

    ttft_values = [m.time_to_first_response_ms for m in metrics]
    latency_values = [m.total_latency_ms for m in metrics]
    successful = len(metrics)
    failed = len(errors)
    total_count = successful + failed

    return ConcurrentBenchmarkResult(
        concurrency=concurrency,
        total_runs=total_count,
        successful=successful,
        failed=failed,
        error_rate=failed / total_count if total_count > 0 else 0.0,
        ttft_p50_ms=_percentile(ttft_values, 50),
        ttft_p95_ms=_percentile(ttft_values, 95),
        ttft_p99_ms=_percentile(ttft_values, 99),
        latency_p50_ms=_percentile(latency_values, 50),
        latency_p95_ms=_percentile(latency_values, 95),
        latency_p99_ms=_percentile(latency_values, 99),
        wall_time_ms=wall_ms,
        throughput_rps=successful / (wall_ms / 1000.0) if wall_ms > 0 else 0.0,
        individual_metrics=metrics,
        errors=errors,
    )

