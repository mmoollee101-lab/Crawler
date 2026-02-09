"""Data models for crawl results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class PageData:
    """Data extracted from a single crawled page."""

    url: str
    status_code: int
    title: str = ""
    meta_description: str = ""
    text_preview: str = ""
    full_text: str = ""
    headlines: List[str] = field(default_factory=list)
    links_found: int = 0
    depth: int = 0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "status_code": self.status_code,
            "title": self.title,
            "meta_description": self.meta_description,
            "text_preview": self.text_preview,
            "links_found": self.links_found,
            "depth": self.depth,
            "error": self.error,
        }


@dataclass
class NewsArticle:
    """A single news article from search results."""

    title: str
    link: str
    source: str = ""
    date: str = ""
    description: str = ""
    body: str = ""


@dataclass
class CrawlProgress:
    """Progress update from the crawl engine."""

    pages_crawled: int
    max_pages: int
    current_url: str
    current_title: str = ""
    current_depth: int = 0
    status_code: int = 0
    event_type: str = "crawled"  # "crawled", "blocked", "failed"


@dataclass
class KeywordResult:
    """Result of keyword analysis."""

    query_keyword: str
    related_keywords: List[dict] = field(default_factory=list)
    total_pages_analyzed: int = 0
    pages_containing_query: int = 0


@dataclass
class CrawlResult:
    """Aggregated result of a crawl session."""

    seed_url: str
    pages: List[PageData] = field(default_factory=list)
    failed_urls: List[dict] = field(default_factory=list)

    @property
    def total_crawled(self) -> int:
        return len(self.pages)

    @property
    def total_failed(self) -> int:
        return len(self.failed_urls)

    def to_dict(self) -> dict:
        return {
            "seed_url": self.seed_url,
            "total_crawled": self.total_crawled,
            "total_failed": self.total_failed,
            "pages": [p.to_dict() for p in self.pages],
            "failed_urls": self.failed_urls,
        }
