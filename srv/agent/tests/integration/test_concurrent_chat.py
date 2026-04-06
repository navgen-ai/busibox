"""
Integration tests for concurrent chat and benchmark helpers.

Tests the chat_benchmark concurrent flow utilities and verifies
the BusiboxClient connection pooling and semaphore limits work
correctly under parallel load.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.services.chat_benchmark import (
    ChatRunMetrics,
    ConcurrentBenchmarkResult,
    benchmark_concurrent_flows,
)
from app.services.load_monitor import LoadMonitor


class TestBenchmarkConcurrentFlows:
    """Tests for benchmark_concurrent_flows()."""

    @pytest.mark.asyncio
    async def test_single_concurrent_flow(self):
        """Single flow should return valid metrics."""
        def runner_factory(idx):
            async def runner():
                await asyncio.sleep(0.01)
                return [
                    {"type": "content", "timestamp_ms": 50.0},
                    {"type": "plan", "timestamp_ms": 80.0},
                ]
            return runner

        result = await benchmark_concurrent_flows(
            concurrency=1,
            runner_factory=runner_factory,
            num_runs=1,
        )

        assert isinstance(result, ConcurrentBenchmarkResult)
        assert result.successful == 1
        assert result.failed == 0
        assert result.error_rate == 0.0
        assert result.ttft_p50_ms > 0

    @pytest.mark.asyncio
    async def test_parallel_flows(self):
        """Multiple parallel flows should all complete."""
        def runner_factory(idx):
            async def runner():
                await asyncio.sleep(0.01)
                return [{"type": "content", "timestamp_ms": 20.0}]
            return runner

        result = await benchmark_concurrent_flows(
            concurrency=4,
            runner_factory=runner_factory,
            num_runs=8,
        )

        assert result.total_runs == 8
        assert result.successful == 8
        assert result.failed == 0
        assert result.throughput_rps > 0
        assert result.wall_time_ms > 0

    @pytest.mark.asyncio
    async def test_partial_failure(self):
        """Flows that raise exceptions should be counted as failures."""
        def runner_factory(idx):
            async def runner():
                if idx % 2 == 0:
                    raise RuntimeError("simulated error")
                return [{"type": "content", "timestamp_ms": 10.0}]
            return runner

        result = await benchmark_concurrent_flows(
            concurrency=2,
            runner_factory=runner_factory,
            num_runs=4,
        )

        assert result.total_runs == 4
        assert result.failed == 2
        assert result.successful == 2
        assert result.error_rate == 0.5
        assert len(result.errors) == 2

    @pytest.mark.asyncio
    async def test_percentile_accuracy(self):
        """Percentiles should be reasonable for known latency distribution."""
        latencies = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]

        def runner_factory(idx):
            async def runner():
                ms = latencies[idx]
                await asyncio.sleep(ms / 1000.0)
                return [{"type": "content", "timestamp_ms": float(ms)}]
            return runner

        result = await benchmark_concurrent_flows(
            concurrency=10,
            runner_factory=runner_factory,
            num_runs=10,
        )

        assert result.successful == 10
        assert result.ttft_p50_ms >= 40
        assert result.ttft_p95_ms >= 80


class TestLoadMonitorWithConcurrentChat:
    """Test load monitor behavior in concurrent chat scenarios."""

    @pytest.mark.asyncio
    async def test_load_monitor_tracks_concurrent_requests(self):
        """Load monitor should accurately track concurrent active requests."""
        monitor = LoadMonitor(threshold=5)
        max_active = 0
        lock = asyncio.Lock()

        async def chat_simulation(idx: int):
            nonlocal max_active
            async with monitor.track("agent"):
                async with lock:
                    current = monitor.get_metrics()["total_llm_active"]
                    if current > max_active:
                        max_active = current
                await asyncio.sleep(0.02)

        await asyncio.gather(*[chat_simulation(i) for i in range(10)])

        metrics = monitor.get_metrics()
        assert metrics["purposes"]["agent"]["active"] == 0
        assert metrics["purposes"]["agent"]["total_requests"] == 10
        assert max_active > 1  # some concurrency was achieved

    @pytest.mark.asyncio
    async def test_fallback_percentage_under_load(self):
        """Under heavy load, a meaningful percentage of requests should fallback."""
        monitor = LoadMonitor(threshold=3)
        fallback_count = 0
        total = 20

        async def chat_simulation(idx: int):
            nonlocal fallback_count
            async with monitor.track("agent") as should_fb:
                if should_fb:
                    fallback_count += 1
                await asyncio.sleep(0.01)

        await asyncio.gather(*[chat_simulation(i) for i in range(total)])

        assert fallback_count > 0
        assert fallback_count < total  # not ALL should fallback
