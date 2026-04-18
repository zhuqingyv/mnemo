"""Write-gate service — L0~L3 pre-write checks.

TECH_PLAN §2.2–§2.5: before committing a new Knowledge row, probe the store
for exact duplicates (L0), title near-matches (L1), semantic near-matches
(L2), and rule-based evidence weakness (L3). Returns a structured report so
the caller (knowledge_service) can decide between ``create`` / ``supersede``
/ ``review`` without blocking the write path.

Gated behind ``config.write_gate_enabled`` — when the flag is off, this
service returns ``None`` and the caller keeps legacy behavior.

Levenshtein is hand-rolled to avoid pulling in ``python-Levenshtein`` /
``rapidfuzz`` — at candidate pool ≤ 50 titles of typical length, the pure-
Python O(n*m) DP completes in well under the 50ms budget.
"""

from __future__ import annotations

import logging
from typing import Any

import jieba
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mnemo.config import MnemoConfig
from mnemo.models.knowledge import Knowledge
from mnemo.repository import (
    knowledge_repository as kr,
    vector_repository as vr,
)
from mnemo.services.embedding_service import EmbeddingService


_NEGATION_TOKENS: tuple[str, ...] = ("不", "禁止", "避免", "反对")
_ASSERTION_TOKENS: tuple[str, ...] = ("必须", "总是", "一定")
_ASSERT_OPPOSING: tuple[str, ...] = ("不", "避免")


logger = logging.getLogger(__name__)


def _levenshtein(a: str, a_len: int, b: str, b_len: int) -> int:
    """Classic two-row DP; returns edit distance.

    a / b are expected to be already-lowercased strings. Lengths are passed in
    so the caller can avoid recomputing ``len()`` when scoring many pairs
    against a fixed incoming title.
    """
    if a_len == 0:
        return b_len
    if b_len == 0:
        return a_len
    prev = list(range(b_len + 1))
    curr = [0] * (b_len + 1)
    for i in range(1, a_len + 1):
        curr[0] = i
        ca = a[i - 1]
        for j in range(1, b_len + 1):
            cost = 0 if ca == b[j - 1] else 1
            curr[j] = min(
                prev[j] + 1,        # deletion
                curr[j - 1] + 1,    # insertion
                prev[j - 1] + cost, # substitution
            )
        prev, curr = curr, prev
    return prev[b_len]


def _title_similarity(a: str, b: str) -> float:
    """Normalized Levenshtein similarity in [0, 1], case-insensitive.

    Returns ``1 - distance / max(len)``. Empty-on-empty → 1.0; empty-vs-non-
    empty → 0.0 — matches intuitive duplicate semantics.
    """
    a = (a or "").strip().lower()
    b = (b or "").strip().lower()
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    la, lb = len(a), len(b)
    dist = _levenshtein(a, la, b, lb)
    longest = la if la >= lb else lb
    return 1.0 - dist / longest


def _jieba_tokens(text: str) -> set[str]:
    """Tokenize via jieba precise mode, drop whitespace/empty tokens."""
    if not text:
        return set()
    return {t for t in jieba.cut(text.strip().lower()) if t and not t.isspace()}


