"""jieba-backed tokenizer for FTS5 indexing and querying.

FTS5's default ``unicode61`` tokenizer splits on unicode category boundaries
but treats each CJK character as its own token — it has no idea that
"批量测试" is two words, so "批量测试" typed as a single search term is
parsed as the 4-character phrase and only matches documents that contain
those four characters *in that order with no break between them*. We want
"批量" and "测试" to be searchable as independent tokens.

Strategy: pre-tokenize both the indexed text and the search query with
jieba before they touch FTS5. Every segment is joined with a single space
so FTS5's default tokenizer sees discrete word tokens. We also keep ASCII
words (e.g. ``SQLite``) intact — jieba already handles that for us.

Notes:
- jieba segmentation is deterministic for a given version + dict.
- Mixed zh/en text stays correct: "SQLite FTS5 基础" → "SQLite FTS5 基础".
- Punctuation and whitespace are collapsed to single spaces.
"""

from __future__ import annotations

import re
from typing import Iterable

import jieba
import jieba.analyse


_WS_RUN = re.compile(r"\s+")

# Stopwords used when filtering jieba keywords for edge-building. Kept
# intentionally small — jieba.analyse already applies its own IDF-based
# filtering, so we only need to drop a few very-high-frequency modifier words
# that slip through on short texts. These duplicate the spirit of the
#     the old ``_FALLBACK_STOPWORDS`` set, trimmed to what matters
# for keyword-level granularity (no 1-char filler since we already drop length
# < 2 tokens below).
_EDGE_KEYWORD_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "and", "for", "with", "from", "this", "that", "these", "those",
        "what", "how", "why", "when", "where", "which", "about", "into",
        "use", "using", "used", "any", "some",
        "怎么", "怎样", "如何", "可以", "能否", "是否", "什么",
        "为什么", "哪里", "哪个", "这个", "那个", "这样", "那样", "请问",
        "帮我", "我们", "你们", "他们",
    }
)


def _iter_tokens(text: str) -> Iterable[str]:
    # cut_for_search yields finer-grained segments than the default cut,
    # which improves recall on compound Chinese terms (e.g. "知识图谱" also
    # emits "知识" and "图谱"). For indexing + querying we want that recall.
    for tok in jieba.cut_for_search(text):
        tok = tok.strip()
        if tok:
            yield tok


def tokenize_for_fts(text: str | None) -> str:
    """Segment *text* and return a space-joined string for FTS5.

    Empty / None input yields an empty string. Runs of whitespace collapse
    into a single space. Non-string input is coerced via ``str()``.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    if not text.strip():
        return ""

    segments = list(_iter_tokens(text))
    if not segments:
        return ""
    joined = " ".join(segments)
    return _WS_RUN.sub(" ", joined).strip()


_PURE_DIGIT = re.compile(r"^\d+$")


def extract_keywords_for_edge(text: str | None, top_n: int = 20) -> list[str]:
    """Extract up to ``top_n`` keywords for fine-grained edge building.

    Uses ``jieba.analyse.extract_tags`` (TF-IDF-based on jieba's built-in
    corpus). No custom dictionary — see docs/phase5b/FINE_EDGE_PLAN.md §1
    for the rationale (edge-building is failure-tolerant and feedback
    compensates for mis-segmented tokens, so the dict maintenance cost
    isn't justified).

    Filters applied after jieba:
      - drop tokens shorter than 2 characters (single chars carry no edge
        signal; jieba already avoids most but emits a few);
      - drop pure-digit tokens (dates, versions, counts — noisy edges);
      - drop the small ``_EDGE_KEYWORD_STOPWORDS`` set of modifier words.
    """
    if not text:
        return []
    raw = text if isinstance(text, str) else str(text)
    raw = raw.strip()
    if not raw:
        return []

    # extract_tags returns up to topK keywords ordered by TF-IDF score.
    # Over-fetch so our post-filter still leaves ``top_n`` survivors in the
    # typical case — keyword filtering drops roughly 10-30%.
    overfetch = max(top_n * 2, top_n + 10)
    candidates = jieba.analyse.extract_tags(raw, topK=overfetch)

    kept: list[str] = []
    seen: set[str] = set()
    for tok in candidates:
        tok = tok.strip()
        if len(tok) < 2:
            continue
        if _PURE_DIGIT.match(tok):
            continue
        lowered = tok.lower()
        if lowered in _EDGE_KEYWORD_STOPWORDS:
            continue
        if lowered in seen:
            continue
        seen.add(lowered)
        kept.append(tok)
        if len(kept) >= top_n:
            break
    return kept
