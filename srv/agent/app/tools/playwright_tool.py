"""Playwright browser tool with stealth patches, resource blocking, and proxy support.

Used by agents that need explicit interaction (clicking, typing, scrolling) or
screenshots. For plain content extraction, prefer `web_scraper` which auto-escalates
through curl_cffi -> Playwright -> Camoufox.
"""

import asyncio
import logging
import random
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

from pydantic import BaseModel, Field
from pydantic_ai import Tool

from app.tools.scraper_config import (
    ScraperErrorCode,
    detect_cloudflare_or_captcha,
    get_proxy_config,
    get_rate_limiter,
    pick_profile,
    should_block_request,
)
from app.tools.scraper_extract import ExtractedLink, extract_content

logger = logging.getLogger(__name__)


class PlaywrightBrowserOutput(BaseModel):
    """Structured output for the interactive Playwright browser tool."""

    success: bool = Field(description="Whether the page was successfully loaded")
    url: str = Field(description="Final URL after redirects / navigation")
    title: str = Field(default="", description="Page title")
    content: str = Field(default="", description="Extracted text content")
    links: List[ExtractedLink] = Field(
        default_factory=list, description="Extracted links (if requested)"
    )
    screenshot_path: Optional[str] = Field(
        default=None, description="Path to screenshot file (if taken)"
    )
    error_code: str = Field(
        default=ScraperErrorCode.OK.value, description="Structured error code"
    )
    error: Optional[str] = Field(default=None, description="Error message if browsing failed")


