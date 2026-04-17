"""Unit tests for scraper_config: headers, error classification, rate limiter, cache, proxy."""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from app.tools.scraper_config import (
    BLOCKED_HOSTNAME_PATTERNS,
    ProxyConfig,
    RateLimiter,
    ScraperErrorCode,
    UA_PROFILES,
    UAProfile,
    build_headers,
    cache_key,
    camoufox_enabled,
    classify_http_status,
    detect_cloudflare_or_captcha,
    get_proxy_config,
    get_rate_limiter,
    looks_like_pdf,
    max_body_bytes,
    pick_profile,
    should_block_request,
)

pytestmark = pytest.mark.unit


# =============================================================================
# UA profiles + header builder
# =============================================================================

class TestUAProfiles:
    def test_all_profiles_have_required_fields(self) -> None:
        for profile in UA_PROFILES:
            assert profile.user_agent, f"{profile.name} missing UA"
            assert profile.accept_language, f"{profile.name} missing locale"
            assert profile.viewport_width > 0 and profile.viewport_height > 0
            assert profile.curl_impersonate, f"{profile.name} missing curl_impersonate"
            assert profile.playwright_engine in ("chromium", "firefox", "webkit")

    def test_chromium_profiles_have_client_hints(self) -> None:
        chromium = [p for p in UA_PROFILES if p.playwright_engine == "chromium"]
        assert len(chromium) >= 2
        for p in chromium:
            assert p.sec_ch_ua, f"Chromium profile {p.name} missing Sec-Ch-Ua"
            assert p.sec_ch_ua_platform, f"Chromium profile {p.name} missing Sec-Ch-Ua-Platform"

    def test_non_chromium_profiles_omit_client_hints(self) -> None:
        # Firefox/Safari must NOT send Sec-Ch-Ua — that would be a fingerprint mismatch
        non_chromium = [p for p in UA_PROFILES if p.playwright_engine != "chromium"]
        for p in non_chromium:
            assert p.sec_ch_ua == "", f"{p.name} must not have Sec-Ch-Ua"
            assert p.sec_ch_ua_mobile == ""
            assert p.sec_ch_ua_platform == ""

    def test_pick_profile_by_name(self) -> None:
        target = UA_PROFILES[0]
        picked = pick_profile(target.name)
        assert picked.name == target.name

    def test_pick_profile_unknown_falls_back_to_random_chromium(self) -> None:
        picked = pick_profile("nonexistent_profile_xyz")
        assert picked.playwright_engine == "chromium"

    def test_pick_profile_no_name_returns_chromium(self) -> None:
        picked = pick_profile()
        assert picked.playwright_engine == "chromium"


class TestHeaderBuilder:
    def test_chromium_headers_include_client_hints(self) -> None:
        profile = next(p for p in UA_PROFILES if p.name == "chrome131_win")
        headers = build_headers(profile, "https://example.com/path")
        assert headers["User-Agent"] == profile.user_agent
        assert headers["Sec-Ch-Ua"] == profile.sec_ch_ua
        assert headers["Sec-Ch-Ua-Platform"] == profile.sec_ch_ua_platform
        assert "Sec-Fetch-Dest" in headers
        assert headers["Sec-Fetch-Site"] == "none"
        assert "br" in headers["Accept-Encoding"]
        assert "zstd" in headers["Accept-Encoding"]

    def test_firefox_headers_omit_client_hints(self) -> None:
        profile = next(p for p in UA_PROFILES if p.name == "firefox121_win")
        headers = build_headers(profile, "https://example.com/")
        assert "Sec-Ch-Ua" not in headers
        assert "Sec-Ch-Ua-Mobile" not in headers
        assert "Sec-Ch-Ua-Platform" not in headers
        assert headers["User-Agent"] == profile.user_agent

    def test_referer_same_origin_sets_sec_fetch_site_same_origin(self) -> None:
        profile = UA_PROFILES[0]
        headers = build_headers(
            profile, "https://example.com/b", referer="https://example.com/a"
        )
        assert headers["Sec-Fetch-Site"] == "same-origin"
        assert headers["Referer"] == "https://example.com/a"

    def test_referer_cross_origin_sets_sec_fetch_site_none(self) -> None:
        profile = UA_PROFILES[0]
        headers = build_headers(profile, "https://example.com/", referer="https://other.com/")
        assert headers["Sec-Fetch-Site"] == "none"
        assert headers["Referer"] == "https://other.com/"


