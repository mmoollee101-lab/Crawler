"""Google News crawler — HTML scraping primary, RSS feed fallback."""

from __future__ import annotations

import logging
import re
import threading
import time
import warnings
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
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

_KO_RE = re.compile(r"[가-힣]")


def _detect_locale(keyword: str) -> tuple[str, str, str]:
    """Detect locale from keyword language.

    Returns (hl, gl, ceid) tuple.
    Korean characters present → Korean locale, otherwise English/US.
    """
    if _KO_RE.search(keyword):
        return ("ko", "KR", "KR:ko")
    return ("en", "US", "US:en")

_HEADERS_HTML = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.google.com/",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# Cookies to bypass Google consent/CAPTCHA screens
_COOKIES = {
    "CONSENT": "PENDING+987",
    "SOCS": "CAESHAgBEhJnd3NfMjAyNDAxMTAtMF9SQzIaAmVuIAEaBgiA_LyuBg",
}

_HEADERS_RSS = {
    "User-Agent": _HEADERS_HTML["User-Agent"],
    "Accept": "application/xml,text/xml,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
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
        self._hl, self._gl, self._ceid = _detect_locale(keyword)

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
        session.cookies.update(_COOKIES)

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
                "hl": self._hl,
                "gl": self._gl,
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
        """Fetch with retries on 429. Returns None if blocked."""
        _RETRY_DELAYS = [3, 5, 8]
        for attempt in range(len(_RETRY_DELAYS) + 1):
            try:
                resp = session.get(_SEARCH_URL, params=params, timeout=15)

                if resp.status_code == 200:
                    resp.encoding = "utf-8"
                    return resp

                if resp.status_code == 429:
                    if attempt < len(_RETRY_DELAYS):
                        delay = _RETRY_DELAYS[attempt]
                        logger.warning("Google 429, retrying in %ds... (%d/%d)",
                                       delay, attempt + 1, len(_RETRY_DELAYS))
                        time.sleep(delay)
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
        """Fetch from Google News RSS with weekly time-window splitting.

        Google News RSS returns a limited number of results per query.
        By splitting the date range into weekly windows and adding
        ``after:/before:`` operators, we can collect more articles.
        """
        session = requests.Session()
        session.headers.update(_HEADERS_RSS)

        dt_from = _parse_date_str(self._start_date)
        dt_to = _parse_date_str(self._end_date) + timedelta(days=1)

        # Build weekly windows (newest first for user feedback)
        windows: list[tuple[datetime, datetime]] = []
        cursor = dt_to
        while cursor > dt_from:
            win_start = max(cursor - timedelta(days=7), dt_from)
            windows.append((win_start, cursor))
            cursor = win_start

        seen_urls: set[str] = set()
        all_items: list[tuple] = []  # (item, pub_dt)

        for win_idx, (ws, we) in enumerate(windows):
            if self._cancel_event and self._cancel_event.is_set():
                break
            if len(all_items) >= self._max_results:
                break

            # Use after:/before: date operators in the query
            q = (
                f"{self._keyword} "
                f"after:{ws.strftime('%Y-%m-%d')} "
                f"before:{we.strftime('%Y-%m-%d')}"
            )
            params = {"q": q, "hl": self._hl, "gl": self._gl, "ceid": self._ceid}

            try:
                resp = session.get(_RSS_URL, params=params, timeout=15)
                resp.raise_for_status()
                resp.encoding = "utf-8"
            except requests.RequestException as e:
                logger.warning("Google News RSS failed (window %d): %s", win_idx, e)
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            items = soup.find_all("item")

            for item in items:
                pd_tag = item.find("pubdate")
                if not pd_tag:
                    continue
                pub_dt = _parse_pub_date_dt(pd_tag.get_text())
                if not pub_dt or not (dt_from <= pub_dt < dt_to):
                    continue
                # Deduplicate by title text
                title_tag = item.find("title")
                title_text = title_tag.get_text(strip=True) if title_tag else ""
                if title_text in seen_urls:
                    continue
                seen_urls.add(title_text)
                all_items.append((item, pub_dt))

            if self._progress_callback:
                self._progress_callback(
                    len(all_items), self._max_results,
                    f"RSS window {win_idx + 1}/{len(windows)}",
                )

            if win_idx < len(windows) - 1:
                time.sleep(self._delay * 0.5)

        session.close()

        if not all_items:
            return []

        # Sort by date descending (newest first)
        all_items.sort(key=lambda x: x[1], reverse=True)

        # Phase 1: Parse all items quickly (no URL decoding yet)
        articles: List[NewsArticle] = []
        total = min(len(all_items), self._max_results)

        for item, pub_dt in all_items[:total]:
            article = self._parse_rss_item_fast(item, pub_dt)
            if article:
                articles.append(article)

        if not articles:
            return []

        if self._progress_callback:
            self._progress_callback(
                len(articles), len(articles),
                f"Decoding {len(articles)} URLs...",
            )

        # Phase 2: Batch-decode Google URLs in parallel
        self._batch_decode_urls(articles)

        return articles

    def _parse_rss_item_fast(
        self, item, pub_dt: datetime,
    ) -> Optional[NewsArticle]:
        """Parse a single RSS <item> without URL decoding (fast)."""
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

        if not title:
            return None

        return NewsArticle(
            title=title, link=google_link, source=source,
            date=date, description=description,
        )

    def _batch_decode_urls(self, articles: List[NewsArticle]) -> None:
        """Decode Google News URLs to real source URLs in parallel."""
        # Build index of articles needing decode
        to_decode: list[tuple[int, str]] = []
        for i, art in enumerate(articles):
            if art.link and "news.google.com" in art.link:
                to_decode.append((i, art.link))

        if not to_decode:
            return

        workers = min(8, len(to_decode))
        decoded: dict[int, str] = {}

        def _decode(idx: int, url: str) -> tuple[int, str]:
            try:
                result = new_decoderv1(url)
                if result.get("status"):
                    return (idx, result["decoded_url"])
            except Exception:
                pass
            return (idx, url)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_decode, idx, url): idx
                for idx, url in to_decode
            }
            done_count = 0
            for future in as_completed(futures):
                if self._cancel_event and self._cancel_event.is_set():
                    pool.shutdown(wait=False, cancel_futures=True)
                    break
                idx, real_url = future.result()
                decoded[idx] = real_url
                done_count += 1
                if self._progress_callback and done_count % 10 == 0:
                    self._progress_callback(
                        done_count, len(to_decode),
                        f"Decoded {done_count}/{len(to_decode)} URLs",
                    )

        for idx, real_url in decoded.items():
            articles[idx] = NewsArticle(
                title=articles[idx].title,
                link=real_url,
                source=articles[idx].source,
                date=articles[idx].date,
                description=articles[idx].description,
            )
