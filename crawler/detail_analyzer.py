"""Detail keyword frequency analysis on article bodies."""

from __future__ import annotations

from typing import Dict, List

from .models import NewsArticle

DEFAULT_COMPANIES: List[str] = [
    "삼성", "SK", "LG", "KT", "네이버", "카카오",
    "한화", "롯데", "현대", "두산", "NHN",
    "AWS", "Google", "Microsoft", "Oracle",
]


class DetailAnalyzer:
    """Count keyword occurrences in article bodies."""

    def analyze(
        self, articles: List[NewsArticle], keywords: List[str],
    ) -> dict:
        """Analyze keyword frequency across articles.

        Returns:
            {
                "keywords": [...],
                "articles": [
                    {"title": ..., "link": ..., "counts": {...}, "total": N},
                    ...
                ],
                "totals": {"keyword": total_count, ...},
            }
        """
        result_articles: List[dict] = []
        totals: Dict[str, int] = {kw: 0 for kw in keywords}

        for article in articles:
            text_lower = article.body.lower()
            counts: Dict[str, int] = {}
            row_total = 0

            for kw in keywords:
                count = text_lower.count(kw.lower())
                counts[kw] = count
                totals[kw] += count
                row_total += count

            result_articles.append({
                "title": article.title,
                "link": article.link,
                "counts": counts,
                "total": row_total,
            })

        return {
            "keywords": list(keywords),
            "articles": result_articles,
            "totals": totals,
        }