# =============================================================================
# Error classification
# =============================================================================

class TestErrorClassification:
    def test_403_is_blocked(self) -> None:
        assert classify_http_status(403) == ScraperErrorCode.BLOCKED_403

    def test_429_is_rate_limited(self) -> None:
        assert classify_http_status(429) == ScraperErrorCode.BLOCKED_429

    def test_503_suggests_js_required(self) -> None:
        assert classify_http_status(503) == ScraperErrorCode.JS_REQUIRED

    def test_unknown_4xx_maps_to_unknown(self) -> None:
        assert classify_http_status(418) == ScraperErrorCode.UNKNOWN

    def test_cloudflare_challenge_in_body(self) -> None:
        body = '<html><head><script src="/cdn-cgi/challenge-platform/..."></script></head></html>'
        assert (
            detect_cloudflare_or_captcha(body, {"server": "cloudflare"})
            == ScraperErrorCode.BLOCKED_CLOUDFLARE
        )

    def test_cloudflare_just_a_moment(self) -> None:
        body = "<html>Just a moment... cf-ray=abc123"
        assert (
            detect_cloudflare_or_captcha(body, {"server": "cloudflare"})
            == ScraperErrorCode.BLOCKED_CLOUDFLARE
        )

    def test_turnstile_detected_as_captcha(self) -> None:
        body = '<div class="cf-turnstile" data-sitekey="xxx"></div>'
        assert detect_cloudflare_or_captcha(body, {}) == ScraperErrorCode.CAPTCHA_REQUIRED

    def test_hcaptcha_detected(self) -> None:
        body = '<div class="h-captcha" data-sitekey="xxx"></div>'
        assert detect_cloudflare_or_captcha(body, {}) == ScraperErrorCode.CAPTCHA_REQUIRED

    def test_normal_page_returns_none(self) -> None:
        body = "<html><body>Public notices listing</body></html>"
        assert detect_cloudflare_or_captcha(body, {}) is None

    def test_empty_body_returns_none(self) -> None:
        assert detect_cloudflare_or_captcha("", {}) is None


# =============================================================================
# Cache key
# =============================================================================

class TestCacheKey:
    def test_cache_key_is_deterministic(self) -> None:
        a = cache_key("https://example.com", "curl_cffi", False)
        b = cache_key("https://example.com", "curl_cffi", False)
        assert a == b
        assert a.startswith("scraper:")

    def test_cache_key_differs_by_tier(self) -> None:
        a = cache_key("https://example.com", "curl_cffi", False)
        b = cache_key("https://example.com", "playwright", False)
        assert a != b

    def test_cache_key_differs_by_render_js(self) -> None:
        a = cache_key("https://example.com", "curl_cffi", False)
        b = cache_key("https://example.com", "curl_cffi", True)
        assert a != b

    def test_cache_key_differs_by_url(self) -> None:
        a = cache_key("https://example.com/a", "curl_cffi", False)
        b = cache_key("https://example.com/b", "curl_cffi", False)
        assert a != b


# =============================================================================
# Rate limiter
# =============================================================================

class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_enforces_min_interval_per_domain(self) -> None:
        limiter = RateLimiter(
            min_interval_seconds=0.2,
            max_concurrent_per_domain=1,
            jitter_min=1.0,
            jitter_max=1.0,
        )

        start = time.monotonic()
        async with await limiter.acquire("https://example.com/a"):
            pass
        async with await limiter.acquire("https://example.com/b"):
            pass
        elapsed = time.monotonic() - start
        assert elapsed >= 0.2, f"Rate limiter did not enforce min interval (elapsed={elapsed})"

    @pytest.mark.asyncio
    async def test_different_domains_do_not_block_each_other(self) -> None:
        limiter = RateLimiter(
            min_interval_seconds=1.0,
            max_concurrent_per_domain=1,
            jitter_min=1.0,
            jitter_max=1.0,
        )

        start = time.monotonic()
        async with await limiter.acquire("https://example.com/"):
            pass
        async with await limiter.acquire("https://other-domain.com/"):
            pass
        elapsed = time.monotonic() - start
        # Two different domains should NOT serialize
        assert elapsed < 0.5, f"Different domains unexpectedly serialized (elapsed={elapsed})"

    @pytest.mark.asyncio
    async def test_concurrent_cap_blocks_third_request(self) -> None:
        limiter = RateLimiter(
            min_interval_seconds=0.0,
            max_concurrent_per_domain=2,
            jitter_min=1.0,
            jitter_max=1.0,
        )
        held: list[bool] = []

        async def worker(delay: float) -> None:
            async with await limiter.acquire("https://example.com/"):
                held.append(True)
                await asyncio.sleep(delay)
                held.pop()

        # Two long-running + one fast: the fast one should wait for one slot to free
        t = asyncio.gather(
            worker(0.2),
            worker(0.2),
            worker(0.01),
        )
        start = time.monotonic()
        await t
        elapsed = time.monotonic() - start
        assert elapsed >= 0.2, f"Concurrency cap not enforced (elapsed={elapsed})"


