"""Tests for attachment resolver processing status streaming."""

import asyncio
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.streaming import StreamEvent
from app.services.attachment_resolver import AttachmentResolver


class StreamCollector:
    def __init__(self):
        self.events: List[StreamEvent] = []

    async def __call__(self, event: StreamEvent):
        self.events.append(event)


def _make_attachment(file_id: str = "abc-123", filename: str = "report.pdf"):
    return {
        "id": "att-1",
        "file_id": file_id,
        "filename": filename,
        "mime_type": "application/pdf",
        "file_url": f"http://data-api/files/{file_id}/download",
    }


class _FakeClient:
    """Fake BusiboxClient that yields configurable status on each poll."""

    def __init__(self, statuses: List[str]):
        self._statuses = list(statuses)
        self._call_count = 0

    async def request(self, method: str, path: str, **kwargs):
        if "/markdown" in path:
            return {"markdown": "# Test document content"}
        if "/search" in path:
            return {"results": []}
        status = self._statuses[min(self._call_count, len(self._statuses) - 1)]
        self._call_count += 1
        return {"status": {"stage": status}}


@pytest.mark.asyncio
async def test_wait_emits_content_event_when_processing():
    """Should emit a user-visible content event when document is still processing."""
    resolver = AttachmentResolver(max_wait_seconds=5)
    collector = StreamCollector()

    fake_client = _FakeClient(["parsing", "embedding", "completed"])

    await resolver._wait_until_processed(
        client=fake_client,
        file_id="abc-123",
        filename="report.pdf",
        stream=collector,
    )

    content_events = [e for e in collector.events if e.type == "content"]
    assert len(content_events) >= 1
    first = content_events[0]
    assert "report.pdf" in first.message
    assert "still being processed" in first.message
    assert first.data["phase"] == "attachment_processing"
    assert first.data["file_id"] == "abc-123"


@pytest.mark.asyncio
async def test_wait_emits_ready_event_after_processing():
    """Should emit a 'ready' content event when document finishes processing."""
    resolver = AttachmentResolver(max_wait_seconds=5)
    collector = StreamCollector()

    fake_client = _FakeClient(["embedding", "completed"])

    await resolver._wait_until_processed(
        client=fake_client,
        file_id="abc-123",
        filename="report.pdf",
        stream=collector,
    )

    content_events = [e for e in collector.events if e.type == "content"]
    ready_events = [e for e in content_events if e.data and e.data.get("phase") == "attachment_ready"]
    assert len(ready_events) == 1
    assert "ready" in ready_events[0].message.lower()


@pytest.mark.asyncio
async def test_wait_emits_progress_on_stage_change():
    """Should emit progress events as the processing stage advances."""
    resolver = AttachmentResolver(max_wait_seconds=10)
    collector = StreamCollector()

    fake_client = _FakeClient(["parsing", "chunking", "embedding", "completed"])

    await resolver._wait_until_processed(
        client=fake_client,
        file_id="abc-123",
        filename="report.pdf",
        stream=collector,
    )

    progress_events = [e for e in collector.events if e.type == "progress"]
    assert len(progress_events) >= 1
    stages = [e.data["stage"] for e in progress_events]
    assert "chunking" in stages or "embedding" in stages


@pytest.mark.asyncio
async def test_wait_no_events_when_already_ready():
    """If document is already ready on first poll, no processing events should be emitted."""
    resolver = AttachmentResolver(max_wait_seconds=5)
    collector = StreamCollector()

    fake_client = _FakeClient(["completed"])

    await resolver._wait_until_processed(
        client=fake_client,
        file_id="abc-123",
        filename="report.pdf",
        stream=collector,
    )

    assert len(collector.events) == 0


@pytest.mark.asyncio
async def test_wait_emits_failed_event():
    """Should emit a failure content event when processing fails."""
    resolver = AttachmentResolver(max_wait_seconds=5)
    collector = StreamCollector()

    fake_client = _FakeClient(["parsing", "failed"])

    with pytest.raises(RuntimeError, match="Processing failed"):
        await resolver._wait_until_processed(
            client=fake_client,
            file_id="abc-123",
            filename="report.pdf",
            stream=collector,
        )

    content_events = [e for e in collector.events if e.type == "content"]
    failed_events = [e for e in content_events if e.data and e.data.get("phase") == "attachment_failed"]
    assert len(failed_events) == 1
    assert "failed" in failed_events[0].message.lower()


@pytest.mark.asyncio
async def test_wait_emits_timeout_event():
    """Should emit a timeout content event when max_wait_seconds is exceeded."""
    resolver = AttachmentResolver(max_wait_seconds=2)
    collector = StreamCollector()

    fake_client = _FakeClient(["parsing"] * 20)

    with pytest.raises(RuntimeError, match="Timed out"):
        await resolver._wait_until_processed(
            client=fake_client,
            file_id="abc-123",
            filename="report.pdf",
            stream=collector,
        )

    content_events = [e for e in collector.events if e.type == "content"]
    timeout_events = [e for e in content_events if e.data and e.data.get("phase") == "attachment_timeout"]
    assert len(timeout_events) == 1
    assert "longer than expected" in timeout_events[0].message


@pytest.mark.asyncio
async def test_wait_no_duplicate_progress_for_same_stage():
    """Should not emit duplicate progress events for the same stage."""
    resolver = AttachmentResolver(max_wait_seconds=10)
    collector = StreamCollector()

    fake_client = _FakeClient(["parsing", "parsing", "parsing", "completed"])

    await resolver._wait_until_processed(
        client=fake_client,
        file_id="abc-123",
        filename="report.pdf",
        stream=collector,
    )

    progress_events = [e for e in collector.events if e.type == "progress"]
    assert len(progress_events) == 0  # No stage change from parsing → parsing


@pytest.mark.asyncio
async def test_wait_handles_various_completion_statuses():
    """All completion-like statuses should be recognized."""
    resolver = AttachmentResolver(max_wait_seconds=5)

    for done_status in ("completed", "complete", "ready", "indexed", "success"):
        collector = StreamCollector()
        fake_client = _FakeClient([done_status])
        await resolver._wait_until_processed(
            client=fake_client,
            file_id="abc-123",
            filename="test.pdf",
            stream=collector,
        )
        assert len(collector.events) == 0
