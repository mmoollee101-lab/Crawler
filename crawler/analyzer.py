"""Keyword extraction and co-occurrence analysis."""

from __future__ import annotations

import logging
import math
import re
from collections import Counter, defaultdict
from typing import List

from .models import KeywordResult, PageData

logger = logging.getLogger(__name__)

# Tokenize Korean, English words, and numbers
_TOKEN_RE = re.compile(r"[가-힣]+|[a-zA-Z]+|[0-9]+")

# Optional morphological analyzer
try:
    from kiwipiepy import Kiwi as _Kiwi
    _kiwi_instance: _Kiwi | None = _Kiwi()
    logger.info("kiwipiepy loaded — using morphological analysis for Korean.")
except ImportError:
    _kiwi_instance = None

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
    # 대명사/관형사/의존명사
    "이", "그", "저", "것", "수", "등", "들", "및", "에", "의", "가", "를",
    "은", "는", "로", "와", "과", "도", "에서", "으로",
    # 동사/형용사 어간
    "하다", "있다", "되다", "없다", "않다", "이다", "하는", "했다", "한다",
    "된다", "되는", "되었다", "하게", "하며", "하면", "하여", "하고",
    # 보조동사/보도용 동사
    "밝혔다", "전했다", "말했다", "보도했다", "발표했다", "알려졌다",
    "나타났다", "지적했다", "강조했다", "설명했다", "주장했다", "제기했다",
    "보였다", "드러났다", "알렸다", "내놓았다", "가졌다", "열렸다",
    # 부사/접속사
    "대한", "또는", "때문", "위해", "통해", "따라", "관련", "대해",
    "이후", "이번", "현재", "최근", "지난", "올해", "내년", "오늘",
    "그러나", "하지만", "그리고", "또한", "다만", "이에", "한편",
    # 조사/어미 잔여
    "에게", "부터", "까지", "마다", "라고", "라며", "이라고", "라는",
    "라면", "으며", "이며", "에도", "에는", "에서는", "으로는",
    # 일반 고빈도 저정보 어휘
    "경우", "정도", "이상", "이하", "가운데", "가능", "중심", "예정",
    "모두", "매우", "가장", "특히", "약", "총", "각", "전체",
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

        # Combine scores: weight TF-IDF (70%) + co-occurrence (30%)
        combined: dict[str, float] = {}
        norm_values: dict[str, tuple[float, float]] = {}
        all_tokens = set(tfidf_scores.keys()) | set(co_occurrence.keys())
        max_tfidf = max(tfidf_scores.values()) if tfidf_scores else 1.0
        max_cooc = max(co_occurrence.values()) if co_occurrence else 1.0

        for token in all_tokens:
            nt = tfidf_scores.get(token, 0.0) / max_tfidf
            nc = co_occurrence.get(token, 0) / max_cooc
            norm_values[token] = (nt, nc)
            combined[token] = 0.7 * nt + 0.3 * nc

        # Sort and take top N
        top_keywords = sorted(combined.items(), key=lambda x: x[1], reverse=True)
        top_keywords = top_keywords[: self._top_n]

        related = []
        top_kw_set = {token for token, _ in top_keywords}
        for token, score in top_keywords:
            nt, nc = norm_values.get(token, (0.0, 0.0))
            related.append({
                "keyword": token,
                "frequency": global_tf.get(token, 0),
                "co_occurrence": co_occurrence.get(token, 0),
                "tfidf_score": round(tfidf_scores.get(token, 0.0), 4),
                "combined_score": round(score, 4),
                "norm_tfidf": round(nt, 6),
                "norm_cooc": round(nc, 6),
            })

        # Append frequent bigrams that aren't already in the top keywords
        if _kiwi_instance is not None:
            bigrams = self._extract_bigrams(documents, min_freq=3)
            for bg in bigrams:
                if bg in top_kw_set or bg == query_lower:
                    continue
                # Count bigram frequency across documents
                bg_freq = sum(
                    1 for doc in documents
                    if bg in " ".join(doc)
                )
                related.append({
                    "keyword": bg,
                    "frequency": bg_freq,
                    "co_occurrence": 0,
                    "tfidf_score": 0.0,
                    "combined_score": 0.0,
                    "norm_tfidf": 0.0,
                    "norm_cooc": 0.0,
                })
                if len(related) >= self._top_n + 10:
                    break

        return KeywordResult(
            query_keyword=query_keyword,
            related_keywords=related,
            total_pages_analyzed=len(pages),
            pages_containing_query=pages_with_query,
        )

    def _tokenize(self, text: str) -> List[str]:
        """Dispatch to kiwi or regex tokenizer."""
        if _kiwi_instance is not None:
            return self._tokenize_kiwi(text)
        return self._tokenize_regex(text)

    def _tokenize_regex(self, text: str) -> List[str]:
        tokens = _TOKEN_RE.findall(text.lower())
        return [
            t for t in tokens
            if len(t) >= self._min_word_length
            and t not in _STOPWORDS_EN
            and t not in _STOPWORDS_KO
        ]

    def _tokenize_kiwi(self, text: str) -> List[str]:
        """Extract nouns, foreign words, and Chinese characters via kiwipiepy."""
        # NNG=일반명사, NNP=고유명사, SL=외래어, SH=한자
        _KEEP_TAGS = {"NNG", "NNP", "SL", "SH"}
        result: List[str] = []
        for token in _kiwi_instance.tokenize(text):
            form = token.form.lower()
            if token.tag in _KEEP_TAGS and len(form) >= self._min_word_length:
                if form not in _STOPWORDS_EN and form not in _STOPWORDS_KO:
                    result.append(form)
        return result

    def _extract_bigrams(
        self, documents: List[List[str]], min_freq: int = 3,
    ) -> List[str]:
        """Extract frequent consecutive noun bigrams across all documents."""
        bigram_counter: Counter = Counter()
        for tokens in documents:
            for i in range(len(tokens) - 1):
                bigram_counter[(tokens[i], tokens[i + 1])] += 1
        return [
            f"{a} {b}"
            for (a, b), freq in bigram_counter.most_common()
            if freq >= min_freq
        ]
