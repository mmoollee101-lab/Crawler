"""BFS crawl engine — the core orchestrator."""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Callable, Optional, Tuple

from .config import CrawlConfig
from .fetcher import Fetcher
from .filters import URLFilter
from .models import CrawlProgress, CrawlResult, PageData
from .parser import Parser
from .robots import RobotsChecker
from .storage import Storage

logger = logging.getLogger(__name__)


class CrawlEngine:
    """Breadth-first crawl engine."""

    def __init__(
        self,
        config: CrawlConfig,
        progress_callback: Optional[Callable[[CrawlProgress], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        self._config = config
        self._fetcher = Fetcher(config)
        self._parser = Parser()
        self._filter = URLFilter(
            seed_url=config.seed_url,
            same_domain=config.same_domain,
            patterns=config.url_patterns or None,
        )
        self._robots = RobotsChecker(config.user_agent) if config.respect_robots else None
        self._storage = Storage(config.output_dir)
        self._progress_callback = progress_callback
        self._cancel_event = cancel_event

    def run(self) -> CrawlResult:
        result = CrawlResult(seed_url=self._config.seed_url)

        # Queue holds (url, depth)
        queue: deque[Tuple[str, int]] = deque()
        queue.append((self._config.seed_url, 0))
        self._filter.mark_seen(self._config.seed_url)

        while queue and result.total_crawled < self._config.max_pages:
            if self._cancel_event and self._cancel_event.is_set():
                logger.info("Crawl cancelled by user.")
                break

            url, depth = queue.popleft()

            if depth > self._config.max_depth:
                continue

            if self._robots and not self._robots.is_allowed(url):
                logger.info("Blocked by robots.txt: %s", url)
                if self._progress_callback:
                    self._progress_callback(CrawlProgress(
                        pages_crawled=result.total_crawled,
                        max_pages=self._config.max_pages,
                        current_url=url,
                        current_title="(robots.txt)",
                        current_depth=depth,
                        status_code=0,
                        event_type="blocked",
                    ))
                continue

            html, status_code = self._fetcher.fetch(url)

            if html is None:
                result.failed_urls.append({"url": url, "status_code": status_code})
                logger.warning("Failed: %s (status=%d)", url, status_code)
                if self._progress_callback:
                    self._progress_callback(CrawlProgress(
                        pages_crawled=result.total_crawled,
                        max_pages=self._config.max_pages,
                        current_url=url,
                        current_title="(failed)",
                        current_depth=depth,
                        status_code=status_code,
                        event_type="failed",
                    ))
                continue

            title, meta_desc, text_preview, full_text, links, headlines = self._parser.parse(html, url)

            page = PageData(
                url=url,
                status_code=status_code,
                title=title,
                meta_description=meta_desc,
                text_preview=text_preview,
                full_text=full_text,
                headlines=headlines,
                links_found=len(links),
                depth=depth,
            )
            result.pages.append(page)
            logger.info(
                "[%d/%d] depth=%d %s — %s",
                result.total_crawled,
                self._config.max_pages,
                depth,
                url,
                title or "(no title)",
            )

            if self._progress_callback:
                progress = CrawlProgress(
                    pages_crawled=result.total_crawled,
                    max_pages=self._config.max_pages,
                    current_url=url,
                    current_title=title,
                    current_depth=depth,
                    status_code=status_code,
                )
                self._progress_callback(progress)

            if depth < self._config.max_depth:
                new_links = self._filter.filter(links)
                for link in new_links:
                    queue.append((link, depth + 1))

        self._fetcher.close()
        return self._save(result)

    def _save(self, result: CrawlResult) -> CrawlResult:
        fmt = self._config.output_format.lower()
        if fmt in ("json", "both"):
            self._storage.save_json(result)
        if fmt in ("csv", "both"):
            self._storage.save_csv(result)
        return result
