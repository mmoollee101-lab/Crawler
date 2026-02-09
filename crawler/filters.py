"""URL filtering — domain restriction, pattern matching, dedup."""

from __future__ import annotations

import logging
import re
from typing import List, Optional, Set
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class URLFilter:
    """Filter URLs by domain, patterns, and deduplication."""

    def __init__(
        self,
        seed_url: str,
        same_domain: bool = True,
        patterns: Optional[List[str]] = None,
    ) -> None:
        self._seed_domain = urlparse(seed_url).netloc
        self._same_domain = same_domain
        self._patterns = [re.compile(p) for p in (patterns or [])]
        self._seen: Set[str] = set()

    def filter(self, urls: List[str]) -> List[str]:
        """Return only URLs that pass all filters and haven't been seen."""
        result: List[str] = []
        for url in urls:
            if url in self._seen:
                continue
            if not self._is_valid_scheme(url):
                continue
            if self._same_domain and not self._is_same_domain(url):
                continue
            if self._patterns and not self._matches_pattern(url):
                continue
            self._seen.add(url)
            result.append(url)
        return result

    def mark_seen(self, url: str) -> None:
        self._seen.add(url)

    def is_seen(self, url: str) -> bool:
        return url in self._seen

    @staticmethod
    def _is_valid_scheme(url: str) -> bool:
        return urlparse(url).scheme in ("http", "https")

    def _is_same_domain(self, url: str) -> bool:
        return urlparse(url).netloc == self._seed_domain

    def _matches_pattern(self, url: str) -> bool:
        return any(p.search(url) for p in self._patterns)
