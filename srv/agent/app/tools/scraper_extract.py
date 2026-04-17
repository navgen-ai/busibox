"""Content extraction for the stealth scraper.

Tries trafilatura first (preserves tables, lists, headings, links), falls back
to readability-lxml, and finally to a regex-based stripper so we never return
empty content. Also handles title + link extraction.
"""

from __future__ import annotations

import logging
import re
from typing import List, Tuple
from urllib.parse import urljoin, urlparse

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ExtractedLink(BaseModel):
    """A link extracted from a web page."""

    text: str = Field(description="Link text")
    url: str = Field(description="Absolute URL")


# =============================================================================
# Title / links (used by all tiers and content-extraction modes)
# =============================================================================

_TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.IGNORECASE)
_H1_RE = re.compile(r"<h1[^>]*>([^<]+)</h1>", re.IGNORECASE)
_LINK_RE = re.compile(r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>([^<]*)</a>', re.IGNORECASE)


def extract_title(html: str) -> str:
    """Extract the page title from HTML (prefers <title>, falls back to first <h1>)."""
    match = _TITLE_RE.search(html)
    if match:
        return match.group(1).strip()
    match = _H1_RE.search(html)
    if match:
        return match.group(1).strip()
    return ""


def extract_links_from_html(html: str, base_url: str, max_links: int = 50) -> List[ExtractedLink]:
    """Extract up to `max_links` unique links from the HTML content."""
    links: List[ExtractedLink] = []
    seen_urls: set[str] = set()
    for href, text in _LINK_RE.findall(html):
        if href.startswith("#") or href.startswith("javascript:") or href.startswith("mailto:"):
            continue
        absolute_url = urljoin(base_url, href)
        if absolute_url in seen_urls:
            continue
        seen_urls.add(absolute_url)
        clean_text = re.sub(r"\s+", " ", text).strip()
        if not clean_text:
            clean_text = urlparse(absolute_url).path.split("/")[-1] or absolute_url
        links.append(ExtractedLink(text=clean_text[:100], url=absolute_url))
        if len(links) >= max_links:
            break
    return links


# =============================================================================
# Content extraction
# =============================================================================

def _regex_clean(html: str) -> str:
    """Last-resort regex stripper (preserved from the original implementation)."""
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    html = re.sub(r"<nav[^>]*>.*?</nav>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<header[^>]*>.*?</header>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<footer[^>]*>.*?</footer>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<aside[^>]*>.*?</aside>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<(p|div|br|h[1-6]|li|tr)[^>]*>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<[^>]+>", "", html)
    replacements = {
        "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">",
        "&quot;": '"', "&#39;": "'",
    }
    for entity, char in replacements.items():
        html = html.replace(entity, char)
    html = re.sub(r"\n\s*\n", "\n\n", html)
    html = re.sub(r"[ \t]+", " ", html)
    html = "\n".join(line.strip() for line in html.split("\n"))
    return html.strip()


def _trafilatura_extract(html: str, url: str, favor_precision: bool) -> str:
    """Extract clean markdown-ish content using trafilatura."""
    try:
        import trafilatura  # type: ignore
    except ImportError:
        return ""
    try:
        result = trafilatura.extract(
            html,
            url=url,
            include_tables=True,
            include_links=True,
            include_formatting=True,
            favor_precision=favor_precision,
            favor_recall=not favor_precision,
            output_format="markdown",
            with_metadata=False,
        )
        return (result or "").strip()
    except Exception as e:
        logger.debug("trafilatura extraction failed: %s", e)
        return ""


def _readability_extract(html: str, url: str) -> str:
    """Fallback content extractor using readability-lxml + a light HTML-to-text pass."""
    try:
        from readability import Document  # type: ignore
    except ImportError:
        return ""
    try:
        doc = Document(html)
        summary_html = doc.summary(html_partial=True)
        return _regex_clean(summary_html)
    except Exception as e:
        logger.debug("readability extraction failed: %s", e)
        return ""


def extract_content(
    html: str,
    url: str,
    *,
    mode: str = "auto",
) -> Tuple[str, str]:
    """Extract readable content from an HTML document.

    Args:
        html: The raw HTML body.
        url: Absolute URL (used by trafilatura to resolve relative links).
        mode: "auto" (trafilatura -> readability -> regex), "article" (trafilatura
              only, favor precision), or "full_page" (regex only, keeps all text).

    Returns:
        (content, extractor_name)
    """
    if not html:
        return "", "none"

    if mode == "full_page":
        return _regex_clean(html), "regex"

    if mode == "article":
        traf = _trafilatura_extract(html, url, favor_precision=True)
        if traf:
            return traf, "trafilatura"
        readable = _readability_extract(html, url)
        if readable:
            return readable, "readability"
        return _regex_clean(html), "regex"

    # auto mode: prefer precision but fall through aggressively so we never return empty
    traf = _trafilatura_extract(html, url, favor_precision=False)
    if traf and len(traf) >= 200:
        return traf, "trafilatura"

    readable = _readability_extract(html, url)
    if readable and len(readable) >= 200:
        return readable, "readability"

    # final fallback
    return _regex_clean(html), "regex"
