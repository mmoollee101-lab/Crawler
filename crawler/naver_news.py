"""Naver News search crawler — paginate through search results."""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import OrderedDict
from typing import Callable, List, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .models import NewsArticle

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://search.naver.com/search.naver"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://search.naver.com/",
}

_PAGE_SIZE = 10
_DATE_RE = re.compile(r"\d+시간 전|\d+분 전|\d+일 전|\d{4}\.\d{2}\.\d{2}")


class NaverNewsCrawler:
    """Crawl Naver News search results with date range and pagination."""

    def __init__(
        self,
        keyword: str,
        start_date: str,
        end_date: str,
        max_results: int = 1000,
        delay: float = 0.5,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        self._keyword = keyword
        self._start_date = start_date  # YYYY.MM.DD
        self._end_date = end_date      # YYYY.MM.DD
        self._max_results = max_results
        self._delay = delay
        self._progress_callback = progress_callback
        self._cancel_event = cancel_event
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)

    def crawl(self) -> List[NewsArticle]:
        articles: List[NewsArticle] = []
        start = 1
        empty_streak = 0

        while len(articles) < self._max_results:
            if self._cancel_event and self._cancel_event.is_set():
                logger.info("Naver News crawl cancelled.")
                break

            params = {
                "where": "news",
                "query": self._keyword,
                "sort": "1",  # 최신순
                "ds": self._start_date,
                "de": self._end_date,
                "start": str(start),
            }

            try:
                resp = self._session.get(_SEARCH_URL, params=params, timeout=15)
                resp.raise_for_status()
                resp.encoding = "utf-8"
            except requests.RequestException as e:
                logger.warning("Naver fetch failed (start=%d): %s", start, e)
                break

            new_articles = self._parse_results(resp.text)

            if not new_articles:
                empty_streak += 1
                if empty_streak >= 2:
                    break
                start += _PAGE_SIZE
                time.sleep(self._delay)
                continue

            empty_streak = 0
            articles.extend(new_articles)

            if self._progress_callback:
                self._progress_callback(
                    len(articles),
                    self._max_results,
                    new_articles[-1].title,
                )

            start += _PAGE_SIZE
            time.sleep(self._delay)

        self._session.close()
        return articles[: self._max_results]

    def _parse_results(self, html: str) -> List[NewsArticle]:
        soup = BeautifulSoup(html, "html.parser")
        container = soup.select_one(".list_news")
        if not container:
            return []

        articles: List[NewsArticle] = []
        seen_urls: set[str] = set()

        for a_tag in container.find_all("a", href=True):
            href = a_tag["href"]
            text = a_tag.get_text(strip=True)
            parsed = urlparse(href)

            # Filter: external article link with meaningful title text
            if (
                not href.startswith("http")
                or "naver.com" in href
                or parsed.path in ("", "/")
                or href in seen_urls
                or not (10 < len(text) < 100)
                or text.startswith("http")
                or "keep" in href.lower()
            ):
                continue

            seen_urls.add(href)
            title = text

            source = ""
            date = ""
            description = ""

            # Walk up ancestors to find press name, date, description
            ancestor = a_tag
            for _ in range(5):
                ancestor = ancestor.parent
                if ancestor is None:
                    break

                if not source:
                    for a2 in ancestor.find_all("a", href=True):
                        h2 = a2["href"]
                        t2 = a2.get_text(strip=True)
                        if (
                            h2 != href
                            and t2
                            and 1 < len(t2) < 20
                            and (
                                "media.naver.com/press" in h2
                                or (
                                    h2.startswith("http")
                                    and "naver.com" not in h2
                                    and urlparse(h2).path in ("", "/")
                                )
                            )
                        ):
                            source = t2
                            break

                if not date:
                    for span in ancestor.find_all("span"):
                        st = span.get_text(strip=True)
                        if len(st) < 20 and _DATE_RE.search(st):
                            date = st
                            break

                if not description:
                    for a2 in ancestor.find_all("a", href=href):
                        t2 = a2.get_text(strip=True)
                        if t2 != title and len(t2) > len(title):
                            description = t2
                            break

                if source and date:
                    break

            articles.append(
                NewsArticle(
                    title=title,
                    link=href,
                    source=source,
                    date=date,
                    description=description,
                )
            )

        return articles