# =============================================================================
# Proxy config
# =============================================================================

class TestProxyConfig:
    def test_no_env_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SCRAPER_PROXY_URL", raising=False)
        monkeypatch.delenv("SCRAPER_PROXY_USERNAME", raising=False)
        monkeypatch.delenv("SCRAPER_PROXY_PASSWORD", raising=False)
        cfg = get_proxy_config()
        assert cfg.enabled is False
        assert cfg.for_curl_cffi() is None
        assert cfg.for_playwright() is None

    def test_url_only(self) -> None:
        cfg = ProxyConfig(url="http://proxy.example:8080")
        assert cfg.enabled is True
        curl = cfg.for_curl_cffi()
        assert curl == {"http": "http://proxy.example:8080", "https": "http://proxy.example:8080"}

    def test_playwright_entry_with_credentials(self) -> None:
        cfg = ProxyConfig(url="http://user:pw@proxy.example:8080")
        pw = cfg.for_playwright()
        assert pw is not None
        assert pw["server"] == "http://proxy.example:8080"
        assert pw["username"] == "user"
        assert pw["password"] == "pw"

    def test_env_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCRAPER_PROXY_URL", "http://proxy:3128")
        monkeypatch.setenv("SCRAPER_PROXY_USERNAME", "alice")
        monkeypatch.setenv("SCRAPER_PROXY_PASSWORD", "secret")
        cfg = get_proxy_config()
        assert cfg.url == "http://proxy:3128"
        assert cfg.username == "alice"
        assert cfg.password == "secret"


# =============================================================================
# Resource blocking
# =============================================================================

class TestResourceBlocking:
    def test_blocks_images_when_enabled(self) -> None:
        assert should_block_request("image", "https://example.com/foo.png", True) is True

    def test_allows_images_when_disabled(self) -> None:
        assert should_block_request("image", "https://example.com/foo.png", False) is False

    def test_blocks_analytics_always(self) -> None:
        for pattern in ("google-analytics.com", "doubleclick.net", "segment.io"):
            assert pattern in BLOCKED_HOSTNAME_PATTERNS
            url = f"https://track.{pattern}/beacon"
            # Even with block_resources=False, analytics gets blocked
            assert should_block_request("xhr", url, False) is True

    def test_allows_page_document(self) -> None:
        assert should_block_request("document", "https://example.com/", True) is False


# =============================================================================
# PDF detection
# =============================================================================

class TestPdfDetection:
    def test_content_type_application_pdf(self) -> None:
        assert looks_like_pdf("https://example.com/x", "application/pdf") is True

    def test_pdf_extension_without_content_type(self) -> None:
        assert looks_like_pdf("https://example.com/notice.pdf", None) is True

    def test_uppercase_pdf_extension(self) -> None:
        assert looks_like_pdf("https://example.com/notice.PDF", "") is True

    def test_html_page_is_not_pdf(self) -> None:
        assert looks_like_pdf("https://example.com/page", "text/html; charset=utf-8") is False

    def test_query_string_with_pdf_in_name(self) -> None:
        # Path ends in .pdf even when query string is present
        assert looks_like_pdf("https://example.com/file.pdf?v=1", None) is True


# =============================================================================
# Body size + Camoufox gate
# =============================================================================

class TestMiscConfig:
    def test_default_max_body_20mb(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SCRAPER_MAX_BODY_BYTES", raising=False)
        assert max_body_bytes() == 20 * 1024 * 1024

    def test_max_body_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCRAPER_MAX_BODY_BYTES", "1048576")
        assert max_body_bytes() == 1048576

    def test_camoufox_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ENABLE_CAMOUFOX", raising=False)
        assert camoufox_enabled() is False

    def test_camoufox_enabled_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENABLE_CAMOUFOX", "true")
        assert camoufox_enabled() is True
