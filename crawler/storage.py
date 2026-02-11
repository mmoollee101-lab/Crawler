"""Save crawl results to JSON and CSV."""

from __future__ import annotations

import csv
import json
import logging
import os
import shutil
from datetime import datetime

from .models import CrawlResult, KeywordResult

logger = logging.getLogger(__name__)

_MIGRATE_FILES = ("history.json", "presets.json")


def _get_user_data_dir() -> str:
    """Return user-local data directory for KeywordCrawler.

    Windows: %LOCALAPPDATA%/KeywordCrawler/
    """
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = os.path.expanduser("~")
    return os.path.join(base, "KeywordCrawler")


class Storage:
    """Persist crawl results to disk."""

    def __init__(self, output_dir: str | None = None) -> None:
        if output_dir is None:
            output_dir = _get_user_data_dir()
        self._output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self._migrate_legacy_data()

    def save_json(self, result: CrawlResult) -> str:
        path = self._make_path("json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info("Saved JSON → %s", path)
        return path

    def save_csv(self, result: CrawlResult) -> str:
        path = self._make_path("csv")
        fieldnames = [
            "url", "status_code", "title", "meta_description",
            "text_preview", "links_found", "depth", "error",
        ]
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for page in result.pages:
                writer.writerow(page.to_dict())
        logger.info("Saved CSV → %s", path)
        return path

    def save_keywords_csv(self, keyword_result: KeywordResult) -> str:
        path = self._make_keyword_path("csv")
        fieldnames = ["rank", "keyword", "frequency", "co_occurrence", "tfidf_score"]
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for i, kw in enumerate(keyword_result.related_keywords, 1):
                writer.writerow({
                    "rank": i,
                    "keyword": kw["keyword"],
                    "frequency": kw["frequency"],
                    "co_occurrence": kw["co_occurrence"],
                    "tfidf_score": round(kw.get("tfidf_score", 0.0), 4),
                })
        logger.info("Saved keywords CSV → %s", path)
        return path

    def save_keywords_json(self, keyword_result: KeywordResult) -> str:
        path = self._make_keyword_path("json")
        data = {
            "query_keyword": keyword_result.query_keyword,
            "total_pages_analyzed": keyword_result.total_pages_analyzed,
            "pages_containing_query": keyword_result.pages_containing_query,
            "related_keywords": keyword_result.related_keywords,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info("Saved keywords JSON → %s", path)
        return path

    def _make_path(self, ext: str) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return os.path.join(self._output_dir, f"crawl_{ts}.{ext}")

    def save_detail_csv(self, detail_result: dict) -> str:
        """Save detail analysis result as CSV."""
        path = self._make_detail_path("csv")
        keywords = detail_result["keywords"]
        fieldnames = ["#", "title", "link"] + keywords + ["total"]
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for i, art in enumerate(detail_result["articles"], 1):
                row = {"#": i, "title": art["title"], "link": art["link"]}
                for kw in keywords:
                    row[kw] = art["counts"].get(kw, 0)
                row["total"] = art["total"]
                writer.writerow(row)
            # Totals row
            totals_row = {"#": "", "title": "TOTAL", "link": ""}
            for kw in keywords:
                totals_row[kw] = detail_result["totals"].get(kw, 0)
            totals_row["total"] = sum(detail_result["totals"].values())
            writer.writerow(totals_row)
        logger.info("Saved detail CSV → %s", path)
        return path

    def save_detail_json(self, detail_result: dict) -> str:
        """Save detail analysis result as JSON."""
        path = self._make_detail_path("json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(detail_result, f, ensure_ascii=False, indent=2)
        logger.info("Saved detail JSON → %s", path)
        return path

    def _make_keyword_path(self, ext: str) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return os.path.join(self._output_dir, f"keywords_{ts}.{ext}")

    def _make_detail_path(self, ext: str) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return os.path.join(self._output_dir, f"detail_{ts}.{ext}")

    # ── History ───────────────────────────────────────────────

    def append_history(self, record: dict) -> str:
        """Append a crawl record to history.json and return its path."""
        path = os.path.join(self._output_dir, "history.json")
        history: list = []
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except (json.JSONDecodeError, ValueError):
                history = []
        history.append(record)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        logger.info("Appended history → %s", path)
        return path

    def load_history(self) -> list:
        """Load crawl history from history.json."""
        path = os.path.join(self._output_dir, "history.json")
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            return []

    # ── Legacy data migration ────────────────────────────────

    def _migrate_legacy_data(self) -> None:
        """Copy history/presets from old ./output/ folder if they exist."""
        legacy_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output")
        if not os.path.isdir(legacy_dir):
            return
        if os.path.normpath(legacy_dir) == os.path.normpath(self._output_dir):
            return
        for fname in _MIGRATE_FILES:
            src = os.path.join(legacy_dir, fname)
            dst = os.path.join(self._output_dir, fname)
            if os.path.exists(src) and not os.path.exists(dst):
                shutil.copy2(src, dst)
                logger.info("Migrated %s → %s", src, dst)

    # ── Preset CRUD ──────────────────────────────────────────

    def _presets_path(self) -> str:
        return os.path.join(self._output_dir, "presets.json")

    def load_presets(self) -> dict[str, list[str]]:
        """Load user-defined presets from presets.json."""
        path = self._presets_path()
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            return {}

    def save_preset(self, name: str, keywords: list[str]) -> None:
        """Save a preset to presets.json."""
        presets = self.load_presets()
        presets[name] = keywords
        with open(self._presets_path(), "w", encoding="utf-8") as f:
            json.dump(presets, f, ensure_ascii=False, indent=2)
        logger.info("Saved preset '%s' (%d keywords)", name, len(keywords))

    def delete_preset(self, name: str) -> bool:
        """Delete a preset from presets.json. Returns True if deleted."""
        presets = self.load_presets()
        if name not in presets:
            return False
        del presets[name]
        with open(self._presets_path(), "w", encoding="utf-8") as f:
            json.dump(presets, f, ensure_ascii=False, indent=2)
        logger.info("Deleted preset '%s'", name)
        return True
