"""Google News crawler — HTML scraping primary, RSS feed fallback."""

from __future__ import annotations

import logging
import threading
import time
import warnings
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Callable, List, Optional
from urllib.parse import unquote, urlparse

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from googlenewsdecoder import new_decoderv1

from .models import NewsArticle

logger = logging.getLogger(__name__)

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

_SEARCH_URL = "https://www.google.com/search"
_RSS_URL = "https://news.google.com/rss/search"
_PAGE_SIZE = 10

_HEADERS_HTML = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ko-KR;q=0.8,ko;q=0.7",
    "Referer": "https://www.google.com/",
}

_HEADERS_RSS = {
    "User-Agent": _HEADERS_HTML["User-Agent"],
    "Accept": "application/xml,text/xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ko-KR;q=0.8,ko;q=0.7",
}


def _convert_date_tbs(date_str: str) -> str:
    """Convert YYYY.MM.DD to MM/DD/YYYY for Google tbs parameter."""
    parts = date_str.strip().split(".")
    if len(parts) == 3:
        return f"{parts[1]}/{parts[2]}/{parts[0]}"
    return date_str


def _parse_date_str(date_str: str) -> datetime:
    """Parse YYYY.MM.DD string to datetime."""
    return datetime.strptime(date_str.strip(), "%Y.%m.%d")


def _parse_pub_date_dt(pub_date: str) -> Optional[datetime]:
    """Parse RSS pubDate to datetime object."""
    try:
        return datetime.strptime(pub_date.strip(), "%a, %d %b %Y %H:%M:%S %Z")
    except (ValueError, TypeError):
        return None


def _format_pub_date(dt: datetime) -> str:
    """Format datetime to compact display string."""
    return dt.strftime("%Y.%m.%d %H:%M")


def _extract_real_url(href: str) -> str:
    """Extract real URL from Google's /url?q= redirect wrapper."""
    if "/url?q=" in href:
        start = href.index("/url?q=") + len("/url?q=")
        end = href.find("&", start)
        raw = href[start:end] if end != -1 else href[start:]
        return unquote(raw)
    return href


