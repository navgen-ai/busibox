"""Playwright browser tool for fetching and extracting content from JS-rendered web pages."""
import asyncio
import logging
import random
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

from pydantic import BaseModel, Field
from pydantic_ai import Tool

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]


class ExtractedLink(BaseModel):
    text: str = Field(description="Link text")
    url: str = Field(description="Absolute URL")


class PlaywrightBrowserOutput(BaseModel):
    success: bool = Field(description="Whether the page was successfully loaded")
    url: str = Field(description="The URL that was browsed")
    title: str = Field(default="", description="Page title")
    content: str = Field(default="", description="Extracted text content from the page")
    links: List[ExtractedLink] = Field(default_factory=list, description="Extracted links (if requested)")
    screenshot_path: Optional[str] = Field(default=None, description="Path to screenshot file (if taken)")
    error: Optional[str] = Field(default=None, description="Error message if browsing failed")


async def browse_webpage(
    url: str,
    wait_for_selector: Optional[str] = None,
    actions: Optional[List[Dict[str, Any]]] = None,
    extract_links: bool = False,
    screenshot: bool = False,
    max_content_length: int = 15000,
) -> PlaywrightBrowserOutput:
    """
    Browse a web page using a headless browser with full JavaScript support.

    Unlike the web_scraper tool, this can handle JS-rendered pages, SPAs,
    and sites that require interaction (clicking, typing, scrolling).

    Args:
        url: URL of the web page to browse
        wait_for_selector: CSS selector to wait for before extracting content
        actions: List of actions to perform before extraction. Each action is a dict:
            - {"type": "click", "selector": "CSS selector"}
            - {"type": "type", "selector": "CSS selector", "text": "text to type"}
            - {"type": "scroll", "direction": "down"} (default down)
            - {"type": "wait", "ms": 1000}
        extract_links: Whether to extract links from the page
        screenshot: Whether to take a screenshot
        max_content_length: Maximum characters of content to return (default: 15000)

    Returns:
        PlaywrightBrowserOutput with page content, links, and optional screenshot path
    """
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return PlaywrightBrowserOutput(
            success=False,
            url=url,
            error="Invalid URL. Provide a complete URL starting with http:// or https://",
        )

    if parsed.scheme not in ("http", "https"):
        return PlaywrightBrowserOutput(
            success=False,
            url=url,
            error="Only HTTP and HTTPS URLs are supported",
        )

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return PlaywrightBrowserOutput(
            success=False,
            url=url,
            error="Playwright is not installed. Install with: pip install playwright && playwright install chromium",
        )

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={"width": 1280, "height": 800},
                java_script_enabled=True,
            )

            page = await context.new_page()

            # Anti-detection: override navigator.webdriver
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            """)

            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Random delay to appear human-like
            await asyncio.sleep(random.uniform(0.5, 1.5))

            if wait_for_selector:
                try:
                    await page.wait_for_selector(wait_for_selector, timeout=10000)
                except Exception:
                    logger.warning(f"Selector '{wait_for_selector}' not found, continuing anyway")

            if actions:
                for action in actions:
                    action_type = action.get("type", "")
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

            # Wait for any dynamic content to settle
            await asyncio.sleep(0.5)

            title = await page.title()

            # Extract text content from the page body
            text_content = await page.evaluate("""() => {
                const remove = document.querySelectorAll('script, style, nav, header, footer, aside, noscript, iframe');
                remove.forEach(el => el.remove());
                return document.body ? document.body.innerText : '';
            }""")

            # Clean up whitespace
            text_content = re.sub(r'\n\s*\n', '\n\n', text_content)
            text_content = re.sub(r'[ \t]+', ' ', text_content)
            text_content = '\n'.join(line.strip() for line in text_content.split('\n'))
            text_content = text_content.strip()

            if len(text_content) > max_content_length:
                text_content = text_content[:max_content_length] + "\n\n[Content truncated...]"

            links_list = []
            if extract_links:
                raw_links = await page.evaluate("""() => {
                    const anchors = document.querySelectorAll('a[href]');
                    return Array.from(anchors).slice(0, 50).map(a => ({
                        text: (a.innerText || a.textContent || '').trim().substring(0, 100),
                        href: a.href
                    }));
                }""")
                seen = set()
                for link in raw_links:
                    href = link.get("href", "")
                    if not href or href.startswith("javascript:") or href.startswith("#") or href.startswith("mailto:"):
                        continue
                    abs_url = urljoin(url, href)
                    if abs_url in seen:
                        continue
                    seen.add(abs_url)
                    text = link.get("text", "") or urlparse(abs_url).path.split("/")[-1] or abs_url
                    links_list.append(ExtractedLink(text=text, url=abs_url))

            screenshot_path = None
            if screenshot:
                import tempfile, os
                screenshot_dir = os.path.expanduser("~/.cursor/browser-logs")
                os.makedirs(screenshot_dir, exist_ok=True)
                screenshot_path = os.path.join(screenshot_dir, f"playwright-{int(asyncio.get_event_loop().time())}.png")
                await page.screenshot(path=screenshot_path)

            await browser.close()

            return PlaywrightBrowserOutput(
                success=True,
                url=str(page.url),
                title=title,
                content=text_content,
                links=links_list,
                screenshot_path=screenshot_path,
            )

    except Exception as e:
        logger.error(f"Playwright browsing failed for {url}: {e}", exc_info=True)
        return PlaywrightBrowserOutput(
            success=False,
            url=url,
            error=f"Browser error: {str(e)}",
        )


playwright_browser_tool = Tool(
    browse_webpage,
    takes_ctx=False,
    name="playwright_browser",
    description="""Browse a web page using a full headless browser with JavaScript support.
Use this tool when:
- The page requires JavaScript to render content (SPAs, dynamic sites)
- You need to interact with the page (click buttons, fill forms, scroll)
- The web_scraper tool failed because content is JS-rendered
- You need to handle CAPTCHA-protected or login-gated pages

The tool:
- Launches a headless Chromium browser
- Renders the full page including JavaScript
- Supports click, type, scroll, and wait actions
- Extracts clean text content after rendering
- Can optionally extract links and take screenshots

Use web_scraper for simple static pages; use this for dynamic/JS-heavy sites.""",
)