async def _apply_stealth(context: Any) -> None:
    """Apply stealth patches. Uses playwright-stealth if available."""
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
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        window.chrome = window.chrome || { runtime: {} };
        """
    )


async def _install_resource_blocker(page: Any, block_resources: bool) -> None:
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


async def browse_webpage(
    url: str,
    wait_for_selector: Optional[str] = None,
    actions: Optional[List[Dict[str, Any]]] = None,
    include_links: bool = False,
    screenshot: bool = False,
    max_content_length: int = 15000,
    block_resources: bool = True,
    extract_mode: str = "auto",
    profile_name: Optional[str] = None,
) -> PlaywrightBrowserOutput:
    """Browse a web page with a stealth-patched headless browser and JS execution.

    Unlike `web_scraper`, this tool supports a scripted action list (click, type,
    scroll, wait) so agents can drive interactive forms, search boxes, and SPAs.

    Args:
        url: URL of the web page to browse.
        wait_for_selector: Optional CSS selector to wait for before extracting.
        actions: Action list, each a dict like:
            - {"type": "click", "selector": "CSS"}
            - {"type": "type", "selector": "CSS", "text": "value"}
            - {"type": "scroll", "direction": "down"}
            - {"type": "wait", "ms": 1000}
        include_links: Extract up to 50 links.
        screenshot: Save a PNG screenshot (returned as `screenshot_path`).
        max_content_length: Max characters of content to return.
        block_resources: Block images/CSS/fonts/analytics (default true).
        extract_mode: "auto" | "article" | "full_page" (see scraper_extract.extract_content).
        profile_name: Override the UA profile (see scraper_config.UA_PROFILES).
    """
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return PlaywrightBrowserOutput(
            success=False,
            url=url,
            error_code=ScraperErrorCode.INVALID_URL.value,
            error="Invalid URL. Provide a complete URL starting with http:// or https://",
        )
    if parsed.scheme not in ("http", "https"):
        return PlaywrightBrowserOutput(
            success=False,
            url=url,
            error_code=ScraperErrorCode.INVALID_URL.value,
            error="Only HTTP and HTTPS URLs are supported",
        )

    try:
        from playwright.async_api import async_playwright  # type: ignore
    except ImportError:
        return PlaywrightBrowserOutput(
            success=False,
            url=url,
            error_code=ScraperErrorCode.UNKNOWN.value,
            error="Playwright not installed. Run: pip install playwright && playwright install chromium",
        )

    profile = pick_profile(profile_name)
    proxy_cfg = get_proxy_config()
    rate_limiter = get_rate_limiter()

    async with await rate_limiter.acquire(url):
        try:
            async with async_playwright() as p:
                browser_launcher = getattr(p, profile.playwright_engine, p.chromium)
                launch_kwargs: Dict[str, Any] = {"headless": True}
                if proxy_cfg.enabled:
                    proxy_dict = proxy_cfg.for_playwright()
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

                try:
                    await page.goto(url, wait_until="networkidle", timeout=45000)
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
                    return PlaywrightBrowserOutput(
                        success=False, url=url, error_code=code.value, error=msg
                    )

                await asyncio.sleep(random.uniform(0.5, 1.5))

                if wait_for_selector:
                    try:
                        await page.wait_for_selector(wait_for_selector, timeout=10000)
                    except Exception:
                        logger.warning(
                            "Selector '%s' not found, continuing anyway", wait_for_selector
                        )

                if actions:
                    for action in actions:
                        action_type = action.get("type", "")
                        try:
                            if action_type == "click":
                                selector = action.get("selector", "")
                                if selector:
                                    await page.click(selector, timeout=5000)
                                    await asyncio.sleep(random.uniform(0.3, 0.8))
                            elif action_type == "type":
                                selector = action.get("selector", "")
                                text = action.get("text", "")
                                if selector and text:
                                    await page.fill(selector, text)
                                    await asyncio.sleep(random.uniform(0.2, 0.5))
                            elif action_type == "scroll":
                                await page.evaluate("window.scrollBy(0, window.innerHeight)")
                                await asyncio.sleep(random.uniform(0.5, 1.0))
                            elif action_type == "wait":
                                ms = action.get("ms", 1000)
                                await asyncio.sleep(ms / 1000)
                        except Exception as e:
                            logger.warning("Action %s failed: %s", action_type, e)

                # Settle
                await asyncio.sleep(0.5)

                try:
                    title = await page.title()
                except Exception:
                    title = ""
                try:
                    html = await page.content()
                except Exception:
                    html = ""
                try:
                    body_text = await page.evaluate(
                        """() => {
                            const remove = document.querySelectorAll(
                                'script, style, nav, header, footer, aside, noscript, iframe'
                            );
                            remove.forEach(el => el.remove());
                            return document.body ? document.body.innerText : '';
                        }"""
                    )
                except Exception:
                    body_text = ""

                final_url = str(page.url)

                detected = detect_cloudflare_or_captcha(html, {})
                if detected is not None:
                    await browser.close()
                    return PlaywrightBrowserOutput(
                        success=False,
                        url=final_url,
                        error_code=detected.value,
                        error=f"Anti-bot challenge detected ({detected.value})",
                    )

                content, _extractor = extract_content(html, final_url, mode=extract_mode)
                if len(content) < 120 and body_text:
                    content = body_text

                if len(content) > max_content_length:
                    content = content[:max_content_length] + "\n\n[Content truncated...]"

                links_list: List[ExtractedLink] = []
                if include_links:
                    try:
                        raw_links = await page.evaluate(
                            """() => {
                                const anchors = document.querySelectorAll('a[href]');
                                return Array.from(anchors).slice(0, 100).map(a => ({
                                    text: (a.innerText || a.textContent || '').trim().substring(0, 100),
                                    href: a.href
                                }));
                            }"""
                        )
                        seen: set[str] = set()
                        for link in raw_links:
                            href = link.get("href", "")
                            if not href or href.startswith("javascript:") or href.startswith("#") or href.startswith("mailto:"):
                                continue
                            abs_url = urljoin(final_url, href)
                            if abs_url in seen:
                                continue
                            seen.add(abs_url)
                            text = (
                                link.get("text", "")
                                or urlparse(abs_url).path.split("/")[-1]
                                or abs_url
                            )
                            links_list.append(ExtractedLink(text=text, url=abs_url))
                            if len(links_list) >= 50:
                                break
                    except Exception as e:
                        logger.debug("Link extraction failed: %s", e)

                screenshot_path: Optional[str] = None
                if screenshot:
                    try:
                        import os as _os
                        screenshot_dir = _os.path.expanduser("~/.cursor/browser-logs")
                        _os.makedirs(screenshot_dir, exist_ok=True)
                        screenshot_path = _os.path.join(
                            screenshot_dir,
                            f"playwright-{int(asyncio.get_event_loop().time())}.png",
                        )
                        await page.screenshot(path=screenshot_path)
                    except Exception as e:
                        logger.debug("Screenshot failed: %s", e)
                        screenshot_path = None

                await browser.close()

                return PlaywrightBrowserOutput(
                    success=True,
                    url=final_url,
                    title=title,
                    content=content,
                    links=links_list,
                    screenshot_path=screenshot_path,
                    error_code=ScraperErrorCode.OK.value,
                )

        except Exception as e:
            logger.error("Playwright browsing failed for %s: %s", url, e, exc_info=True)
            return PlaywrightBrowserOutput(
                success=False,
                url=url,
                error_code=ScraperErrorCode.UNKNOWN.value,
                error=f"Browser error: {e}",
            )


playwright_browser_tool = Tool(
    browse_webpage,
    takes_ctx=False,
    name="playwright_browser",
    description="""Browse a web page with a stealth headless browser (JS + interaction).

Use this tool when:
- The page requires JavaScript (SPAs, dynamic content)
- You need to interact with the page (click, type, scroll, wait)
- `web_scraper` returned JS_REQUIRED / BLOCKED_403 and you need explicit control
- You need a screenshot

For plain content retrieval without interaction, prefer `web_scraper` — it
auto-escalates through curl_cffi -> Playwright -> Camoufox with less overhead.

Returns structured `error_code` (OK / BLOCKED_CLOUDFLARE / CAPTCHA_REQUIRED /
TIMEOUT / ...) so you can decide your next step.
""",
)