def _title_jaccard(a: str, b: str) -> float:
    """Jaccard similarity over jieba token sets. Catches word-reorder cases
    Levenshtein misses (e.g. ``"Python 异步编程入门"`` vs ``"入门 Python 异步编程"``).
    """
    ta = _jieba_tokens(a)
    tb = _jieba_tokens(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def _has_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(tok in text for tok in tokens)


def _l4_polarity_flip(
    new_content: str, candidate_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """L4 negation polarity flip heuristic (tech_research.md §5.3).

    For each candidate, concatenate its ``title`` + ``content`` into one text
    blob and compare negation / assertion markers against ``new_content``:

      - One side has a negation token (``不`` / ``禁止`` / ``避免`` / ``反对``)
        while the other side has none.
      - Or one side uses a strong assertion (``必须`` / ``总是`` / ``一定``)
        while the other side carries a weaker negation (``不`` / ``避免``).

    Precision is intentionally low — this is a hint surfaced to the caller,
    not a verdict. Matching is plain substring containment; no tokenization
    is needed because the Chinese negation markers are single characters
    or dedicated words that don't collide with unrelated stems.
    """
    new_text = new_content or ""
    new_has_neg = _has_any(new_text, _NEGATION_TOKENS)
    new_has_assert = _has_any(new_text, _ASSERTION_TOKENS)

    flips: list[dict[str, Any]] = []
    for cand in candidate_rows:
        cand_text = f"{cand.get('title') or ''} {cand.get('content') or ''}"
        cand_has_neg = _has_any(cand_text, _NEGATION_TOKENS)
        cand_has_assert = _has_any(cand_text, _ASSERTION_TOKENS)
        cand_has_opposing = _has_any(cand_text, _ASSERT_OPPOSING)
        new_has_opposing = _has_any(new_text, _ASSERT_OPPOSING)

        mismatch = False
        if new_has_neg != cand_has_neg:
            mismatch = True
        elif new_has_assert and cand_has_opposing and not new_has_opposing:
            mismatch = True
        elif cand_has_assert and new_has_opposing and not cand_has_opposing:
            mismatch = True

        if mismatch:
            flips.append(
                {
                    "id": cand["id"],
                    "title": cand.get("title") or "",
                    "reason": "negation_mismatch",
                }
            )
    return flips


def _check_evidence_weak(new_row: Knowledge, min_chars: int) -> dict | None:
    """L3 pure-rule evidence check. First failing rule wins.

    Order is intentional: too-short content is the most concrete signal and
    shadows the weaker summary/source heuristics.
    """
    content = new_row.content or ""
    if len(content) < min_chars:
        return {"reason": "content_too_short"}
    if new_row.claim_type == "fact" and not (new_row.source and new_row.source.strip()):
        return {"reason": "no_source_for_fact"}
    if not (new_row.summary and new_row.summary.strip()):
        return {"reason": "empty_summary"}
    return None


async def run_write_gate(
    session: AsyncSession,
    new_row: Knowledge,
    embedding_service: EmbeddingService | None,
    config: MnemoConfig,
) -> dict[str, Any] | None:
    """Run L0~L4 checks against *new_row*.

    Returns ``None`` when the feature flag is off. Otherwise returns a dict
    with the shape documented in TECH_PLAN §2.5. The function does not mutate
    the session — it only reads — so the caller owns commit semantics.

    ``recommended_action`` decision tree:
      L0 hit → ``supersede`` (same content already exists — bump version);
      L4 hit → ``review`` (polarity flip — surface contradiction to caller);
      L2 strong hit → ``review`` (near-duplicate, surface to caller);
      otherwise → ``create``.
    L1 / L3 populate their fields for the caller's UX but don't short-circuit
    the decision — they're advisory. L4 is gated behind
    ``contradiction_pair_enabled`` because the polarity heuristic runs at
    30-50% precision (tech_research.md §5.3).
    """
    if not config.write_gate_enabled:
        return None

    # --- L0: exact_duplicate -------------------------------------------------
    exact_dup: dict[str, Any] | None = None
    if new_row.content_hash:
        dup = await kr.find_duplicate_by_hash(
            session, new_row.content_hash, exclude_id=new_row.id
        )
        if dup is not None:
            exact_dup = {"id": dup.id, "title": dup.title}

    # --- L1: title_similar ---------------------------------------------------
    title_similar: list[dict[str, Any]] = []
    incoming_title = new_row.title or ""
    if incoming_title:
        candidates = await kr.list_titles_by_scope(
            session,
            scope=new_row.scope,
            project_name=new_row.project_name,
            limit=config.write_gate_prefilter_topk,
        )
        lev_threshold = config.write_gate_title_similarity_threshold
        jac_threshold = config.write_gate_title_jaccard_threshold
        scored: list[dict[str, Any]] = []
        for cand in candidates:
            if cand["id"] == new_row.id:
                continue
            lev = _title_similarity(incoming_title, cand["title"])
            jac = _title_jaccard(incoming_title, cand["title"])
            if lev >= lev_threshold or jac >= jac_threshold:
                # Report the stronger of the two so downstream ranking reflects
                # whichever channel triggered the hit.
                score = lev if lev >= jac else jac
                scored.append(
                    {"id": cand["id"], "title": cand["title"], "score": score}
                )
        scored.sort(key=lambda r: r["score"], reverse=True)
        title_similar = scored[:3]

    # --- L2: semantic_similar ------------------------------------------------
    semantic_similar: list[dict[str, Any]] = []
    if embedding_service is not None:
        prepared = embedding_service.prepare_text(
            new_row.title, new_row.summary, new_row.content
        )
        try:
            query_vec = await embedding_service.embed(prepared)
        except Exception as e:  # noqa: BLE001 — write-gate must never raise
            logger.warning("write_gate L2 embed failed: %s", e)
            query_vec = None

        if query_vec is not None:
            hits = await vr.topk_cosine_by_scope(
                session,
                query_vec,
                scope=new_row.scope,
                project_name=new_row.project_name,
                k=config.write_gate_prefilter_topk,
            )
            sem_threshold = config.write_gate_semantic_similarity_threshold
            filtered = [
                {"id": h["id"], "title": h["title"], "cosine": h["cosine"]}
                for h in hits
                if h["id"] != new_row.id and h["cosine"] >= sem_threshold
            ]
            # topk_cosine_by_scope already sorts by cosine desc; take top-3.
            semantic_similar = filtered[:3]

    # --- L3: evidence_weak ---------------------------------------------------
    evidence_weak = _check_evidence_weak(
        new_row, config.write_gate_min_content_chars
    )

    # --- L4: potential_contradiction -----------------------------------------
    # Gated behind a separate flag because L4 is heuristic (precision 30-50%,
    # tech_research.md §5.3); projects can opt in when they want the hint.
    potential_contradiction: list[dict[str, Any]] = []
    if config.contradiction_pair_enabled and semantic_similar:
        cand_ids = [s["id"] for s in semantic_similar]
        rows = (
            await session.execute(
                select(Knowledge.id, Knowledge.title, Knowledge.content).where(
                    Knowledge.id.in_(cand_ids)
                )
            )
        ).all()
        cand_rows = [{"id": r[0], "title": r[1], "content": r[2]} for r in rows]
        # Preserve top-3 order from semantic_similar.
        by_id = {r["id"]: r for r in cand_rows}
        ordered = [by_id[i] for i in cand_ids if i in by_id]
        new_blob = f"{new_row.title or ''} {new_row.content or ''}"
        potential_contradiction = _l4_polarity_flip(new_blob, ordered)

    # --- decision ------------------------------------------------------------
    if exact_dup is not None:
        recommended_action = "supersede"
    elif potential_contradiction:
        recommended_action = "review"
    elif semantic_similar:
        # semantic_similar is already filtered to >= threshold (0.92 default)
        recommended_action = "review"
    else:
        recommended_action = "create"

    return {
        "exact_duplicate": exact_dup,
        "title_similar": title_similar,
        "semantic_similar": semantic_similar,
        "evidence_weak": evidence_weak,
        "potential_contradiction": potential_contradiction,
        "recommended_action": recommended_action,
    }
