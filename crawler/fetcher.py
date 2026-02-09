"""HTTP fetching with rate limiting and retries."""

from __future__ import annotations

import logging
import time
from typing import Optional, Tuple

import requests

from .config import CrawlConfig

logger = logging.getLogger(__name__)


class Fetcher:
    """Handles HTTP requests with rate limiting and retry logic."""

    def __init__(self, config: CrawlConfig) -> None:
        self._config = config
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": config.user_agent})
        self._last_request_time: float = 0.0

    def fetch(self, url: str) -> Tuple[Optional[str], int]:
        """Fetch a URL and return (html_content, status_code).

        Returns (None, status_code) on failure.
        """
        for attempt in range(1 + self._config.retries):
            try:
                self._rate_limit()
                logger.debug("Fetching %s (attempt %d)", url, attempt + 1)
                resp = self._session.get(url, timeout=self._config.timeout)
                content_type = resp.headers.get("Content-Type", "")
                if "text/html" not in content_type:
                    logger.debug("Skipping non-HTML content: %s", content_type)
                    return None, resp.status_code
                return resp.text, resp.status_code
            except requests.RequestException as exc:
                logger.warning("Request failed for %s: %s", url, exc)
                if attempt < self._config.retries:
                    time.sleep(1)
        return None, 0

    def _rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self._config.delay:
            time.sleep(self._config.delay - elapsed)
        self._last_request_time = time.monotonic()

    def close(self) -> None:
        self._session.close()
