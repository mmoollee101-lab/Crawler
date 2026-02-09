"""HTML parsing — extract links, text, and metadata."""

from __future__ import annotations

import logging
from typing import List, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

# Minimum length for a headline to be considered an article title
_MIN_HEADLINE_LEN = 8


class Parser:
    """Parse HTML and extract structured data."""

    @staticmethod
    def parse(html: str, base_url: str) -> Tuple[str, str, str, str, List[str], List[str]]:
        """Parse HTML and return (title, meta_desc, text_preview, full_text, links, headlines).

        headlines = article/post titles extracted from headings and link texts.
        Links are resolved to absolute URLs.
        """
        soup = BeautifulSoup(html, "html.parser")

        title = ""
        title_tag = soup.find("title")
        if title_tag and title_tag.string:
            title = title_tag.string.strip()

        meta_description = ""
        meta_tag = soup.find("meta", attrs={"name": "description"})
        if meta_tag:
            meta_description = meta_tag.get("content", "").strip()

        full_text = soup.get_text(separator=" ", strip=True)
        text_preview = full_text[:500]

        links = Parser._extract_links(soup, base_url)
        headlines = Parser._extract_headlines(soup)

        return title, meta_description, text_preview, full_text, links, headlines

    @staticmethod
    def _extract_headlines(soup: BeautifulSoup) -> List[str]:
        """Extract article/post titles from headings and prominent link texts."""
        seen: set[str] = set()
        headlines: List[str] = []

        # 1) Headings h1-h3
        for tag in soup.find_all(["h1", "h2", "h3"]):
            text = tag.get_text(strip=True)
            if len(text) >= _MIN_HEADLINE_LEN and text not in seen:
                seen.add(text)
                headlines.append(text)

        # 2) Link texts that look like article titles (long enough, not navigation)
        for a_tag in soup.find_all("a", href=True):
            text = a_tag.get_text(strip=True)
            if len(text) < _MIN_HEADLINE_LEN:
                continue
            if text in seen:
                continue
            # Skip if it's just a URL
            if text.startswith(("http://", "https://", "www.")):
                continue
            seen.add(text)
            headlines.append(text)

        return headlines

    @staticmethod
    def _extract_links(soup: BeautifulSoup, base_url: str) -> List[str]:
        links: List[str] = []
        for tag in soup.find_all("a", href=True):
            href = tag["href"].strip()
            if href.startswith(("javascript:", "mailto:", "tel:", "#")):
                continue
            absolute = urljoin(base_url, href)
            # Strip fragment
            parsed = urlparse(absolute)
            clean = parsed._replace(fragment="").geturl()
            links.append(clean)
        return links
