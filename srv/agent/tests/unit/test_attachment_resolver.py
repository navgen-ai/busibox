"""Tests for attachment resolver: processing status, early chunks, keyword ranking, and coverage annotation."""

import asyncio
import math
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.streaming import StreamEvent
from app.services.attachment_resolver import (
    AttachmentResolver,
    EARLY_READY_STATES,
    TERMINAL_STATES,
    FAILED_STATES,
    _ProcessingStatus,
)


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
    """Fake BusiboxClient that yields configurable responses per path."""

    def __init__(
        self,
        statuses: List[str],
        *,
        chunks: Optional[List[str]] = None,
        markdown: str = "# Test document content",
        pages_processed: Optional[int] = None,
        total_pages: Optional[int] = None,
    ):
        self._statuses = list(statuses)
        self._call_count = 0
        self._chunks = chunks
        self._markdown = markdown
        self._pages_processed = pages_processed
        self._total_pages = total_pages

    async def request(self, method: str, path: str, **kwargs):
        if "/markdown" in path:
            return {"markdown": self._markdown}
        if "/search" in path:
            return {"results": []}
        if "/chunks" in path:
            if self._chunks is not None:
                return {"chunks": [{"text": c} for c in self._chunks]}
            return {"chunks": []}

        status = self._statuses[min(self._call_count, len(self._statuses) - 1)]
        self._call_count += 1
        info: Dict[str, Any] = {"status": {"stage": status}}
        if self._pages_processed is not None:
            info["status"]["pages_processed"] = self._pages_processed
        if self._total_pages is not None:
            info["status"]["total_pages"] = self._total_pages
        return info


# =====================================================================
# _check_status tests
# =====================================================================


@pytest.mark.asyncio
async def test_check_status_terminal():
    """Terminal states should be recognized."""
    resolver = AttachmentResolver()
    for state in TERMINAL_STATES:
        client = _FakeClient([state])
        result = await resolver._check_status(client, "file-1")
        assert result.is_terminal, f"{state} should be terminal"
        assert not result.is_early_ready
        assert not result.is_failed


@pytest.mark.asyncio
async def test_check_status_early_ready():
    """Early-ready states should be recognized."""
    resolver = AttachmentResolver()
    for state in EARLY_READY_STATES:
        client = _FakeClient([state])
        result = await resolver._check_status(client, "file-1")
        assert result.is_early_ready, f"{state} should be early-ready"
        assert not result.is_terminal
        assert not result.is_failed


@pytest.mark.asyncio
async def test_check_status_failed():
    """Failed states should be recognized."""
    resolver = AttachmentResolver()
    for state in FAILED_STATES:
        client = _FakeClient([state])
        result = await resolver._check_status(client, "file-1")
        assert result.is_failed, f"{state} should be failed"
        assert not result.is_terminal
        assert not result.is_early_ready


@pytest.mark.asyncio
async def test_check_status_extracts_page_progress():
    """Page progress metadata should be extracted from file info."""
    resolver = AttachmentResolver()
    client = _FakeClient(["available"], pages_processed=20, total_pages=47)
    result = await resolver._check_status(client, "file-1")
    assert result.pages_processed == 20
    assert result.total_pages == 47


@pytest.mark.asyncio
async def test_check_status_none_page_progress():
    """Missing page progress should be None."""
    resolver = AttachmentResolver()
    client = _FakeClient(["parsing"])
    result = await resolver._check_status(client, "file-1")
    assert result.pages_processed is None
    assert result.total_pages is None


# =====================================================================
# _fetch_early_chunks tests
# =====================================================================


@pytest.mark.asyncio
async def test_fetch_early_chunks_returns_text():
    """Should fetch and extract chunk text from the /chunks endpoint."""
    resolver = AttachmentResolver()
    chunks = ["chunk 1 text", "chunk 2 text", "chunk 3 text"]
    client = _FakeClient(["available"], chunks=chunks)
    result = await resolver._fetch_early_chunks(client=client, file_id="f-1")
    assert result == chunks


@pytest.mark.asyncio
async def test_fetch_early_chunks_empty_when_no_chunks():
    """Should return empty list when no chunks are available."""
    resolver = AttachmentResolver()
    client = _FakeClient(["available"], chunks=[])
    result = await resolver._fetch_early_chunks(client=client, file_id="f-1")
    assert result == []


@pytest.mark.asyncio
async def test_fetch_early_chunks_handles_error():
    """Should return empty list if the /chunks endpoint fails."""
    resolver = AttachmentResolver()

    class _ErrorClient:
        async def request(self, method, path, **kwargs):
            if "/chunks" in path:
                raise ConnectionError("Service unavailable")
            return {"status": {"stage": "available"}}

    result = await resolver._fetch_early_chunks(client=_ErrorClient(), file_id="f-1")
    assert result == []


