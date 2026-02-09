"""Command-line interface for the crawler."""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .config import CrawlConfig
from .engine import CrawlEngine


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="crawler",
        description="Python general-purpose web crawler",
    )
    p.add_argument("url", help="Seed URL to start crawling")
    p.add_argument(
        "-d", "--max-depth", type=int, default=2,
        help="Maximum link depth (default: 2)",
    )
    p.add_argument(
        "-n", "--max-pages", type=int, default=100,
        help="Maximum pages to crawl (default: 100)",
    )
    p.add_argument(
        "--delay", type=float, default=1.0,
        help="Delay between requests in seconds (default: 1.0)",
    )
    p.add_argument(
        "--timeout", type=int, default=10,
        help="HTTP request timeout in seconds (default: 10)",
    )
    p.add_argument(
        "--retries", type=int, default=2,
        help="Max retries per request (default: 2)",
    )
    p.add_argument(
        "--no-robots", action="store_true",
        help="Ignore robots.txt",
    )
    p.add_argument(
        "--allow-external", action="store_true",
        help="Follow links to other domains",
    )
    p.add_argument(
        "--url-pattern", action="append", default=[],
        help="Regex pattern(s) URLs must match (can be repeated)",
    )
    p.add_argument(
        "-f", "--format", choices=["json", "csv", "both"], default="json",
        help="Output format (default: json)",
    )
    p.add_argument(
        "-o", "--output-dir", default="output",
        help="Output directory (default: output)",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose logging",
    )
    p.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}",
    )
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    config = CrawlConfig(
        seed_url=args.url,
        max_depth=args.max_depth,
        max_pages=args.max_pages,
        delay=args.delay,
        timeout=args.timeout,
        retries=args.retries,
        respect_robots=not args.no_robots,
        same_domain=not args.allow_external,
        url_patterns=args.url_pattern,
        output_format=args.format,
        output_dir=args.output_dir,
        verbose=args.verbose,
    )

    engine = CrawlEngine(config)
    result = engine.run()

    print(f"\nCrawl complete: {result.total_crawled} pages, {result.total_failed} failed")


if __name__ == "__main__":
    main()
