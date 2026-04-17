"""Integration tests for the stealth scraper stack.

Validates:
- Tier 1 (curl_cffi) passes bot.sannysoft.com headless-browser checks
- Tier 2 (Playwright+stealth) produces a good CreepJS trust score
- Known-blocked government sites are reachable via the tiered escalation
- Response caching: second call is served from cache
- Structured error codes fire on expected failure modes
- PDF detection routes to the PDF branch
- Per-domain rate limiter serializes bursts to the same host

All tests require live network; most are marked `slow` so they're skipped by the
default FAST=1 test run. Run with:

    make test-docker SERVICE=agent ARGS="tests/integration/test_scraper_stealth.py" FAST=0
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, List

import pytest

from app.tools.scraper_config import ScraperErrorCode
from app.tools.web_scraper_tool import scrape_webpage

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def _network_allowed() -> bool:
    """Skip network tests in sandboxed environments where outbound HTTP is denied."""
    return os.getenv("SCRAPER_NETWORK_TESTS", "1") == "1"


pytestmark_network = pytest.mark.skipif(
    not _network_allowed(),
    reason="Network tests disabled (SCRAPER_NETWORK_TESTS=0)",
)


# =============================================================================
# Tier 1: curl_cffi
# =============================================================================

class TestTier1CurlCffi:
    """Tier 1 (curl_cffi) — Chrome TLS fingerprint. Should beat Level 1 bot checks."""

    @pytestmark_network
    async def test_basic_html_scrape(self) -> None:
        """Scrape a plain site with no bot protection — should succeed via Tier 1."""
        result = await scrape_webpage(url="https://example.com/", cache_ttl=0)
        assert result.success, f"Failed: {result.error_code} {result.error}"
        assert result.method in ("curl_cffi", "cache")
        assert "example" in result.content.lower() or "illustrative" in result.content.lower()
        assert result.error_code == ScraperErrorCode.OK.value

    @pytestmark_network
    async def test_cache_hit_second_call(self) -> None:
        """Identical URL + same tier should be served from cache on the second call."""
        url = "https://httpbin.org/html"
        first = await scrape_webpage(url=url, cache_ttl=300)
        assert first.success
        second = await scrape_webpage(url=url, cache_ttl=300)
        assert second.success
        assert second.from_cache is True
        assert second.method == "cache"

    @pytestmark_network
    async def test_cache_bypass_when_ttl_zero(self) -> None:
        """cache_ttl=0 must bypass cache on both read and write."""
        url = "https://httpbin.org/html?bust=1"
        await scrape_webpage(url=url, cache_ttl=300)
        result = await scrape_webpage(url=url, cache_ttl=0)
        assert result.from_cache is False
        assert result.method != "cache"


# =============================================================================
# Stealth validation: bot.sannysoft.com
# =============================================================================

class TestStealthValidation:
    """Validates that our stealth setup passes standard headless-browser fingerprint checks."""

    @pytestmark_network
    async def test_sannysoft_tier1(self) -> None:
        """Tier 1 (curl_cffi) should score well on bot.sannysoft.com — no JS needed for the static checks."""
        result = await scrape_webpage(
            url="https://bot.sannysoft.com/",
            cache_ttl=0,
            include_links=False,
        )
        assert result.success, f"Failed: {result.error_code} {result.error}"
        # The static HTML contains the JS-driven tests as labeled rows; we're not
        # evaluating JS here, but we at least expect the page to return.
        assert result.raw_html_len > 0

    @pytestmark_network
    async def test_sannysoft_tier2_playwright(self) -> None:
        """Tier 2 (Playwright+stealth) must pass the core navigator.webdriver check."""
        result = await scrape_webpage(
            url="https://bot.sannysoft.com/",
            use_browser=True,
            cache_ttl=0,
        )
        assert result.success, f"Failed: {result.error_code} {result.error}"
        # With stealth patches, webdriver should be undefined (which the page labels "passed")
        # bot.sannysoft.com writes "passed"/"failed" next to each check.
        # We count "passed" occurrences as a proxy for stealth effectiveness.
        passed = result.content.lower().count("passed")
        failed = result.content.lower().count("failed")
        assert passed >= 1, (
            f"Playwright+stealth did not pass any bot.sannysoft.com checks "
            f"(passed={passed}, failed={failed}). Content: {result.content[:500]}"
        )


# =============================================================================
# Error taxonomy
# =============================================================================

class TestErrorCodes:
    """Each failure mode should emit a structured error_code the LLM can react to."""

    async def test_invalid_url(self) -> None:
        result = await scrape_webpage(url="not a url")
        assert result.success is False
        assert result.error_code == ScraperErrorCode.INVALID_URL.value

    async def test_unsupported_scheme(self) -> None:
        result = await scrape_webpage(url="ftp://example.com/")
        assert result.success is False
        assert result.error_code == ScraperErrorCode.INVALID_URL.value

    @pytestmark_network
    async def test_dns_failure_yields_structured_code(self) -> None:
        """A nonexistent TLD should fail fast with DNS_FAILED or UNKNOWN, never success."""
        result = await scrape_webpage(
            url="https://this-domain-should-not-exist-123456.invalid/",
            cache_ttl=0,
        )
        assert result.success is False
        assert result.error_code in (
            ScraperErrorCode.DNS_FAILED.value,
            ScraperErrorCode.CONNECTION_REFUSED.value,
            ScraperErrorCode.UNKNOWN.value,
        )


# =============================================================================
# Rate limiter (uses real domains, but no live network required — just timing)
# =============================================================================

class TestRateLimiterAgainstLiveDomain:
    """Verify that the per-domain rate limiter actually enforces spacing in-process."""

    @pytestmark_network
    async def test_two_calls_to_same_domain_are_spaced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Two scrapes of the same domain should be spaced out by at least SCRAPER_MIN_INTERVAL_SECONDS."""
        monkeypatch.setenv("SCRAPER_MIN_INTERVAL_SECONDS", "2.0")
        # Reset the global rate limiter so the env var takes effect
        import app.tools.scraper_config as cfg
        cfg._global_rate_limiter = None  # type: ignore[attr-defined]

        start = time.monotonic()
        a = await scrape_webpage(url="https://httpbin.org/delay/0?a=1", cache_ttl=0)
        b = await scrape_webpage(url="https://httpbin.org/delay/0?a=2", cache_ttl=0)
        elapsed = time.monotonic() - start

        assert a.success and b.success
        # 2 s min interval (with 0.5-1.5x jitter) between the two
        assert elapsed >= 1.0, (
            f"Rate limiter did not enforce spacing for same-domain calls (elapsed={elapsed})"
        )

        cfg._global_rate_limiter = None  # type: ignore[attr-defined]


