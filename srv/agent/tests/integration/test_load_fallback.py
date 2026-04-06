"""
Integration tests for load-aware model fallback.

Verifies that:
1. LoadMonitor correctly counts active requests across the system.
2. Model selection switches to the fallback when threshold is reached.
3. Fallback releases back to the primary when load drops.
4. The /llm/load endpoint returns correct metrics.

These tests use the LoadMonitor directly (no live LLM needed).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.load_monitor import LoadMonitor, get_load_monitor, reset_load_monitor


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Ensure a fresh monitor per test."""
    reset_load_monitor()
    yield
    reset_load_monitor()


class TestLoadFallbackIntegration:
    """End-to-end tests for the fallback decision chain."""

    @pytest.mark.asyncio
    async def test_gradual_load_ramp(self):
        """Simulate gradual ramp: track when fallback first triggers."""
        monitor = LoadMonitor(threshold=4)
        fallback_triggered_at = None

        for i in range(10):
            should_fb = await monitor.acquire("agent")
            if should_fb and fallback_triggered_at is None:
                fallback_triggered_at = i + 1

        assert fallback_triggered_at == 5  # threshold is 4, 5th request triggers

    @pytest.mark.asyncio
    async def test_mixed_purpose_load(self):
        """Load across different purposes all counts toward threshold."""
        monitor = LoadMonitor(threshold=3)

        await monitor.acquire("agent")
        await monitor.acquire("chat")
        await monitor.acquire("research")
        # 3 active, at threshold
        should_fb = await monitor.acquire("tool_calling")
        assert should_fb is True

    @pytest.mark.asyncio
    async def test_fallback_recovery_cycle(self):
        """System correctly transitions: normal -> fallback -> normal."""
        monitor = LoadMonitor(threshold=2)

        # Ramp up to trigger fallback
        await monitor.acquire("agent")
        await monitor.acquire("agent")
        should_fb = await monitor.acquire("agent")
        assert should_fb is True
        assert monitor.should_fallback("agent") is True

        # Release requests to drop below threshold
        await monitor.release("agent")
        await monitor.release("agent")
        assert monitor.should_fallback("agent") is False

    @pytest.mark.asyncio
    async def test_concurrent_workers_with_fallback(self):
        """Simulate concurrent chat workers each checking fallback."""
        monitor = LoadMonitor(threshold=3)
        fallback_count = 0

        async def worker(worker_id: int):
            nonlocal fallback_count
            async with monitor.track("agent") as should_fb:
                if should_fb:
                    fallback_count += 1
                await asyncio.sleep(0.01)

        await asyncio.gather(*[worker(i) for i in range(8)])

        metrics = monitor.get_metrics()
        assert metrics["purposes"]["agent"]["active"] == 0
        assert metrics["purposes"]["agent"]["total_requests"] == 8
        assert fallback_count > 0

    @pytest.mark.asyncio
    async def test_metrics_endpoint_data(self):
        """Verify the shape of data returned by get_metrics()."""
        monitor = LoadMonitor(threshold=5)

        await monitor.acquire("agent")
        await monitor.acquire("chat")

        metrics = monitor.get_metrics()
        assert "total_llm_active" in metrics
        assert "threshold" in metrics
        assert "fallback_active" in metrics
        assert "purposes" in metrics
        assert metrics["total_llm_active"] == 2
        assert metrics["threshold"] == 5
        assert metrics["fallback_active"] is False

    @pytest.mark.asyncio
    async def test_peak_active_tracking(self):
        """Peak active should track the highest watermark."""
        monitor = LoadMonitor(threshold=10)

        for _ in range(5):
            await monitor.acquire("agent")
        for _ in range(3):
            await monitor.release("agent")

        metrics = monitor.get_metrics()
        assert metrics["purposes"]["agent"]["peak_active"] == 5
        assert metrics["purposes"]["agent"]["active"] == 2


class TestConcurrentChat:
    """Tests verifying concurrent chat requests work correctly."""

    @pytest.mark.asyncio
    async def test_many_concurrent_track_operations(self):
        """Stress test: 50 concurrent track operations should not corrupt state."""
        monitor = LoadMonitor(threshold=10)

        async def simulate_chat(idx: int):
            async with monitor.track("agent"):
                await asyncio.sleep(0.005)

        await asyncio.gather(*[simulate_chat(i) for i in range(50)])

        metrics = monitor.get_metrics()
        assert metrics["purposes"]["agent"]["active"] == 0
        assert metrics["purposes"]["agent"]["total_requests"] == 50

    @pytest.mark.asyncio
    async def test_interleaved_acquire_release(self):
        """Interleaved acquire/release across purposes stays consistent."""
        monitor = LoadMonitor(threshold=5)

        ops = []
        for i in range(10):
            purpose = ["agent", "chat", "research"][i % 3]
            ops.append(("acquire", purpose))
        for i in range(10):
            purpose = ["agent", "chat", "research"][i % 3]
            ops.append(("release", purpose))

        for op, purpose in ops:
            if op == "acquire":
                await monitor.acquire(purpose)
            else:
                await monitor.release(purpose)

        metrics = monitor.get_metrics()
        for p in ("agent", "chat", "research"):
            assert metrics["purposes"][p]["active"] == 0