# =====================================================================
# _rank_chunks_by_relevance tests
# =====================================================================


def test_rank_chunks_relevance_basic():
    """Chunks containing query terms should rank higher."""
    chunks = [
        "The weather in Alaska is cold and snowy.",
        "Marine construction requires dredging equipment.",
        "Dredging projects need careful environmental review.",
    ]
    ranked = AttachmentResolver._rank_chunks_by_relevance("dredging environmental impact", chunks)
    assert ranked[0] == chunks[2]
    assert ranked[1] == chunks[1]
    assert ranked[2] == chunks[0]


def test_rank_chunks_empty_query():
    """Empty query should return chunks in original order."""
    chunks = ["alpha", "beta", "gamma"]
    ranked = AttachmentResolver._rank_chunks_by_relevance("", chunks)
    assert ranked == chunks


def test_rank_chunks_empty_chunks():
    """Empty chunks list should return empty."""
    ranked = AttachmentResolver._rank_chunks_by_relevance("some query", [])
    assert ranked == []


def test_rank_chunks_stopwords_ignored():
    """Stopwords in the query should not affect ranking."""
    chunks = [
        "Report on the financial results for the quarter.",
        "Financial analysis shows positive growth trends.",
    ]
    ranked = AttachmentResolver._rank_chunks_by_relevance(
        "what are the financial results", chunks
    )
    assert "financial" in ranked[0].lower()


def test_rank_chunks_preserves_order_on_tie():
    """Equally-scored chunks should preserve original order."""
    chunks = [
        "The project status is green.",
        "This project status remains green.",
    ]
    ranked = AttachmentResolver._rank_chunks_by_relevance("project status", chunks)
    assert ranked[0] == chunks[0]
    assert ranked[1] == chunks[1]


# =====================================================================
# _resolve_document: early-ready path tests
# =====================================================================


@pytest.mark.asyncio
async def test_resolve_document_uses_early_chunks_when_available():
    """When status is 'available', should use early chunks instead of waiting."""
    resolver = AttachmentResolver()
    collector = StreamCollector()
    chunks = ["relevant chunk about dredging costs", "another chunk"]
    client = _FakeClient(
        ["available"],
        chunks=chunks,
        pages_processed=20,
        total_pages=47,
    )

    result = await resolver._resolve_document(
        client=client,
        file_id="f-1",
        filename="report.pdf",
        query="what are the dredging costs",
        available_tokens=5000,
        stream=collector,
        attachment=_make_attachment("f-1"),
    )

    assert result["source_kind"] == "early_chunks"
    assert result["pages_processed"] == 20
    assert result["total_pages"] == 47
    assert "dredging" in result["content"].lower()

    content_events = [e for e in collector.events if e.type == "content"]
    early_events = [e for e in content_events if e.data and e.data.get("phase") == "attachment_early_ready"]
    assert len(early_events) == 1
    assert "20 of 47" in early_events[0].message


@pytest.mark.asyncio
async def test_resolve_document_uses_full_path_when_completed():
    """When status is terminal, should use the full markdown/RAG path."""
    resolver = AttachmentResolver()
    collector = StreamCollector()
    client = _FakeClient(["completed"], markdown="# Full document\n\nAll content here.")

    result = await resolver._resolve_document(
        client=client,
        file_id="f-1",
        filename="report.pdf",
        query="summary",
        available_tokens=5000,
        stream=collector,
        attachment=_make_attachment("f-1"),
    )

    assert result["source_kind"] == "full_markdown"
    assert "Full document" in result["content"]


@pytest.mark.asyncio
async def test_resolve_document_fallback_on_failed():
    """When status is failed, should emit failure event and return fallback."""
    resolver = AttachmentResolver()
    collector = StreamCollector()
    client = _FakeClient(["failed"])

    result = await resolver._resolve_document(
        client=client,
        file_id="f-1",
        filename="report.pdf",
        query="summary",
        available_tokens=5000,
        stream=collector,
        attachment=_make_attachment("f-1"),
    )

    assert result["source_kind"] == "fallback"
    failed_events = [
        e for e in collector.events
        if e.type == "content" and e.data and e.data.get("phase") == "attachment_failed"
    ]
    assert len(failed_events) == 1