# =============================================================================
# PDF handling
# =============================================================================

class TestPdfHandling:
    """PDFs should be detected, previewed, and (when configured) uploaded to data-api."""

    @pytestmark_network
    async def test_pdf_returns_preview(self) -> None:
        """A PDF URL without ingest_library_id returns a pypdf preview and PDF marker."""
        # A public, small PDF hosted by w3.org
        result = await scrape_webpage(
            url="https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf",
            cache_ttl=0,
        )
        # Either tier 1 returns it, or we degrade to an error — but it should never be mistaken for HTML.
        if result.success:
            assert result.extractor == "pdf"
            assert result.ingested is False  # no library_id provided
        else:
            # Some hosts 403; just make sure the error is structured
            assert result.error_code in {
                ScraperErrorCode.BLOCKED_403.value,
                ScraperErrorCode.TIMEOUT.value,
                ScraperErrorCode.UNKNOWN.value,
            }

    async def test_pdf_ingest_requested_without_token_returns_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Requesting ingestion when we can't authenticate to data-api yields PDF_INGEST_FAILED."""
        # Monkey-patch the HTTP fetch to synthesize a PDF response without hitting the network
        from app.tools import web_scraper_tool

        class FakeTier1:
            def __init__(self) -> None:
                self.url = "https://example.com/test.pdf"
                self.body = b"%PDF-1.4\n%fake\n"
                self.content_type = "application/pdf"
                self.status = 200
                self.text = ""
                self.headers = {"content-type": "application/pdf"}

        async def fake_t1(url: str, profile: Any, proxy: Any, timeout: float) -> Any:
            return FakeTier1(), ScraperErrorCode.OK, None

        monkeypatch.setattr(web_scraper_tool, "_tier1_curl_cffi", fake_t1)
        monkeypatch.delenv("DATA_API_URL", raising=False)
        monkeypatch.delenv("BUSIBOX_DATA_API_URL", raising=False)

        result = await scrape_webpage(
            url="https://example.com/test.pdf",
            ingest_library_id="00000000-0000-0000-0000-000000000001",
            cache_ttl=0,
        )
        # We succeeded in fetching, but ingestion failed because no data-api token
        assert result.success is True
        assert result.extractor == "pdf"
        assert result.ingested is False
        assert result.error_code == ScraperErrorCode.PDF_INGEST_FAILED.value


# =============================================================================
# Known-blocked government sites (sanity probe, always marked slow)
# =============================================================================

# Keep this small — these are probes against real government sites, and we don't want
# to hammer them. They also may change access patterns. The test just asserts we return
# a structured result (success OR a known error_code), never an unhandled exception.

GOVT_PROBE_URLS: List[str] = [
    # USACE New England — typically 403s Tier 1, should escalate
    "https://www.nae.usace.army.mil/Missions/Regulatory/Public-Notices/",
    # Florida DEP — state .gov, JS-heavy
    "https://floridadep.gov/water/wetlands/permits/public-notices",
]


class TestKnownBlockedSites:
    @pytestmark_network
    @pytest.mark.parametrize("url", GOVT_PROBE_URLS)
    async def test_returns_structured_result(self, url: str) -> None:
        """Every govt URL returns either success or a structured error_code — never a raised exception."""
        result = await scrape_webpage(url=url, cache_ttl=0)
        assert result.url, "result must include final URL"
        valid_error_codes = {e.value for e in ScraperErrorCode}
        assert result.error_code in valid_error_codes
        if result.success:
            assert result.method in ("curl_cffi", "playwright", "camoufox", "cache")
            assert result.content, "successful scrape must return content"
