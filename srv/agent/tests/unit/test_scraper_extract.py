"""Unit tests for scraper_extract: content extraction, title, links."""

from __future__ import annotations

import pytest

from app.tools.scraper_extract import (
    ExtractedLink,
    extract_content,
    extract_links_from_html,
    extract_title,
)

pytestmark = pytest.mark.unit


SAMPLE_HTML = """
<!DOCTYPE html>
<html>
<head><title>  USACE Public Notice NAE-2025-001  </title></head>
<body>
    <nav>Skip to content</nav>
    <header>Banner</header>
    <main>
        <h1>Public Notice: Dredging Application</h1>
        <p>Applicant: Cashman Dredging &amp; Marine Contracting</p>
        <p>Project location: Boston Harbor, MA</p>
        <h2>Description</h2>
        <p>The applicant proposes to dredge approximately 50,000 cubic yards.</p>
        <h2>Contacts</h2>
        <ul>
            <li>District Engineer: <a href="/contact">Contact form</a></li>
            <li>External docs: <a href="https://example.com/ext">Externals</a></li>
            <li>Skip: <a href="javascript:void(0)">bad</a></li>
            <li>Mail: <a href="mailto:foo@bar.com">email</a></li>
            <li>Hash: <a href="#top">top</a></li>
        </ul>
    </main>
    <footer>Copyright 2025</footer>
    <script>console.log('tracker');</script>
</body>
</html>
"""


# =============================================================================
# Title extraction
# =============================================================================

class TestExtractTitle:
    def test_title_tag(self) -> None:
        assert extract_title(SAMPLE_HTML) == "USACE Public Notice NAE-2025-001"

    def test_fallback_to_h1(self) -> None:
        html = "<html><body><h1>Just an H1</h1></body></html>"
        assert extract_title(html) == "Just an H1"

    def test_empty_html(self) -> None:
        assert extract_title("") == ""

    def test_no_title_or_h1(self) -> None:
        assert extract_title("<html><body><p>no title</p></body></html>") == ""


# =============================================================================
# Link extraction
# =============================================================================

class TestExtractLinks:
    def test_skips_js_mailto_hash_links(self) -> None:
        links = extract_links_from_html(SAMPLE_HTML, "https://example.mil/notice/123")
        hrefs = [link.url for link in links]
        # javascript:, mailto:, and # should be filtered out
        assert not any("javascript:" in h for h in hrefs)
        assert not any(h.startswith("mailto:") for h in hrefs)
        assert not any(h.endswith("#top") for h in hrefs)

    def test_resolves_relative_urls(self) -> None:
        links = extract_links_from_html(SAMPLE_HTML, "https://example.mil/notice/123")
        hrefs = [link.url for link in links]
        assert any(h.startswith("https://example.mil/contact") for h in hrefs)

    def test_preserves_external_urls(self) -> None:
        links = extract_links_from_html(SAMPLE_HTML, "https://example.mil/notice/123")
        hrefs = [link.url for link in links]
        assert "https://example.com/ext" in hrefs

    def test_deduplicates(self) -> None:
        html = (
            '<a href="https://a.com/1">one</a>'
            '<a href="https://a.com/1">one-dup</a>'
            '<a href="https://a.com/2">two</a>'
        )
        links = extract_links_from_html(html, "https://a.com/")
        urls = [link.url for link in links]
        assert urls.count("https://a.com/1") == 1
        assert len(links) == 2

    def test_max_links_cap(self) -> None:
        many = "".join(f'<a href="/p{i}">p{i}</a>' for i in range(200))
        html = f"<html><body>{many}</body></html>"
        links = extract_links_from_html(html, "https://a.com/")
        assert len(links) == 50


# =============================================================================
# Content extraction
# =============================================================================

class TestContentExtraction:
    def test_regex_fallback_strips_scripts_and_nav(self) -> None:
        content, extractor = extract_content(SAMPLE_HTML, "https://example.com/", mode="full_page")
        assert extractor == "regex"
        assert "tracker" not in content  # script stripped
        assert "Skip to content" not in content  # nav stripped
        assert "Banner" not in content  # header stripped
        assert "Copyright 2025" not in content  # footer stripped
        assert "Dredging Application" in content
        assert "Boston Harbor" in content
        # HTML entities decoded
        assert "Cashman Dredging & Marine Contracting" in content

    def test_auto_mode_returns_content(self) -> None:
        content, extractor = extract_content(SAMPLE_HTML, "https://example.com/", mode="auto")
        # At minimum we expect some recognizable text; trafilatura/readability may or may not
        # be installed at unit-test time.
        assert "Dredging Application" in content or "Public Notice" in content
        assert extractor in ("trafilatura", "readability", "regex")

    def test_article_mode_returns_content(self) -> None:
        content, extractor = extract_content(SAMPLE_HTML, "https://example.com/", mode="article")
        assert len(content) > 0
        assert extractor in ("trafilatura", "readability", "regex")

    def test_empty_html_returns_empty(self) -> None:
        content, extractor = extract_content("", "https://example.com/", mode="auto")
        assert content == ""
        assert extractor == "none"

    def test_preserves_content_meaning(self) -> None:
        """Even the regex fallback should preserve the key procurement data."""
        content, _ = extract_content(SAMPLE_HTML, "https://example.com/", mode="full_page")
        assert "50,000 cubic yards" in content