@pytest.mark.asyncio
async def test_resolve_document_early_chunks_fallback_on_empty():
    """If early chunks are empty, should return fallback."""
    resolver = AttachmentResolver()
    collector = StreamCollector()
    client = _FakeClient(["available"], chunks=[], pages_processed=5, total_pages=100)

    result = await resolver._resolve_document(
        client=client,
        file_id="f-1",
        filename="report.pdf",
        query="summary",
        available_tokens=5000,
        stream=collector,
        attachment=_make_attachment("f-1"),
    )

    assert result["source_kind"] == "fallback"


# =====================================================================
# _resolve_with_polling tests (replaces old _wait_until_processed tests)
# =====================================================================


@pytest.mark.asyncio
async def test_polling_reaches_terminal_and_resolves():
    """Polling through early stages to terminal should return full content."""
    resolver = AttachmentResolver(max_wait_seconds=10)
    collector = StreamCollector()
    client = _FakeClient(["parsing", "chunking", "completed"], markdown="# Done")

    result = await resolver._resolve_with_polling(
        client=client,
        file_id="f-1",
        filename="report.pdf",
        query="summary",
        available_tokens=5000,
        stream=collector,
        attachment=_make_attachment("f-1"),
    )

    assert result["source_kind"] == "full_markdown"
    assert "Done" in result["content"]


@pytest.mark.asyncio
async def test_polling_switches_to_early_ready():
    """Polling should switch to early-ready path when 'available' is reached."""
    resolver = AttachmentResolver(max_wait_seconds=10)
    collector = StreamCollector()
    chunks = ["early content about the project"]
    client = _FakeClient(
        ["parsing", "chunking", "available"],
        chunks=chunks,
        pages_processed=10,
        total_pages=50,
    )

    result = await resolver._resolve_with_polling(
        client=client,
        file_id="f-1",
        filename="report.pdf",
        query="project details",
        available_tokens=5000,
        stream=collector,
        attachment=_make_attachment("f-1"),
    )

    assert result["source_kind"] == "early_chunks"


@pytest.mark.asyncio
async def test_polling_emits_progress_on_stage_change():
    """Polling should emit progress events as stages change."""
    resolver = AttachmentResolver(max_wait_seconds=10)
    collector = StreamCollector()
    client = _FakeClient(["parsing", "chunking", "completed"], markdown="# Done")

    await resolver._resolve_with_polling(
        client=client,
        file_id="f-1",
        filename="report.pdf",
        query="summary",
        available_tokens=5000,
        stream=collector,
        attachment=_make_attachment("f-1"),
    )

    progress_events = [e for e in collector.events if e.type == "progress"]
    assert len(progress_events) >= 1


@pytest.mark.asyncio
async def test_polling_emits_failed_event_and_raises():
    """Polling should emit failure event and raise on 'failed' status."""
    resolver = AttachmentResolver(max_wait_seconds=5)
    collector = StreamCollector()
    client = _FakeClient(["parsing", "failed"])

    with pytest.raises(RuntimeError, match="Processing failed"):
        await resolver._resolve_with_polling(
            client=client,
            file_id="f-1",
            filename="report.pdf",
            query="summary",
            available_tokens=5000,
            stream=collector,
            attachment=_make_attachment("f-1"),
        )

    failed = [
        e for e in collector.events
        if e.type == "content" and e.data and e.data.get("phase") == "attachment_failed"
    ]
    assert len(failed) == 1


@pytest.mark.asyncio
async def test_polling_timeout_raises():
    """Polling should raise on timeout with appropriate streaming event."""
    resolver = AttachmentResolver(max_wait_seconds=2)
    collector = StreamCollector()
    client = _FakeClient(["parsing"] * 20)

    with pytest.raises(RuntimeError, match="Timed out"):
        await resolver._resolve_with_polling(
            client=client,
            file_id="f-1",
            filename="report.pdf",
            query="summary",
            available_tokens=5000,
            stream=collector,
            attachment=_make_attachment("f-1"),
        )

    timeout = [
        e for e in collector.events
        if e.type == "content" and e.data and e.data.get("phase") == "attachment_timeout"
    ]
    assert len(timeout) == 1


@pytest.mark.asyncio
async def test_polling_no_duplicate_progress_for_same_stage():
    """Polling should not emit duplicate progress events for the same stage."""
    resolver = AttachmentResolver(max_wait_seconds=10)
    collector = StreamCollector()
    client = _FakeClient(["parsing", "parsing", "parsing", "completed"], markdown="# ok")

    await resolver._resolve_with_polling(
        client=client,
        file_id="f-1",
        filename="report.pdf",
        query="test",
        available_tokens=5000,
        stream=collector,
        attachment=_make_attachment("f-1"),
    )

    progress_events = [e for e in collector.events if e.type == "progress"]
    assert len(progress_events) == 0


