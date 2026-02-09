"""Crawl configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class CrawlConfig:
    """Configuration for a crawl session."""

    seed_url: str
    max_depth: int = 2
    max_pages: int = 100
    delay: float = 1.0
    timeout: int = 10
    retries: int = 2
    respect_robots: bool = True
    same_domain: bool = True
    url_patterns: List[str] = field(default_factory=list)
    output_format: str = "json"  # "json" or "csv"
    output_dir: str = "output"
    verbose: bool = False
    user_agent: str = "PyCrawler/1.0 (+https://github.com/example/crawler)"
    keyword: str = ""
