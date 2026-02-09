"""Fetch article bodies from news URLs."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, List, Optional

import requests
from bs4 import BeautifulSoup

from .models import NewsArticle

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

_REMOVE_TAGS = {"script", "style", "nav", "header", "footer", "aside"}


class ArticleFetcher:
    """Visit each article URL and extract the body text."""

    def __init__(
        self,
        delay: float = 0.5,
        cancel_event: Optional[threading.Event] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> None:
        self._delay = delay
        self._cancel_event = cancel_event
        self._progress_callback = progress_callback
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)

    def fetch_bodies(self, articles: List[NewsArticle]) -> List[NewsArticle]:
        """Fetch body text for each article. Modifies articles in-place and returns them."""
        total = len(articles)
        for i, article in enumerate(articles):
            if self._cancel_event and self._cancel_event.is_set():
                logger.info("Article fetch cancelled.")
                break

            if article.body:
                # Already fetched
                if self._progress_callback:
                    self._progress_callback(i + 1, total, article.title)
                continue

            try:
                resp = self._session.get(article.link, timeout=15)
                resp.raise_for_status()
                resp.encoding = resp.apparent_encoding or "utf-8"
                article.body = self._extract_body(resp.text)
            except requests.RequestException as e:
                logger.warning("Failed to fetch %s: %s", article.link, e)
                article.body = ""

            if self._progress_callback:
                self._progress_callback(i + 1, total, article.title)

            if i < total - 1:
                time.sleep(self._delay)

        self._session.close()
        return articles

    @staticmethod
    def _extract_body(html: str) -> str:
        """Extract main body text from HTML."""
        soup = BeautifulSoup(html, "html.parser")

        # Remove unwanted tags
        for tag in soup.find_all(_REMOVE_TAGS):
            tag.decompose()

        # Try <article> tag first
        article_tag = soup.find("article")
        if article_tag:
            return article_tag.get_text(separator=" ", strip=True)

        # Fallback: find the <div> with the longest text
        best_div = None
        best_len = 0
        for div in soup.find_all("div"):
            text = div.get_text(separator=" ", strip=True)
            if len(text) > best_len:
                best_len = len(text)
                best_div = div

        if best_div and best_len > 100:
            return best_div.get_text(separator=" ", strip=True)

        # Last resort: full body text
        body_tag = soup.find("body")
        if body_tag:
            return body_tag.get_text(separator=" ", strip=True)

        return ""