# =====================================================================
# _extract_page_progress tests
# =====================================================================


def test_extract_page_progress_from_status():
    """Should extract page progress from status sub-dict."""
    resolver = AttachmentResolver()
    pp, tp = resolver._extract_page_progress(
        {"status": {"pages_processed": 15, "total_pages": 40}}
    )
    assert pp == 15
    assert tp == 40


def test_extract_page_progress_from_data():
    """Should extract page progress from data sub-dict."""
    resolver = AttachmentResolver()
    pp, tp = resolver._extract_page_progress(
        {"data": {"pages_processed": 5, "total_pages": 20}}
    )
    assert pp == 5
    assert tp == 20


def test_extract_page_progress_from_top_level():
    """Should extract page progress from top-level keys."""
    resolver = AttachmentResolver()
    pp, tp = resolver._extract_page_progress(
        {"pages_processed": 10, "total_pages": 30}
    )
    assert pp == 10
    assert tp == 30


def test_extract_page_progress_missing():
    """Should return None,None when not present."""
    resolver = AttachmentResolver()
    pp, tp = resolver._extract_page_progress({"status": {"stage": "parsing"}})
    assert pp is None
    assert tp is None


# =====================================================================
# _build_attachment_context_section tests (in base_agent)
#
# BaseStreamingAgent has deep import chains (busibox_common, etc.)
# that aren't available outside Docker. We replicate the method logic
# here to test the annotation behaviour in isolation.
# =====================================================================


def _build_attachment_context_section(resolved_attachments: List[Dict[str, Any]]) -> List[str]:
    """Mirror of BaseStreamingAgent._build_attachment_context_section for unit testing."""
    if not resolved_attachments:
        return []

    parts: List[str] = []
    parts.append("## Attached Documents")
    parts.append("The user uploaded the following attachments for this request:")

    for idx, attachment in enumerate(resolved_attachments, start=1):
        filename = attachment.get("filename", f"attachment-{idx}")
        source_kind = attachment.get("source_kind", "document")
        parts.append(f"\n### Attachment {idx}: {filename} ({source_kind})")

        if source_kind == "image":
            image_url = attachment.get("image_url")
            if image_url:
                parts.append(f"Image URL: {image_url}")
            else:
                parts.append("Image attachment provided (no URL available).")
            continue

        if source_kind == "early_chunks":
            pages_proc = attachment.get("pages_processed")
            total_pages = attachment.get("total_pages")
            if pages_proc and total_pages:
                parts.append(
                    f"**Note: This document is still being processed. "
                    f"The content below covers approximately {pages_proc} of "
                    f"{total_pages} pages. Your answer may be incomplete — "
                    f"the full document will be available shortly for follow-up questions.**"
                )
            else:
                parts.append(
                    "**Note: This document is still being processed. "
                    "Only partial content is available below. Your answer may be "
                    "incomplete — the full document will be available shortly for "
                    "follow-up questions.**"
                )

        att_content = attachment.get("content", "")
        if isinstance(att_content, str) and att_content.strip():
            parts.append(att_content.strip())
        else:
            parts.append("No extracted text content available.")

    parts.append("")
    return parts


def test_partial_coverage_annotation_with_pages():
    """early_chunks source_kind with page metadata should produce a coverage note."""
    attachments = [
        {
            "filename": "big-report.pdf",
            "source_kind": "early_chunks",
            "content": "[Chunk 1]\nSome early content here.",
            "pages_processed": 20,
            "total_pages": 47,
        }
    ]

    parts = _build_attachment_context_section(attachments)
    text = "\n".join(parts)
    assert "still being processed" in text
    assert "20" in text
    assert "47" in text
    assert "follow-up" in text.lower()
    assert "Some early content here" in text


def test_partial_coverage_annotation_without_pages():
    """early_chunks without page metadata should still produce a generic note."""
    attachments = [
        {
            "filename": "doc.pdf",
            "source_kind": "early_chunks",
            "content": "[Chunk 1]\nPartial content.",
            "pages_processed": None,
            "total_pages": None,
        }
    ]

    parts = _build_attachment_context_section(attachments)
    text = "\n".join(parts)
    assert "still being processed" in text
    assert "partial content" in text.lower() or "Partial content" in text


def test_full_markdown_no_coverage_annotation():
    """full_markdown source_kind should NOT produce a coverage annotation."""
    attachments = [
        {
            "filename": "complete.pdf",
            "source_kind": "full_markdown",
            "content": "# Full document content",
        }
    ]

    parts = _build_attachment_context_section(attachments)
    text = "\n".join(parts)
    assert "still being processed" not in text
    assert "Full document content" in text