class GoogleNewsCrawler:
    """Crawl Google News — tries HTML scraping first, falls back to RSS.

    HTML scraping (Google Search tbm=nws) has the broadest index, matching
    what users see in the browser.  If Google blocks us (429/503), we
    automatically fall back to the official RSS feed which is never blocked
    but has a narrower article index.
    """

    def __init__(
        self,
        keyword: str,
        start_date: str,
        end_date: str,
        max_results: int = 1000,
        delay: float = 1.0,
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

    def crawl(self) -> List[NewsArticle]:
        """Try HTML scraping, fall back to RSS if blocked."""
        if self._cancel_event and self._cancel_event.is_set():
            return []

        articles = self._crawl_html()
        if articles is not None:
            return articles

        # HTML scraping failed or was blocked — fall back to RSS
        logger.info("Falling back to Google News RSS feed.")
        if self._progress_callback:
            self._progress_callback(0, 0, "(RSS fallback)")
        return self._crawl_rss()

    # ── Strategy 1: HTML scraping (Google Search News tab) ────

    def _crawl_html(self) -> Optional[List[NewsArticle]]:
        """Scrape Google Search News tab. Returns None if blocked."""
        session = requests.Session()
        session.headers.update(_HEADERS_HTML)

        articles: List[NewsArticle] = []
        start = 0
        empty_streak = 0

        date_from = _convert_date_tbs(self._start_date)
        date_to = _convert_date_tbs(self._end_date)
        tbs = f"cdr:1,cd_min:{date_from},cd_max:{date_to}"

        while len(articles) < self._max_results:
            if self._cancel_event and self._cancel_event.is_set():
                break

            params = {
                "q": self._keyword,
                "tbm": "nws",
                "num": str(_PAGE_SIZE),
                "start": str(start),
                "tbs": tbs,
            }

            resp = self._fetch_html(session, params)

            if resp is None:
                session.close()
                # None signals "blocked" — caller should try RSS
                if not articles:
                    return None
                # We have partial results, return them
                return articles[:self._max_results]

            new_articles = self._parse_html(resp.text)

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

        session.close()
        return articles[:self._max_results]

    def _fetch_html(
        self, session: requests.Session, params: dict,
    ) -> Optional[requests.Response]:
        """Fetch with single retry on 429. Returns None if blocked."""
        for attempt in range(2):
            try:
                resp = session.get(_SEARCH_URL, params=params, timeout=15)

                if resp.status_code == 200:
                    resp.encoding = "utf-8"
                    return resp

                if resp.status_code == 429:
                    if attempt == 0:
                        logger.warning("Google 429, retrying in 3s...")
                        time.sleep(3)
                        continue
                    logger.warning("Google 429 persists, switching to RSS.")
                    return None

                if resp.status_code == 503:
                    logger.warning("Google 503 (CAPTCHA), switching to RSS.")
                    return None

                logger.warning("Google HTTP %d, switching to RSS.", resp.status_code)
                return None

            except requests.RequestException as e:
                logger.warning("Google fetch error: %s", e)
                return None

        return None

    def _parse_html(self, html: str) -> List[NewsArticle]:
        """Parse Google Search News HTML."""
        soup = BeautifulSoup(html, "html.parser")
        seen: set[str] = set()

        # Primary selectors
        containers = soup.select("div.SoaBEf")
        if containers:
            return self._parse_html_primary(containers, seen)

        # Fallback: <a> tag iteration
        return self._parse_html_fallback(soup, seen)

    def _parse_html_primary(
        self, containers, seen: set[str],
    ) -> List[NewsArticle]:
        articles: List[NewsArticle] = []

        for container in containers:
            link_tag = container.find("a", href=True)
            if not link_tag:
                continue

            href = _extract_real_url(link_tag["href"])
            if href in seen or "google.com" in href:
                continue
            seen.add(href)

            title_el = (
                container.select_one("div[role='heading']")
                or container.select_one("div.mCBkyc")
            )
            title = title_el.get_text(strip=True) if title_el else ""

            snippet_el = (
                container.select_one("div.UqSP2b")
                or container.select_one(".GI74Re")
            )
            description = snippet_el.get_text(strip=True) if snippet_el else ""

            source_el = container.select_one(".NUnG9d span")
            source = source_el.get_text(strip=True) if source_el else ""

            date_el = (
                container.select_one(".OSrXXb span")
                or container.select_one(".ZE0LJd span")
            )
            date = date_el.get_text(strip=True) if date_el else ""

            if not title:
                continue

            articles.append(NewsArticle(
                title=title, link=href, source=source,
                date=date, description=description,
            ))

        return articles

    def _parse_html_fallback(
        self, soup: BeautifulSoup, seen: set[str],
    ) -> List[NewsArticle]:
        articles: List[NewsArticle] = []

        for a_tag in soup.find_all("a", href=True):
            href = _extract_real_url(a_tag["href"])
            parsed = urlparse(href)
            if not href.startswith("http") or "google.com" in parsed.netloc:
                continue
            if href in seen:
                continue

            text = a_tag.get_text(strip=True)
            if not (10 < len(text) < 200):
                continue
            seen.add(href)

            articles.append(NewsArticle(
                title=text, link=href, source="", date="", description="",
            ))

        return articles

    # ── Strategy 2: RSS feed (fallback) ───────────────────────

    def _crawl_rss(self) -> List[NewsArticle]:
        """Fetch from Google News RSS, filter dates in Python."""
        session = requests.Session()
        session.headers.update(_HEADERS_RSS)

        dt_from = _parse_date_str(self._start_date)
        dt_to = _parse_date_str(self._end_date) + timedelta(days=1)

        params = {"q": self._keyword}

        try:
            resp = session.get(_RSS_URL, params=params, timeout=15)
            resp.raise_for_status()
            resp.encoding = "utf-8"
        except requests.RequestException as e:
            logger.warning("Google News RSS failed: %s", e)
            session.close()
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.find_all("item")
        session.close()

        if not items:
            return []

        # Filter by date range
        filtered = []
        for item in items:
            pd_tag = item.find("pubdate")
            if not pd_tag:
                continue
            pub_dt = _parse_pub_date_dt(pd_tag.get_text())
            if pub_dt and dt_from <= pub_dt < dt_to:
                filtered.append((item, pub_dt))

        if not filtered:
            return []

        articles: List[NewsArticle] = []
        total = min(len(filtered), self._max_results)

        for i, (item, pub_dt) in enumerate(filtered[:total]):
            if self._cancel_event and self._cancel_event.is_set():
                break

            article = self._parse_rss_item(item, pub_dt)
            if article:
                articles.append(article)

            if self._progress_callback:
                self._progress_callback(
                    i + 1, total,
                    article.title if article else "",
                )

            if i < total - 1 and self._delay > 0:
                time.sleep(self._delay * 0.3)

        return articles

    def _parse_rss_item(
        self, item, pub_dt: datetime,
    ) -> Optional[NewsArticle]:
        """Parse a single RSS <item>."""
        title_tag = item.find("title")
        raw_title = title_tag.get_text(strip=True) if title_tag else ""

        title = raw_title
        source = ""
        if " - " in raw_title:
            parts = raw_title.rsplit(" - ", 1)
            title = parts[0].strip()
            source = parts[1].strip()

        source_tag = item.find("source")
        if source_tag:
            sibling = source_tag.next_sibling
            if sibling and str(sibling).strip():
                source = str(sibling).strip()
            elif source_tag.get_text(strip=True):
                source = source_tag.get_text(strip=True)

        date = _format_pub_date(pub_dt)

        desc_tag = item.find("description")
        description = ""
        if desc_tag:
            desc_soup = BeautifulSoup(desc_tag.get_text(), "html.parser")
            description = desc_soup.get_text(strip=True)

        google_link = ""
        link_tag = item.find("link")
        if link_tag and link_tag.next_sibling:
            google_link = str(link_tag.next_sibling).strip()

        real_url = self._decode_article_url(google_link)

        if not title:
            return None

        return NewsArticle(
            title=title, link=real_url, source=source,
            date=date, description=description,
        )

    @staticmethod
    def _decode_article_url(google_url: str) -> str:
        """Decode Google News article URL to the real source URL."""
        if not google_url:
            return ""
        try:
            result = new_decoderv1(google_url)
            if result.get("status"):
                return result["decoded_url"]
        except Exception as e:
            logger.debug("URL decode failed: %s", e)
        return google_url
