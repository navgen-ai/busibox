"""
Unit tests for the LoadMonitor service.

Tests the load tracking, threshold logic, acquire/release lifecycle,
and fallback decision-making without requiring any external services.
"""

import asyncio

import pytest

from app.services.load_monitor import LoadMonitor, _LLM_PURPOSES


@pytest.fixture
def monitor():
    return LoadMonitor(threshold=3)


class TestLoadMonitorBasics:
    """Core acquire/release and threshold logic."""

    @pytest.mark.asyncio
    async def test_acquire_increments_active_count(self, monitor):
        should_fb = await monitor.acquire("agent")
        metrics = monitor.get_metrics()
        assert metrics["purposes"]["agent"]["active"] == 1
        assert should_fb is False

    @pytest.mark.asyncio
    async def test_release_decrements_active_count(self, monitor):
        await monitor.acquire("agent")
        await monitor.release("agent")
        metrics = monitor.get_metrics()
        assert metrics["purposes"]["agent"]["active"] == 0

    @pytest.mark.asyncio
    async def test_release_does_not_go_negative(self, monitor):
        await monitor.release("agent")
        metrics = monitor.get_metrics()
        assert metrics["purposes"]["agent"]["active"] == 0

    @pytest.mark.asyncio
    async def test_fallback_triggers_at_threshold(self, monitor):
        """Fallback triggers when total LLM active exceeds threshold."""
        await monitor.acquire("agent")
        await monitor.acquire("chat")
        await monitor.acquire("research")
        # 3 active, threshold is 3 => next one goes over
        should_fb = await monitor.acquire("agent")
        assert should_fb is True

    @pytest.mark.asyncio
    async def test_no_fallback_below_threshold(self, monitor):
        await monitor.acquire("agent")
        await monitor.acquire("chat")
        should_fb = await monitor.acquire("research")
        # 3 active, threshold is 3 => at threshold but not over
        assert should_fb is False

    @pytest.mark.asyncio
    async def test_fallback_recovers_after_release(self, monitor):
        for _ in range(4):
            await monitor.acquire("agent")
        assert monitor.should_fallback("agent") is True

        await monitor.release("agent")
        await monitor.release("agent")
        assert monitor.should_fallback("agent") is False

    @pytest.mark.asyncio
    async def test_non_llm_purpose_never_triggers_fallback(self, monitor):
        """Purposes not in _LLM_PURPOSES should not trigger fallback."""
        for _ in range(10):
            await monitor.acquire("embedding")
        should_fb = await monitor.acquire("embedding")
        assert should_fb is False
        assert "embedding" not in _LLM_PURPOSES


class TestLoadMonitorMetrics:
    """Metrics reporting."""

    @pytest.mark.asyncio
    async def test_metrics_snapshot(self, monitor):
        await monitor.acquire("agent")
        await monitor.acquire("agent")
        await monitor.acquire("chat")

        metrics = monitor.get_metrics()
        assert metrics["total_llm_active"] == 3
        assert metrics["threshold"] == 3
        assert metrics["fallback_active"] is True
        assert metrics["purposes"]["agent"]["active"] == 2
        assert metrics["purposes"]["agent"]["total_requests"] == 2
        assert metrics["purposes"]["agent"]["peak_active"] == 2
        assert metrics["purposes"]["chat"]["active"] == 1

    @pytest.mark.asyncio
    async def test_total_fallbacks_counted(self, monitor):
        for _ in range(3):
            await monitor.acquire("agent")
        # 4th goes over threshold
        await monitor.acquire("agent")
        metrics = monitor.get_metrics()
        assert metrics["purposes"]["agent"]["total_fallbacks"] == 1

    @pytest.mark.asyncio
    async def test_reset_clears_metrics(self, monitor):
        await monitor.acquire("agent")
        monitor.reset()
        metrics = monitor.get_metrics()
        assert metrics["total_llm_active"] == 0
        assert len(metrics["purposes"]) == 0


class TestLoadMonitorContextManager:
    """Context manager interface."""

    @pytest.mark.asyncio
    async def test_track_context_manager(self, monitor):
        async with monitor.track("agent") as should_fb:
            assert should_fb is False
            metrics = monitor.get_metrics()
            assert metrics["purposes"]["agent"]["active"] == 1

        metrics = monitor.get_metrics()
        assert metrics["purposes"]["agent"]["active"] == 0

    @pytest.mark.asyncio
    async def test_track_releases_on_exception(self, monitor):
        with pytest.raises(ValueError):
            async with monitor.track("agent"):
                raise ValueError("test error")

        metrics = monitor.get_metrics()
        assert metrics["purposes"]["agent"]["active"] == 0

    @pytest.mark.asyncio
    async def test_track_reports_fallback(self, monitor):
        for _ in range(3):
            await monitor.acquire("agent")

        async with monitor.track("agent") as should_fb:
            assert should_fb is True


class TestLoadMonitorConcurrency:
    """Concurrent access safety."""

    @pytest.mark.asyncio
    async def test_concurrent_acquire_release(self, monitor):
        """Multiple concurrent acquire/release cycles should not corrupt state."""
        async def worker(purpose: str, n: int):
            for _ in range(n):
                await monitor.acquire(purpose)
                await asyncio.sleep(0.001)
                await monitor.release(purpose)

        await asyncio.gather(
            worker("agent", 20),
            worker("chat", 20),
            worker("research", 20),
        )

        metrics = monitor.get_metrics()
        for p in ("agent", "chat", "research"):
            assert metrics["purposes"][p]["active"] == 0
            assert metrics["purposes"][p]["total_requests"] == 20


class TestLoadMonitorThreshold:
    """Threshold configuration."""

    def test_threshold_property(self, monitor):
        assert monitor.threshold == 3
        monitor.threshold = 10
        assert monitor.threshold == 10

    def test_threshold_minimum_is_one(self, monitor):
        monitor.threshold = 0
        assert monitor.threshold == 1
        monitor.threshold = -5
        assert monitor.threshold == 1

    @pytest.mark.asyncio
    async def test_cross_purpose_threshold(self, monitor):
        """Threshold is across ALL LLM purposes, not per-purpose."""
        await monitor.acquire("agent")
        await monitor.acquire("chat")
        await monitor.acquire("research")
        # 3 active across 3 purposes, threshold is 3
        should_fb = await monitor.acquire("agent")
        assert should_fb is True


class TestShouldFallbackSync:
    """Non-async should_fallback check."""

    @pytest.mark.asyncio
    async def test_should_fallback_reflects_state(self, monitor):
        assert monitor.should_fallback("agent") is False
        for _ in range(3):
            await monitor.acquire("agent")
        assert monitor.should_fallback("agent") is True

    def test_should_fallback_non_llm_purpose(self, monitor):
        assert monitor.should_fallback("embedding") is False
        assert monitor.should_fallback("reranking") is False
