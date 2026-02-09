"""Keyword extraction and co-occurrence analysis."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import List

from .models import KeywordResult, PageData

# Tokenize Korean, English words, and numbers
_TOKEN_RE = re.compile(r"[가-힣]+|[a-zA-Z]+|[0-9]+")

_STOPWORDS_EN = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "need", "must",
    "not", "no", "nor", "so", "if", "then", "than", "too", "very",
    "just", "about", "above", "after", "again", "all", "also", "am",
    "any", "as", "because", "before", "between", "both", "each", "few",
    "get", "got", "he", "her", "here", "him", "his", "how", "i", "into",
    "it", "its", "let", "me", "more", "most", "my", "new", "now", "old",
    "only", "other", "our", "out", "own", "part", "per", "put", "re",
    "s", "same", "she", "some", "still", "such", "t", "take", "that",
    "their", "them", "there", "these", "they", "this", "those", "through",
    "under", "up", "us", "use", "want", "we", "what", "when", "where",
    "which", "while", "who", "whom", "why", "you", "your",
})

_STOPWORDS_KO = frozenset({
    "이", "그", "저", "것", "수", "등", "들", "및", "에", "의", "가", "를",
    "은", "는", "로", "와", "과", "도", "를", "에서", "으로", "하다", "있다",
    "되다", "없다", "않다", "이다", "대한", "또는", "때문", "하는", "위해",
})


class KeywordAnalyzer:
    """Analyze keywords from crawled pages."""

    def __init__(self, top_n: int = 30, min_word_length: int = 2) -> None:
        self._top_n = top_n
        self._min_word_length = min_word_length

    def analyze(
        self,
        pages: List[PageData],
        query_keyword: str,
        headlines_only: bool = False,
    ) -> KeywordResult:
        query_lower = query_keyword.lower()
        documents: List[List[str]] = []
        pages_with_query = 0

        for page in pages:
            if headlines_only:
                text = " ".join(page.headlines)
            else:
                text = page.full_text
            tokens = self._tokenize(text)
            documents.append(tokens)
            if query_lower in tokens:
                pages_with_query += 1

        if not documents:
            return KeywordResult(
                query_keyword=query_keyword,
                total_pages_analyzed=len(pages),
                pages_containing_query=0,
            )

        # Document frequency
        df: Counter = Counter()
        for doc_tokens in documents:
            unique = set(doc_tokens)
            for token in unique:
                df[token] += 1

        # Global term frequency
        global_tf: Counter = Counter()
        for doc_tokens in documents:
            global_tf.update(doc_tokens)

        # Co-occurrence with query keyword
        co_occurrence: Counter = Counter()
        for doc_tokens in documents:
            if query_lower not in doc_tokens:
                continue
            unique = set(doc_tokens)
            unique.discard(query_lower)
            for token in unique:
                co_occurrence[token] += 1

        # TF-IDF scores
        n_docs = len(documents)
        tfidf_scores: dict[str, float] = {}
        for token, tf_val in global_tf.items():
            if token == query_lower:
                continue
            idf = math.log((n_docs + 1) / (df.get(token, 0) + 1)) + 1
            tfidf_scores[token] = tf_val * idf

        # Combine scores: weight TF-IDF + co-occurrence
        combined: dict[str, float] = {}
        all_tokens = set(tfidf_scores.keys()) | set(co_occurrence.keys())
        max_tfidf = max(tfidf_scores.values()) if tfidf_scores else 1.0
        max_cooc = max(co_occurrence.values()) if co_occurrence else 1.0

        for token in all_tokens:
            norm_tfidf = tfidf_scores.get(token, 0.0) / max_tfidf
            norm_cooc = co_occurrence.get(token, 0) / max_cooc
            combined[token] = 0.5 * norm_tfidf + 0.5 * norm_cooc

        # Sort and take top N
        top_keywords = sorted(combined.items(), key=lambda x: x[1], reverse=True)
        top_keywords = top_keywords[: self._top_n]

        related = []
        for token, score in top_keywords:
            related.append({
                "keyword": token,
                "frequency": global_tf.get(token, 0),
                "co_occurrence": co_occurrence.get(token, 0),
                "tfidf_score": round(tfidf_scores.get(token, 0.0), 4),
                "combined_score": round(score, 4),
            })

        return KeywordResult(
            query_keyword=query_keyword,
            related_keywords=related,
            total_pages_analyzed=len(pages),
            pages_containing_query=pages_with_query,
        )

    def _tokenize(self, text: str) -> List[str]:
        tokens = _TOKEN_RE.findall(text.lower())
        return [
            t for t in tokens
            if len(t) >= self._min_word_length
            and t not in _STOPWORDS_EN
            and t not in _STOPWORDS_KO
        ]
