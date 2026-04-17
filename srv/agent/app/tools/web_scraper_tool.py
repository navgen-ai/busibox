"""Stealth web scraper tool with tiered escalation.

Escalation tiers (in order of speed and cost):

    Tier 1  curl_cffi with Chrome TLS/JA3 impersonation
    Tier 2  Playwright + playwright-stealth (resource-blocked)
    Tier 3  Camoufox (Firefox-based anti-detect browser, optional)

The agent sees a single `web_scraper` tool. `use_browser` forces tier 2+,
`use_camoufox` forces tier 3; otherwise tier 1 is tried first and we auto-fallback
to tier 2 on 403/429/Cloudflare/timeout.

PDFs are detected by content-type and URL suffix. When a PDF is fetched and the
caller provides an `ingest_library_id`, the bytes are POSTed to the data-api
`/upload` endpoint using the agent's data-api token. The agent receives the
resulting `file_id` plus a short text preview extracted with `pypdf`.

Structured error codes (see `scraper_config.ScraperErrorCode`) let the LLM
decide whether to retry with a higher tier or give up.
"""

import asyncio
import logging
import os
import random
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from pydantic import BaseModel, Field
from pydantic_ai import RunContext, Tool

from app.tools.scraper_config import (
    BLOCKED_RESOURCE_TYPES,
    ScraperErrorCode,
    build_headers,
    cache_get,
    cache_key,
    cache_set,
    camoufox_enabled,
    classify_http_status,
    detect_cloudflare_or_captcha,
    get_proxy_config,
    get_rate_limiter,
    looks_like_pdf,
    max_body_bytes,
    pick_profile,
    should_block_request,
    UAProfile,
)
from app.tools.scraper_extract import (
    ExtractedLink,
    extract_content,
    extract_links_from_html,
    extract_title,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Output schema
# =============================================================================

class WebScraperOutput(BaseModel):
    """Structured output for the stealth web scraper."""

    success: bool = Field(description="Whether the page was successfully scraped")
    url: str = Field(description="The URL that was scraped (final URL after redirects)")
    title: str = Field(default="", description="Page title")
    content: str = Field(default="", description="Extracted text/markdown content")
    word_count: int = Field(default=0, description="Number of words in the content")
    links: List[ExtractedLink] = Field(
        default_factory=list, description="Extracted links (if requested)"
    )

    method: str = Field(
        default="curl_cffi",
        description='Tier used: "curl_cffi" | "playwright" | "camoufox" | "cache"',
    )
    extractor: str = Field(
        default="",
        description='Extractor used: "trafilatura" | "readability" | "regex" | "pdf" | ""',
    )
    error_code: str = Field(
        default=ScraperErrorCode.OK.value,
        description="Structured error code (see scraper_config.ScraperErrorCode).",
    )
    error: Optional[str] = Field(
        default=None, description="Human-readable error message if scraping failed"
    )
    from_cache: bool = Field(
        default=False, description="Whether the response was served from cache"
    )
    raw_html_len: int = Field(
        default=0,
        description="Length of the raw HTML (0 for PDFs/cache hits without raw HTML)",
    )
    file_id: Optional[str] = Field(
        default=None,
        description='For PDFs: data-api file_id when `ingest_library_id` triggered ingestion',
    )
    ingested: bool = Field(
        default=False,
        description="Whether the PDF was successfully ingested into the data-api",
    )


# =============================================================================
# Tier 1: curl_cffi (chrome TLS/JA3 impersonation)
# =============================================================================

@dataclass
class _Tier1Response:
    status: int
    url: str
    body: bytes
    text: str
    headers: Dict[str, str]
    content_type: str


async def _tier1_curl_cffi(
    url: str,
    profile: UAProfile,
    proxy: Optional[Dict[str, str]],
    timeout: float,
) -> Tuple[Optional[_Tier1Response], ScraperErrorCode, Optional[str]]:
    """Fetch the URL with curl_cffi using the profile's Chrome TLS fingerprint.

    Returns (response, error_code, error_message). On success error_code == OK.
    """
    try:
        from curl_cffi.requests import AsyncSession  # type: ignore
    except ImportError:
        return None, ScraperErrorCode.UNKNOWN, "curl_cffi is not installed"

    headers = build_headers(profile, url)
    max_bytes = max_body_bytes()

    try:
        async with AsyncSession(impersonate=profile.curl_impersonate) as session:
            response = await session.get(
                url,
                headers=headers,
                timeout=timeout,
                allow_redirects=True,
                proxies=proxy,
                max_redirects=10,
            )
    except asyncio.TimeoutError:
        return None, ScraperErrorCode.TIMEOUT, f"Timeout after {timeout}s"
    except Exception as e:  # curl_cffi raises its own errors; normalize here
        msg = str(e)
        low = msg.lower()
        if "timeout" in low or "timed out" in low:
            return None, ScraperErrorCode.TIMEOUT, msg
        if "resolve" in low or "name or service not known" in low:
            return None, ScraperErrorCode.DNS_FAILED, msg
        if "ssl" in low or "tls" in low or "certificate" in low:
            return None, ScraperErrorCode.TLS_FAILED, msg
        if "refused" in low:
            return None, ScraperErrorCode.CONNECTION_REFUSED, msg
        return None, ScraperErrorCode.UNKNOWN, msg

    # Read body, capping to max_bytes
    try:
        raw = response.content or b""
    except Exception:
        raw = b""
    if len(raw) > max_bytes:
        return None, ScraperErrorCode.BODY_TOO_LARGE, f"Response body > {max_bytes} bytes"

    try:
        text_body = response.text
    except Exception:
        text_body = raw.decode("utf-8", errors="replace")

    resp_headers: Dict[str, str] = {}
    try:
        for k, v in response.headers.items():
            resp_headers[str(k)] = str(v)
    except Exception:
        resp_headers = {}

    content_type = resp_headers.get("content-type") or resp_headers.get("Content-Type") or ""

    # Classify bad statuses
    status = response.status_code
    if status >= 400:
        code = classify_http_status(status)
        return None, code, f"HTTP {status}"

    # Detect Cloudflare/CAPTCHA in the body even on 200
    detected = detect_cloudflare_or_captcha(text_body, resp_headers)
    if detected is not None:
        return None, detected, f"Anti-bot challenge detected ({detected.value})"

    return (
        _Tier1Response(
            status=status,
            url=str(response.url),
            body=raw,
            text=text_body,
            headers=resp_headers,
            content_type=content_type,
        ),
        ScraperErrorCode.OK,
        None,
    )


# =============================================================================
# Tier 2: Playwright + stealth
# =============================================================================

async def _apply_stealth(context: Any) -> None:
    """Apply stealth patches to a Playwright context. Uses playwright-stealth if available,
    otherwise falls back to a small hand-rolled init script."""
    try:
        from playwright_stealth import Stealth  # type: ignore
        await Stealth().apply_stealth_async(context)
        return
    except ImportError:
        pass
    except Exception as e:
        logger.debug("playwright-stealth apply failed, using manual init: %s", e)

    await context.add_init_script(
        """
        // Minimal stealth shim (used only when playwright-stealth is unavailable)
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        window.chrome = window.chrome || { runtime: {} };
        """
    )


async def _install_resource_blocker(
    page: Any, block_resources: bool
) -> None:
    """Block images/fonts/CSS/analytics requests to save bandwidth and speed up pages."""

    async def _handler(route: Any) -> None:
        try:
            req = route.request
            if should_block_request(req.resource_type, req.url, block_resources):
                await route.abort()
                return
            await route.continue_()
        except Exception:
            try:
                await route.continue_()
            except Exception:
                pass

    await page.route("**/*", _handler)


@dataclass
class _BrowserResponse:
    final_url: str
    title: str
    html: str
    text_content: str
    raw_body_bytes: Optional[bytes]
    raw_body_content_type: Optional[str]


async def _tier2_playwright(
    url: str,
    profile: UAProfile,
    proxy_cfg: Any,
    include_links_preview: bool,
    block_resources: bool,
    timeout_ms: int,
) -> Tuple[Optional[_BrowserResponse], ScraperErrorCode, Optional[str]]:
    """Fetch the URL via Playwright with stealth patches and resource blocking.

    Returns the rendered HTML + evaluated body text so the caller can run
    content extraction on whichever is more useful.
    """
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except ImportError:
        return None, ScraperErrorCode.UNKNOWN, "Playwright is not installed"

    proxy_dict = proxy_cfg.for_playwright() if proxy_cfg.enabled else None

    pdf_bytes: Optional[bytes] = None
    pdf_ct: Optional[str] = None

    try:
        async with async_playwright() as p:
            browser_launcher = getattr(p, profile.playwright_engine, p.chromium)
            launch_kwargs: Dict[str, Any] = {"headless": True}
            if proxy_dict:
                launch_kwargs["proxy"] = proxy_dict
            browser = await browser_launcher.launch(**launch_kwargs)

            context = await browser.new_context(
                user_agent=profile.user_agent,
                viewport={
                    "width": profile.viewport_width,
                    "height": profile.viewport_height,
                },
                locale=profile.locale,
                timezone_id=profile.timezone_id,
                java_script_enabled=True,
            )
            await _apply_stealth(context)
            page = await context.new_page()
            await _install_resource_blocker(page, block_resources)

            # Capture PDF responses for URL if it's directly a PDF
            async def _on_response(resp: Any) -> None:
                nonlocal pdf_bytes, pdf_ct
                if pdf_bytes is not None:
                    return
                try:
                    rurl = resp.url
                    if rurl != url and not rurl.startswith(url):
                        return
                    ct = (resp.headers.get("content-type") or "").lower()
                    if "application/pdf" in ct or rurl.lower().endswith(".pdf"):
                        pdf_bytes = await resp.body()
                        pdf_ct = ct or "application/pdf"
                except Exception:
                    pass

            page.on("response", _on_response)

            try:
                await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            except Exception as e:
                msg = str(e)
                low = msg.lower()
                if "timeout" in low:
                    code = ScraperErrorCode.TIMEOUT
                elif "net::err_name_not_resolved" in low:
                    code = ScraperErrorCode.DNS_FAILED
                elif "net::err_connection_refused" in low:
                    code = ScraperErrorCode.CONNECTION_REFUSED
                else:
                    code = ScraperErrorCode.UNKNOWN
                await browser.close()
                return None, code, msg

            # Random human-like settle delay
            await asyncio.sleep(random.uniform(0.8, 2.5))

            if pdf_bytes is not None:
                final_url = str(page.url)
                await browser.close()
                return (
                    _BrowserResponse(
                        final_url=final_url,
                        title="",
                        html="",
                        text_content="",
                        raw_body_bytes=pdf_bytes,
                        raw_body_content_type=pdf_ct or "application/pdf",
                    ),
                    ScraperErrorCode.OK,
                    None,
                )

            try:
                title = await page.title()
            except Exception:
                title = ""

            try:
                html = await page.content()
            except Exception:
                html = ""

            # Also grab the evaluated text — sometimes trafilatura does better on
            # raw HTML, sometimes on the text the browser actually rendered.
            try:
                text_content = await page.evaluate(
                    """() => {
                        const remove = document.querySelectorAll(
                            'script, style, nav, header, footer, aside, noscript, iframe'
                        );
                        remove.forEach(el => el.remove());
                        return document.body ? document.body.innerText : '';
                    }"""
                )
            except Exception:
                text_content = ""

            final_url = str(page.url)

            # Cloudflare / captcha detection on the rendered page
            detected = detect_cloudflare_or_captcha(html, {})
            await browser.close()

            if detected is not None:
                return None, detected, f"Anti-bot challenge detected ({detected.value})"

            return (
                _BrowserResponse(
                    final_url=final_url,
                    title=title,
                    html=html,
                    text_content=text_content,
                    raw_body_bytes=None,
                    raw_body_content_type=None,
                ),
                ScraperErrorCode.OK,
                None,
            )

    except Exception as e:
        logger.error("Tier 2 Playwright failed for %s: %s", url, e, exc_info=True)
        return None, ScraperErrorCode.UNKNOWN, str(e)


# =============================================================================
# Tier 3: Camoufox (opt-in)
# =============================================================================

async def _tier3_camoufox(
    url: str,
    profile: UAProfile,
    proxy_cfg: Any,
    block_resources: bool,
    timeout_ms: int,
) -> Tuple[Optional[_BrowserResponse], ScraperErrorCode, Optional[str]]:
    """Nuclear option: Camoufox (anti-detect Firefox) with humanize+geoip.

    Requires ENABLE_CAMOUFOX=true and the `camoufox` Python package installed.
    Slow (~10-15s per page) — use sparingly.
    """
    if not camoufox_enabled():
        return None, ScraperErrorCode.UNKNOWN, (
            "Camoufox is disabled. Set ENABLE_CAMOUFOX=true and install the "
            "camoufox package to enable tier 3."
        )
    try:
        from camoufox.async_api import AsyncCamoufox  # type: ignore
    except ImportError:
        return None, ScraperErrorCode.UNKNOWN, "camoufox package is not installed"

    proxy_dict = proxy_cfg.for_playwright() if proxy_cfg.enabled else None

    try:
        cam_kwargs: Dict[str, Any] = {
            "headless": True,
            "humanize": True,
            "geoip": True,
            "locale": [profile.locale],
        }
        if proxy_dict:
            cam_kwargs["proxy"] = proxy_dict

        async with AsyncCamoufox(**cam_kwargs) as browser:
            page = await browser.new_page()
            await _install_resource_blocker(page, block_resources)

            try:
                await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            except Exception as e:
                return None, ScraperErrorCode.TIMEOUT if "timeout" in str(e).lower() else ScraperErrorCode.UNKNOWN, str(e)

            await asyncio.sleep(random.uniform(1.0, 3.0))

            try:
                title = await page.title()
            except Exception:
                title = ""
            try:
                html = await page.content()
            except Exception:
                html = ""
            try:
                text_content = await page.evaluate(
                    """() => {
                        const remove = document.querySelectorAll(
                            'script, style, nav, header, footer, aside, noscript, iframe'
                        );
                        remove.forEach(el => el.remove());
                        return document.body ? document.body.innerText : '';
                    }"""
                )
            except Exception:
                text_content = ""

            final_url = str(page.url)

            detected = detect_cloudflare_or_captcha(html, {})
            if detected is not None:
                return None, detected, f"Anti-bot challenge detected ({detected.value})"

            return (
                _BrowserResponse(
                    final_url=final_url,
                    title=title,
                    html=html,
                    text_content=text_content,
                    raw_body_bytes=None,
                    raw_body_content_type=None,
                ),
                ScraperErrorCode.OK,
                None,
            )
    except Exception as e:
        logger.error("Tier 3 Camoufox failed for %s: %s", url, e, exc_info=True)
        return None, ScraperErrorCode.UNKNOWN, str(e)


# =============================================================================
# PDF handling
# =============================================================================

def _pdf_quick_preview(pdf_bytes: bytes, max_chars: int) -> str:
    """Extract up to `max_chars` of text from a PDF using pypdf (fast, no OCR).

    For image-only PDFs this will return little/nothing — the data-api ingest
    worker handles those via full OCR downstream.
    """
    try:
        import io
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        logger.debug("pypdf not installed; PDF preview unavailable")
        return ""

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        chunks: List[str] = []
        total = 0
        for page in reader.pages[:20]:
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            if not text.strip():
                continue
            chunks.append(text.strip())
            total += len(text)
            if total >= max_chars:
                break
        preview = "\n\n".join(chunks)
        if len(preview) > max_chars:
            preview = preview[:max_chars] + "\n\n[Preview truncated]"
        return preview
    except Exception as e:
        logger.debug("PDF preview extraction failed: %s", e)
        return ""


def _filename_from_url(url: str) -> str:
    try:
        path = urlparse(url).path
        base = path.rsplit("/", 1)[-1] or "document"
    except Exception:
        base = "document"
    if not base.lower().endswith(".pdf"):
        base = base + ".pdf"
    return base


async def _ingest_pdf_to_data_api(
    pdf_bytes: bytes,
    source_url: str,
    library_id: str,
    visibility: str,
    data_api_url: str,
    token: str,
    tier_used: str,
) -> Tuple[Optional[str], ScraperErrorCode, Optional[str]]:
    """POST a scraped PDF to the data-api /upload endpoint.

    Returns (file_id, error_code, error_message). On success the error code is OK.
    """
    import httpx  # already a dependency

    filename = _filename_from_url(source_url)
    metadata = {
        "source": "scraper",
        "source_url": source_url,
        "scraped_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "tier_used": tier_used,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            files = {
                "file": (filename, pdf_bytes, "application/pdf"),
            }
            data = {
                "metadata": __import__("json").dumps(metadata),
                "visibility": visibility,
                "library_id": library_id,
            }
            upload_url = data_api_url.rstrip("/") + "/upload"
            resp = await client.post(
                upload_url,
                files=files,
                data=data,
                headers={"Authorization": f"Bearer {token}"},
            )
    except Exception as e:
        return None, ScraperErrorCode.PDF_INGEST_FAILED, f"Upload request failed: {e}"

    if resp.status_code >= 400:
        return None, ScraperErrorCode.PDF_INGEST_FAILED, (
            f"Upload returned {resp.status_code}: {resp.text[:200]}"
        )
    try:
        payload = resp.json()
    except Exception:
        return None, ScraperErrorCode.PDF_INGEST_FAILED, "Upload returned non-JSON response"

    file_id = payload.get("fileId") or payload.get("file_id") or payload.get("id")
    if not file_id:
        return None, ScraperErrorCode.PDF_INGEST_FAILED, "Upload response missing fileId"
    return str(file_id), ScraperErrorCode.OK, None


def _resolve_ingest_token(ctx: Optional[RunContext[Any]]) -> Optional[str]:
    """Extract the user's data-api token from the agent run context, if available."""
    if ctx is None or ctx.deps is None:
        return None
    deps = ctx.deps
    client = getattr(deps, "busibox_client", None)
    if client is None:
        return None
    tokens = getattr(client, "_tokens", {}) or {}
    return tokens.get("data-api") or getattr(client, "_default_token", None)


# =============================================================================
# Main entry point
# =============================================================================

_MIN_USEFUL_CONTENT_LEN = 120  # anything less is probably a JS shell


async def _scrape_webpage_impl(
    ctx: Optional[RunContext[Any]],
    url: str,
    include_links: bool = False,
    max_content_length: int = 10000,
    use_browser: bool = False,
    use_camoufox: bool = False,
    cache_ttl: int = 3600,
    ingest_library_id: Optional[str] = None,
    extract_mode: str = "auto",
    block_resources: bool = True,
    profile_name: Optional[str] = None,
) -> WebScraperOutput:
    """Shared implementation used by both the pydantic_ai tool and direct callers.

    `ctx` may be None when invoked directly from a workflow step (no agent run
    context) — in that case PDF ingestion is skipped because we can't reach the
    data-api without a user token.
    """
    # ---- Validate URL ----
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return WebScraperOutput(
            success=False,
            url=url,
            error_code=ScraperErrorCode.INVALID_URL.value,
            error="Invalid URL. Provide a complete URL starting with http:// or https://",
        )
    if parsed.scheme not in ("http", "https"):
        return WebScraperOutput(
            success=False,
            url=url,
            error_code=ScraperErrorCode.INVALID_URL.value,
            error="Only HTTP and HTTPS URLs are supported",
        )

    render_js = bool(use_browser or use_camoufox)
    tier_label = "camoufox" if use_camoufox else ("playwright" if use_browser else "curl_cffi")
    ckey = cache_key(url, tier_label, render_js)

    # ---- Cache hit? ----
    if cache_ttl > 0:
        cached = await cache_get(ckey)
        if cached:
            try:
                cached["from_cache"] = True
                cached["method"] = "cache"
                return WebScraperOutput(**cached)
            except Exception as e:
                logger.debug("Cache payload invalid, ignoring: %s", e)

    profile = pick_profile(profile_name)
    proxy_cfg = get_proxy_config()
    rate_limiter = get_rate_limiter()

    ingest_library_id = ingest_library_id or (os.getenv("SCRAPER_DEFAULT_LIBRARY_ID") or None)
    pdf_visibility = os.getenv("SCRAPER_PDF_VISIBILITY", "personal")

    data_api_token = _resolve_ingest_token(ctx)
    data_api_url = os.getenv("DATA_API_URL") or os.getenv("BUSIBOX_DATA_API_URL")

    async with await rate_limiter.acquire(url):
        result = await _do_scrape(
            url=url,
            profile=profile,
            proxy_cfg=proxy_cfg,
            use_browser=use_browser,
            use_camoufox=use_camoufox,
            include_links=include_links,
            max_content_length=max_content_length,
            block_resources=block_resources,
            extract_mode=extract_mode,
            ingest_library_id=ingest_library_id,
            pdf_visibility=pdf_visibility,
            data_api_url=data_api_url,
            data_api_token=data_api_token,
        )

    # Cache successful scrapes (and PDF ingests — file_id is useful to recall)
    if cache_ttl > 0 and (result.success or result.error_code == ScraperErrorCode.PDF_INGESTED.value):
        try:
            payload = result.model_dump()
            payload["from_cache"] = False
            await cache_set(ckey, payload, cache_ttl)
        except Exception as e:
            logger.debug("Cache write failed: %s", e)

    return result


async def _do_scrape(
    *,
    url: str,
    profile: UAProfile,
    proxy_cfg: Any,
    use_browser: bool,
    use_camoufox: bool,
    include_links: bool,
    max_content_length: int,
    block_resources: bool,
    extract_mode: str,
    ingest_library_id: Optional[str],
    pdf_visibility: str,
    data_api_url: Optional[str],
    data_api_token: Optional[str],
) -> WebScraperOutput:
    """Core dispatch — Tier 1 -> Tier 2 -> Tier 3 with auto-escalation."""

    proxy_for_curl = proxy_cfg.for_curl_cffi() if proxy_cfg.enabled else None

    # ---- Decide entry tier ----
    if use_camoufox:
        start_tier = 3
    elif use_browser:
        start_tier = 2
    else:
        start_tier = 1

    tier1_error: Optional[Tuple[ScraperErrorCode, str]] = None
    tier2_error: Optional[Tuple[ScraperErrorCode, str]] = None

    # ========== Tier 1 ==========
    if start_tier == 1:
        t1, code, err = await _tier1_curl_cffi(
            url=url,
            profile=profile,
            proxy=proxy_for_curl,
            timeout=30.0,
        )
        if t1 is not None and code == ScraperErrorCode.OK:
            # PDF path
            if looks_like_pdf(t1.url, t1.content_type):
                return await _build_pdf_output(
                    source_url=t1.url,
                    pdf_bytes=t1.body,
                    tier_used="curl_cffi",
                    max_content_length=max_content_length,
                    ingest_library_id=ingest_library_id,
                    pdf_visibility=pdf_visibility,
                    data_api_url=data_api_url,
                    data_api_token=data_api_token,
                )

            # HTML path
            html = t1.text
            content, extractor = extract_content(html, t1.url, mode=extract_mode)
            if len(content) < _MIN_USEFUL_CONTENT_LEN and "javascript" in html.lower():
                # Classic "JS required" shell — escalate
                tier1_error = (ScraperErrorCode.JS_REQUIRED, "Page content too short; JS likely required")
            else:
                return _build_html_output(
                    tier="curl_cffi",
                    extractor=extractor,
                    final_url=t1.url,
                    title=extract_title(html),
                    content=content,
                    raw_html_len=len(html),
                    html_for_links=html,
                    include_links=include_links,
                    max_content_length=max_content_length,
                )
        else:
            tier1_error = (code, err or "Tier 1 failed")

    # ========== Tier 2 ==========
    if start_tier <= 2:
        t2, code, err = await _tier2_playwright(
            url=url,
            profile=profile,
            proxy_cfg=proxy_cfg,
            include_links_preview=include_links,
            block_resources=block_resources,
            timeout_ms=45000,
        )
        if t2 is not None and code == ScraperErrorCode.OK:
            # PDF captured by the response listener
            if t2.raw_body_bytes is not None:
                return await _build_pdf_output(
                    source_url=t2.final_url or url,
                    pdf_bytes=t2.raw_body_bytes,
                    tier_used="playwright",
                    max_content_length=max_content_length,
                    ingest_library_id=ingest_library_id,
                    pdf_visibility=pdf_visibility,
                    data_api_url=data_api_url,
                    data_api_token=data_api_token,
                )

            html = t2.html
            # Prefer extracting from raw HTML, then fall back to the evaluated body text
            content, extractor = extract_content(html, t2.final_url, mode=extract_mode)
            if len(content) < _MIN_USEFUL_CONTENT_LEN and t2.text_content:
                content = t2.text_content
                extractor = "playwright_body"

            if len(content) < _MIN_USEFUL_CONTENT_LEN and start_tier < 3 and camoufox_enabled():
                tier2_error = (ScraperErrorCode.JS_REQUIRED, "Content too short after Playwright render")
            else:
                return _build_html_output(
                    tier="playwright",
                    extractor=extractor,
                    final_url=t2.final_url or url,
                    title=t2.title,
                    content=content,
                    raw_html_len=len(html),
                    html_for_links=html,
                    include_links=include_links,
                    max_content_length=max_content_length,
                )
        else:
            tier2_error = (code, err or "Tier 2 failed")
            # Only escalate on anti-bot signals
            should_escalate = code in (
                ScraperErrorCode.BLOCKED_CLOUDFLARE,
                ScraperErrorCode.CAPTCHA_REQUIRED,
                ScraperErrorCode.BLOCKED_403,
            )
            if not should_escalate and start_tier < 3:
                return WebScraperOutput(
                    success=False,
                    url=url,
                    method="playwright",
                    error_code=code.value,
                    error=err,
                )

    # ========== Tier 3 ==========
    if start_tier <= 3 and camoufox_enabled():
        t3, code, err = await _tier3_camoufox(
            url=url,
            profile=profile,
            proxy_cfg=proxy_cfg,
            block_resources=block_resources,
            timeout_ms=60000,
        )
        if t3 is not None and code == ScraperErrorCode.OK:
            html = t3.html
            content, extractor = extract_content(html, t3.final_url, mode=extract_mode)
            if len(content) < _MIN_USEFUL_CONTENT_LEN and t3.text_content:
                content = t3.text_content
                extractor = "camoufox_body"
            return _build_html_output(
                tier="camoufox",
                extractor=extractor,
                final_url=t3.final_url or url,
                title=t3.title,
                content=content,
                raw_html_len=len(html),
                html_for_links=html,
                include_links=include_links,
                max_content_length=max_content_length,
            )
        # Fall through to error reporting
        return WebScraperOutput(
            success=False,
            url=url,
            method="camoufox",
            error_code=(code or ScraperErrorCode.UNKNOWN).value,
            error=err or "Tier 3 failed",
        )

    # ---- Exhausted ----
    final_code = (tier2_error or tier1_error or (ScraperErrorCode.UNKNOWN, "Scraper failed"))
    return WebScraperOutput(
        success=False,
        url=url,
        method=("playwright" if tier2_error else "curl_cffi"),
        error_code=final_code[0].value,
        error=final_code[1],
    )


def _build_html_output(
    *,
    tier: str,
    extractor: str,
    final_url: str,
    title: str,
    content: str,
    raw_html_len: int,
    html_for_links: str,
    include_links: bool,
    max_content_length: int,
) -> WebScraperOutput:
    truncated_content = content
    if len(truncated_content) > max_content_length:
        truncated_content = truncated_content[:max_content_length] + "\n\n[Content truncated...]"

    links: List[ExtractedLink] = []
    if include_links:
        links = extract_links_from_html(html_for_links, final_url)

    return WebScraperOutput(
        success=True,
        url=final_url,
        title=title,
        content=truncated_content,
        word_count=len(truncated_content.split()),
        links=links,
        method=tier,
        extractor=extractor,
        error_code=ScraperErrorCode.OK.value,
        error=None,
        from_cache=False,
        raw_html_len=raw_html_len,
    )


async def _build_pdf_output(
    *,
    source_url: str,
    pdf_bytes: bytes,
    tier_used: str,
    max_content_length: int,
    ingest_library_id: Optional[str],
    pdf_visibility: str,
    data_api_url: Optional[str],
    data_api_token: Optional[str],
) -> WebScraperOutput:
    """Assemble a WebScraperOutput for a PDF, ingesting into data-api when configured."""
    preview = _pdf_quick_preview(pdf_bytes, max_content_length)

    file_id: Optional[str] = None
    ingested = False
    error_code = ScraperErrorCode.OK
    error_message: Optional[str] = None

    if ingest_library_id and data_api_url and data_api_token:
        file_id, up_code, up_err = await _ingest_pdf_to_data_api(
            pdf_bytes=pdf_bytes,
            source_url=source_url,
            library_id=ingest_library_id,
            visibility=pdf_visibility,
            data_api_url=data_api_url,
            token=data_api_token,
            tier_used=tier_used,
        )
        if file_id:
            ingested = True
            error_code = ScraperErrorCode.PDF_INGESTED
        else:
            error_code = up_code
            error_message = up_err
    elif ingest_library_id and not (data_api_url and data_api_token):
        # User wanted ingestion but we can't do it. Return preview with a clear signal.
        error_code = ScraperErrorCode.PDF_INGEST_FAILED
        error_message = (
            "PDF ingestion requested but no data-api token / URL available in agent context"
        )

    return WebScraperOutput(
        success=True,
        url=source_url,
        title=_filename_from_url(source_url),
        content=preview,
        word_count=len(preview.split()),
        links=[],
        method=tier_used,
        extractor="pdf",
        error_code=error_code.value,
        error=error_message,
        from_cache=False,
        raw_html_len=0,
        file_id=file_id,
        ingested=ingested,
    )


# =============================================================================
# Public entry points
# =============================================================================

async def scrape_webpage(
    ctx: Optional[RunContext[Any]] = None,
    url: str = "",
    include_links: bool = False,
    max_content_length: int = 10000,
    use_browser: bool = False,
    use_camoufox: bool = False,
    cache_ttl: int = 3600,
    ingest_library_id: Optional[str] = None,
    extract_mode: str = "auto",
    block_resources: bool = True,
    profile_name: Optional[str] = None,
) -> WebScraperOutput:
    """Direct-callable entrypoint. Accepts optional ctx (None when called from
    workflow engine without an agent run context). Required-ctx tool wrapper
    below is used by pydantic_ai agents."""
    return await _scrape_webpage_impl(
        ctx=ctx,
        url=url,
        include_links=include_links,
        max_content_length=max_content_length,
        use_browser=use_browser,
        use_camoufox=use_camoufox,
        cache_ttl=cache_ttl,
        ingest_library_id=ingest_library_id,
        extract_mode=extract_mode,
        block_resources=block_resources,
        profile_name=profile_name,
    )


async def _scrape_webpage_tool(
    ctx: RunContext[Any],
    url: str,
    include_links: bool = False,
    max_content_length: int = 10000,
    use_browser: bool = False,
    use_camoufox: bool = False,
    cache_ttl: int = 3600,
    ingest_library_id: Optional[str] = None,
    extract_mode: str = "auto",
    block_resources: bool = True,
    profile_name: Optional[str] = None,
) -> WebScraperOutput:
    """Pydantic-AI-compatible tool wrapper.

    Args:
        url: URL to scrape.
        include_links: If true, extract up to 50 links.
        max_content_length: Cap on returned content (characters).
        use_browser: Force Tier 2 (Playwright+stealth). Use after a
            BLOCKED_403 / JS_REQUIRED signal from a prior call.
        use_camoufox: Force Tier 3 (Camoufox anti-detect Firefox). Only use
            after BLOCKED_CLOUDFLARE / CAPTCHA_REQUIRED. Requires
            ENABLE_CAMOUFOX=true on the server.
        cache_ttl: Response cache TTL in seconds. 0 = bypass cache.
        ingest_library_id: If the URL turns out to be a PDF, upload it to this
            data-api library_id and return the resulting file_id. You can then
            use document_search tools against it.
        extract_mode: "auto" (default), "article" (favor precision), or
            "full_page" (keep all text including nav/footer).
        block_resources: Block images/fonts/CSS/analytics in browser tiers.
        profile_name: Override UA profile (chrome131_win, chrome131_mac, etc).
    """
    return await _scrape_webpage_impl(
        ctx=ctx,
        url=url,
        include_links=include_links,
        max_content_length=max_content_length,
        use_browser=use_browser,
        use_camoufox=use_camoufox,
        cache_ttl=cache_ttl,
        ingest_library_id=ingest_library_id,
        extract_mode=extract_mode,
        block_resources=block_resources,
        profile_name=profile_name,
    )


# =============================================================================
# Tool registration
# =============================================================================

web_scraper_tool = Tool(
    _scrape_webpage_tool,
    takes_ctx=True,
    name="web_scraper",
    description="""Fetch and extract content from a web page URL using a tiered stealth engine.

Tiers (auto-escalates on 403 / Cloudflare / CAPTCHA / timeout):
  1. curl_cffi (Chrome TLS fingerprint) — fast, beats Level 1 bot detection
  2. Playwright + stealth patches — handles JS-rendered / SPA pages
  3. Camoufox — anti-detect Firefox, nuclear option (opt-in, slow)

Behavior:
- PDF URLs are auto-detected. If `ingest_library_id` is provided, the PDF is
  uploaded to the data-api for chunking + embedding and you get back a `file_id`
  plus a short text preview extracted with pypdf.
- Responses are cached in Redis for `cache_ttl` seconds (default 3600). Set
  `cache_ttl=0` to bypass.
- `extract_mode="auto"` runs trafilatura (preserves tables/lists/headings),
  falls back to readability, then regex. Use `"full_page"` for raw text.

Structured output includes `error_code` (OK, BLOCKED_403, BLOCKED_CLOUDFLARE,
CAPTCHA_REQUIRED, JS_REQUIRED, PDF_INGESTED, PDF_INGEST_FAILED, TIMEOUT, etc.)
so you can decide your next action without parsing error strings.

Parameter tips:
- `use_browser=true`: force tier 2 (use after a BLOCKED_403 / JS_REQUIRED signal).
- `use_camoufox=true`: force tier 3 (only after BLOCKED_CLOUDFLARE / CAPTCHA_REQUIRED).
- `include_links=true`: return up to 50 in-page links.
- `block_resources=true` (default): skip images/CSS/fonts in browser tiers.
""",
)
