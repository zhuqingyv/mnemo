"""Business logic layer — orchestrates repository calls and session lifecycle.

Responsibilities:
- Manage AsyncSession lifetime (one session per service call).
- Translate between id / title inputs and Knowledge rows.
- Parse [[wikilinks]] from content and auto-create Relation rows of type
  ``wikilink`` pointing at any existing Knowledge whose title matches the
  wikilink target. Links whose target does not exist yet are silently
  skipped — they'll resolve on the next update.
- Serialize Knowledge rows into plain dicts so callers (CLI / MCP) never have
  to touch SQLAlchemy objects.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mnemo.config import MnemoConfig
from mnemo.db import get_session_factory
from mnemo.markdown.parser import extract_wikilinks
from mnemo.models.knowledge import Knowledge, KnowledgeEvent, Relation
from mnemo.ranking.freshness import batch_freshness_lookup
from mnemo.ranking.rerank import apply_rerank
from mnemo.repository import (
    authority_repository as ar,
    knowledge_repository as kr,
    relation_repository as rr,
    search_repository as sr,
    vector_repository as vr,
)
from mnemo.repository.feedback_repository import (
    batch_feedback_counts,
    compute_verification_mult,
)
from mnemo.repository.rrf_repository import rrf_fuse
from mnemo.services.embedding_service import EmbeddingService
from mnemo.services import feedback_service as _feedback_service
from mnemo.services.archive_service import (
    archive_knowledge as archive_kr,
    unarchive_knowledge as unarchive_kr,
)
from mnemo.services.lifecycle_service import (
    STATUS_ACTIVE as LIFECYCLE_STATUS_ACTIVE,
    check_stale_transition,
    touch_last_accessed,
)
from mnemo.services.write_gate_service import run_write_gate
from mnemo.health import detectors as health_detectors
from mnemo.health import quality_detectors as health_quality
from mnemo.health.task_store import add_task as _add_health_task  # noqa: F401


logger = logging.getLogger(__name__)


WIKILINK_RELATION_TYPE = "wikilink"
MANUAL_RELATION_TYPE = "related"
AUTO_LINK_RELATION_TYPE = "related"
# Phase 5b fine-grained keyword auto-edge relation type — distinct from
# ``related`` so downstream rerank/feedback can apply the dynamic-weight
# evolution (0.3 → 0.85 with feedback) without touching agent-declared
# strong ties. docs/phase5b/FINE_EDGE_PLAN.md §2.1.
AUTO_RELATED_RELATION_TYPE = "auto_related"
AUTO_RELATED_INITIAL_WEIGHT = 0.3


# Stopwords/common modifiers stripped when shortening a zero-hit query for
# the auto-fallback retry. Keep this list conservative — over-aggressive
# stripping turns a meaningful query into noise. Covers English fillers and
# Chinese 怎么/如何/可以/吗 style modifiers that dominate user phrasing.
_FALLBACK_STOPWORDS: frozenset[str] = frozenset(
    {
        # English
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "do", "does", "did", "how", "what", "why", "when", "where", "which",
        "who", "whom", "can", "could", "should", "would", "to", "of", "in",
        "on", "at", "by", "for", "with", "from", "and", "or", "but", "if",
        "then", "than", "so", "as", "that", "this", "these", "those", "it",
        "its", "i", "we", "you", "they", "he", "she", "about", "into", "use",
        "using", "used", "please", "help", "any", "some", "my", "me", "our",
        # Chinese modifiers / fillers
        "的", "了", "是", "在", "和", "或", "与", "及", "也", "都", "就",
        "还", "又", "吗", "呢", "吧", "啊", "哦", "呀", "么", "嘛",
        "怎么", "怎样", "如何", "可以", "能否", "是否", "有没有", "什么",
        "为什么", "哪里", "哪个", "这个", "那个", "这样", "那样", "请问",
        "帮我", "我", "你", "他", "她", "它", "我们", "你们", "他们",
        "怎么办", "怎么用", "如何用", "如何做", "不到", "不了",
    }
)


def _shorten_query_for_fallback(query: str, *, max_terms: int = 3) -> str | None:
    """Return a shortened query with stopwords/common modifiers removed.

    Strategy (kept simple per task spec):
      1. Tokenize on whitespace and CJK boundaries.
      2. Drop entries that are pure punctuation or in ``_FALLBACK_STOPWORDS``.
      3. Keep the 2-3 shortest remaining tokens (shortest ≈ most specific
         for CJK; for English, short stems like "k8s" also win).
      4. If fewer than ``max_terms`` distinct content tokens survive, return
         them as-is. Return ``None`` when nothing material is left or when
         the shortened query is identical to the original.
    """
    import re

    raw = query.strip()
    if not raw:
        return None

    # Tokenize: whitespace splits, then split ASCII/CJK boundaries so
    # "kubernetes集群" becomes ["kubernetes", "集群"]. CJK runs stay whole —
    # we don't char-split because "集群" / "家常菜" carry real semantics as
    # a single lexeme; the stopword list only targets short modifier words.
    pieces: list[str] = []
    token_re = re.compile(r"[A-Za-z0-9_]+|[^\sA-Za-z0-9_\W]+", re.UNICODE)
    punct_trim = ".,!?;:\"'()[]{}<>《》「」『』，。！？；：、"
    for chunk in raw.split():
        chunk = chunk.strip(punct_trim)
        if not chunk:
            continue
        for m in token_re.finditer(chunk):
            pieces.append(m.group(0).lower())

    content = [p for p in pieces if p not in _FALLBACK_STOPWORDS]
    if not content:
        return None

    # Deduplicate while preserving order so "kubernetes kubernetes" collapses.
    seen: set[str] = set()
    unique: list[str] = []
    for p in content:
        if p not in seen:
            seen.add(p)
            unique.append(p)

    # Keep the ``max_terms`` shortest (≈ most specific) tokens, preserving
    # their original relative order so we don't reshuffle semantics.
    ranked = sorted(unique, key=lambda p: (len(p), unique.index(p)))
    kept = set(ranked[:max_terms])
    shortened = " ".join(p for p in unique if p in kept)

    if not shortened or shortened == raw.lower():
        return None
    return shortened
SUPERSEDES_RELATION_TYPE = "supersedes"
CONTRADICTS_RELATION_TYPE = "contradicts"

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _try_record_task_completed(
    service: Any,
    *,
    task_id: str,
    action: str,
    knowledge_id: int | None,
) -> None:
    """Best-effort call into ``health.tracking.record_task_completed``.

    The ``tracking`` module is written by a sibling workstream; importing it
    lazily and swallowing any error means the feedback / archive / create /
    update write paths never break when it is absent or temporarily broken
    (Phase 5 red line: task tracking is observational, never load-bearing).
    """
    try:
        from mnemo.health import tracking as _tracking  # type: ignore
    except Exception:  # noqa: BLE001
        return
    record_fn = getattr(_tracking, "record_task_completed", None)
    if record_fn is None:
        return
    try:
        import asyncio as _asyncio

        async def _run() -> None:
            async with service._session_factory() as _session:
                await record_fn(
                    _session,
                    task_id=task_id,
                    action=action,
                    knowledge_id=knowledge_id,
                )
                await _session.commit()

        try:
            loop = _asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            loop.create_task(_run())
        else:
            _asyncio.run(_run())
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "tracking.record_task_completed failed task_id=%s action=%s: %s",
            task_id,
            action,
            e,
        )


def _tags_list(tags_json: str | None) -> list[str]:
    if not tags_json:
        return []
    try:
        data = json.loads(tags_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(t) for t in data]


def _to_dict(knowledge: Knowledge, *, related: list[str] | None = None) -> dict[str, Any]:
    """Serialize a Knowledge row into a plain dict.

    ``related`` is optional — when provided, it is a list of titles of related
    knowledge entries. Callers that don't need it can pass None.
    """
    result: dict[str, Any] = {
        "id": knowledge.id,
        "title": knowledge.title,
        "tags": _tags_list(knowledge.tags),
        "summary": knowledge.summary,
        "content": knowledge.content,
        "scope": knowledge.scope,
        "project_name": knowledge.project_name,
        "session_id": knowledge.session_id,
        "source": knowledge.source,
        "claim_type": knowledge.claim_type,
        "status": knowledge.status,
        "content_hash": knowledge.content_hash,
        "version": knowledge.version,
        "extra_json": knowledge.extra_json,
        "created_at": knowledge.created_at.isoformat() if knowledge.created_at else None,
        "updated_at": knowledge.updated_at.isoformat() if knowledge.updated_at else None,
    }
    if related is not None:
        result["related"] = related
    return result


def _summary_dict(knowledge: Knowledge) -> dict[str, Any]:
    """Lighter representation for search/list results — no full content."""
    return {
        "id": knowledge.id,
        "title": knowledge.title,
        "tags": _tags_list(knowledge.tags),
        "summary": knowledge.summary,
        "scope": knowledge.scope,
        "project_name": knowledge.project_name,
        "claim_type": knowledge.claim_type,
        "status": knowledge.status,
        "version": knowledge.version,
        "created_at": knowledge.created_at.isoformat() if knowledge.created_at else None,
        "updated_at": knowledge.updated_at.isoformat() if knowledge.updated_at else None,
    }


class KnowledgeService:
    """Service façade. A fresh instance can be created per call or reused."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        config: MnemoConfig | None = None,
        embedding_service: EmbeddingService | None = None,
    ):
        self._config = config or MnemoConfig()
        self._session_factory = session_factory or get_session_factory(self._config)
        self._embedding = embedding_service

    # ---- helpers ----------------------------------------------------------

    async def _resolve_id(
        self, session: AsyncSession, id_or_title: int | str
    ) -> int | None:
        if isinstance(id_or_title, int):
            row = await kr.get_by_id(session, id_or_title)
            return row.id if row else None
        row = await kr.get_by_title(session, id_or_title)
        return row.id if row else None

    async def _apply_wikilinks(
        self,
        session: AsyncSession,
        source_id: int,
        content: str | None,
    ) -> list[str]:
        """Create wikilink relations for every existing target title.

        Returns the list of titles that actually resolved to relations
        (skipping dangling wikilinks and self-references).
        """
        targets = extract_wikilinks(content)
        linked: list[str] = []
        for title in targets:
            target = await kr.get_by_title(session, title)
            if target is None or target.id == source_id:
                continue
            await rr.create(
                session,
                source_id=source_id,
                target_id=target.id,
                relation_type=WIKILINK_RELATION_TYPE,
            )
            linked.append(target.title)
        return linked

    async def _apply_manual_relations(
        self,
        session: AsyncSession,
        source_id: int,
        related_titles: Iterable[str] | None,
    ) -> list[str]:
        if not related_titles:
            return []
        linked: list[str] = []
        for title in related_titles:
            if not title:
                continue
            target = await kr.get_by_title(session, title)
            if target is None or target.id == source_id:
                continue
            await rr.create(
                session,
                source_id=source_id,
                target_id=target.id,
                relation_type=MANUAL_RELATION_TYPE,
            )
            linked.append(target.title)
        return linked

    async def _apply_contradicts_with(
        self,
        session: AsyncSession,
        source_id: int,
        contradicts_with: Iterable[int | str] | None,
    ) -> list[int]:
        """Write ``contradicts`` relations + ``contradiction_marked`` events.

        Each entry is resolved (int → direct id, str → Knowledge.title lookup).
        Unknown titles / missing ids raise ValueError so the caller sees the
        failure instead of silently dropping edges (M5 C2 contract). Duplicate
        (source, target, 'contradicts') edges are skipped so repeated marks are
        idempotent at the relation level (M5 C3b); the audit event is still
        written on every call.

        Not gated by ``contradiction_pair_enabled`` — that flag only controls
        L4 heuristics + search pairing; Agent-initiated explicit marks must go
        through regardless (M5 C11).
        """
        if not contradicts_with:
            return []
        resolved: list[int] = []
        for entry in contradicts_with:
            if isinstance(entry, int):
                row = await kr.get_by_id(session, entry)
                if row is None:
                    raise ValueError(
                        f"contradicts_with: id {entry} not found"
                    )
                target_id = row.id
            else:
                title = str(entry)
                row = await kr.get_by_title(session, title)
                if row is None:
                    raise ValueError(
                        f"contradicts_with: title {title!r} not found"
                    )
                target_id = row.id
            if target_id == source_id:
                continue
            dup_stmt = select(Relation.id).where(
                Relation.source_id == source_id,
                Relation.target_id == target_id,
                Relation.relation_type == CONTRADICTS_RELATION_TYPE,
            )
            dup = (await session.execute(dup_stmt)).first()
            if dup is None:
                await rr.create(
                    session,
                    source_id=source_id,
                    target_id=target_id,
                    relation_type=CONTRADICTS_RELATION_TYPE,
                )
            session.add(
                KnowledgeEvent(
                    knowledge_id=source_id,
                    event_type="contradiction_marked",
                    payload_json=json.dumps(
                        {"contradicts_with_id": target_id}
                    ),
                )
            )
            resolved.append(target_id)
        await session.commit()
        return resolved

    async def _outgoing_target_ids(
        self,
        session: AsyncSession,
        source_id: int,
        relation_type: str,
    ) -> list[int]:
        """Return target ids of outgoing relations of the given type."""
        stmt = select(Relation.target_id).where(
            Relation.source_id == source_id,
            Relation.relation_type == relation_type,
        )
        result = await session.execute(stmt)
        return [row[0] for row in result.all()]

    async def _collect_related_titles(
        self, session: AsyncSession, knowledge_id: int
    ) -> list[str]:
        neighbors = await rr.get_related(session, knowledge_id, depth=1)
        return [k.title for k in neighbors]

    async def _attach_conflict_pairs(
        self,
        session: AsyncSession,
        results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Annotate each result with a ``conflicts_with: list[int]`` field.

        For every result, fetch all ``contradicts`` edges touching its id and
        collect the opposing endpoint's id (self-loops skipped defensively).
        Extra field only — does not consume a topk slot, does not reorder.

        Gated by ``contradiction_pair_enabled``: when off, caller should skip
        this method entirely so results keep Phase 2 shape (no extra key).
        """
        if not results:
            return results
        ids = [r["id"] for r in results]
        pairs = await rr.get_contradiction_pairs(session, ids)
        by_id: dict[int, list[int]] = {kid: [] for kid in ids}
        for p in pairs:
            src = p["source_id"]
            tgt = p["target_id"]
            if src == tgt:
                continue
            if src in by_id and tgt not in by_id[src]:
                by_id[src].append(tgt)
            if tgt in by_id and src not in by_id[tgt]:
                by_id[tgt].append(src)
        for r in results:
            r["conflicts_with"] = by_id.get(r["id"], [])
        return results

    def _normalize_task_context(self, task_context: str | None) -> str | None:
        """Validate task_context against the enum; unknown values -> 'general'.

        Emits a WARNING when the caller passed a non-None value outside the
        closed enum (``task_context_boosts`` keys). Empty string / unknown
        strings degrade to ``"general"``. ``None`` passes through unchanged
        so the Phase 2 equivalence path stays untouched.
        """
        if task_context is None:
            return None
        if not getattr(self._config, "context_aware_rank_enabled", False):
            return task_context
        boosts_by_ctx = getattr(self._config, "task_context_boosts", {}) or {}
        enum_values = set(boosts_by_ctx.keys())
        if task_context in enum_values:
            return task_context
        logger.warning(
            "task_context=%r not in enum %s, falling back to 'general'",
            task_context,
            sorted(enum_values),
        )
        return "general"

    def _resolve_claim_type_boost(
        self, task_context: str | None
    ) -> tuple[dict[str, float] | None, str | None]:
        """Pick the claim_type_boost dict for the given task_context.

        Returns ``(boost_dict, normalized_context)``:
        - ``boost_dict is None`` when the flag is off, ``task_context`` is
          ``None`` / ``"general"``, or the dict is empty — rerank falls back
          to Phase 2 behavior (no context boost).
        - Unknown values degrade to ``"general"`` with a WARNING log so Agent
          calls that drift away from the closed enum are observable without
          breaking search.
        """
        if not getattr(self._config, "context_aware_rank_enabled", False):
            return None, None
        if task_context is None:
            return None, None

        boosts_by_ctx = getattr(self._config, "task_context_boosts", {}) or {}
        enum_values = set(boosts_by_ctx.keys())

        normalized = task_context if task_context in enum_values else "general"

        if normalized == "general":
            return None, normalized

        boost = boosts_by_ctx.get(normalized) or {}
        if not boost:
            return None, normalized
        return dict(boost), normalized

    async def _quality_rerank(
        self,
        session: AsyncSession,
        fused: list[dict[str, Any]],
        query_scope: str | None,
        *,
        task_context: str | None = None,
    ) -> list[dict[str, Any]]:
        """Apply authority + contradiction + scope + freshness + stale rerank.

        Authority values are read from ``knowledge_meta`` (written by the
        create/update path). Missing rows fall back to 0 — equivalent to
        "authority not yet computed" and contributes a neutral 1.0 multiplier.

        Freshness (Phase 3 P3a-M2) is gated by ``config.freshness_enabled``:
        when off, ``freshness_lookup=None`` is passed and rerank degrades to
        Phase 2 behavior exactly.

        Stale penalty (Phase 3 P3a-M3) is gated by
        ``config.state_machine_enabled``. Both signals share one
        ``batch_lifecycle_fields`` roundtrip.
        """
        if not fused:
            return []
        ids = [e["id"] for e in fused]
        authority_map, contradiction_map = await ar.batch_authority_and_contradiction(
            session, ids
        )

        freshness_lookup: Any = None
        status_lookup: Any = None
        if self._config.freshness_enabled or self._config.state_machine_enabled:
            lifecycle = await kr.batch_lifecycle_fields(session, ids)

            if self._config.freshness_enabled:
                rows = {
                    kid: (fields["updated_at"], fields["claim_type"])
                    for kid, fields in lifecycle.items()
                }
                fresh_map = batch_freshness_lookup(rows, self._config)
                freshness_lookup = lambda kid: fresh_map.get(kid, 1.0)  # noqa: E731

            if self._config.state_machine_enabled:
                status_map = {
                    kid: fields["status"] for kid, fields in lifecycle.items()
                }
                status_lookup = lambda kid: status_map.get(kid)  # noqa: E731

        verification_lookup: Any = None
        if getattr(self._config, "feedback_loop_enabled", False):
            counts = await batch_feedback_counts(
                session, ids, config=self._config
            )
            sample_floor = int(
                getattr(self._config, "feedback_sample_floor", 3)
            )
            misleading_weight = float(
                getattr(self._config, "feedback_misleading_weight", 2.0)
            )
            low = float(getattr(self._config, "verification_mult_low", 0.7))
            high = float(getattr(self._config, "verification_mult_high", 1.3))
            verif_map: dict[int, float] = {}
            for kid, (h, m) in counts.items():
                verif_map[kid] = compute_verification_mult(
                    h,
                    m,
                    sample_floor=sample_floor,
                    misleading_weight=misleading_weight,
                    low=low,
                    high=high,
                )
            verification_lookup = lambda kid: verif_map.get(kid, 1.0)  # noqa: E731

        claim_type_boost, _ = self._resolve_claim_type_boost(task_context)
        contradicts_edge_lookup: Any = None
        if claim_type_boost and "contradicts_edge" in claim_type_boost:
            # reuse the same batch already loaded above for the contradiction
            # penalty lookup — both signals key on "has any contradicts edge".
            contradicts_edge_lookup = lambda kid: contradiction_map.get(  # noqa: E731
                kid, False
            )

        return apply_rerank(
            fused,
            authority_lookup=lambda kid: authority_map.get(kid, 0.0),
            contradiction_lookup=lambda kid: contradiction_map.get(kid, False),
            alpha=self._config.authority_multiplier,
            vec_only_min_final=self._config.vec_only_min_final,
            query_scope=query_scope,
            scope_mismatch_penalty=self._config.scope_mismatch_penalty,
            freshness_lookup=freshness_lookup,
            status_lookup=status_lookup,
            stale_penalty=self._config.stale_penalty_multiplier,
            state_machine_enabled=self._config.state_machine_enabled,
            verification_lookup=verification_lookup,
            claim_type_boost=claim_type_boost,
            contradicts_edge_lookup=contradicts_edge_lookup,
        )

    async def _recompute_authority_for(
        self,
        session: AsyncSession,
        knowledge_ids: Iterable[int],
    ) -> None:
        """Recompute and persist authority for the given ids.

        Called from the write path whenever incoming typed relations to a node
        may have changed. Self-targets count too — creating a relation touches
        the target node's authority.
        """
        seen: set[int] = set()
        for kid in knowledge_ids:
            if kid is None or kid in seen:
                continue
            seen.add(kid)
            await ar.recompute_and_store_authority(session, kid)

    def _get_embedding(self) -> EmbeddingService | None:
        """Return the configured embedding service, or None if disabled.

        Embedding is opt-in: callers pass an EmbeddingService explicitly.
        When not configured, create/update skip vector generation — FTS-only
        mode. Tests and FTS-only deployments don't need Ollama or the
        sqlite-vec virtual table.
        """
        return self._embedding

    async def _embed_and_store(
        self,
        session: AsyncSession,
        row: Knowledge,
    ) -> None:
        """Generate vector + persist via vector_repository.

        No-op when no embedding service is configured. Failures are captured
        as ``knowledge_event`` rows so the background reindex job can pick
        them up. Never re-raises — the write path must succeed even when
        embedding falls back to L2.
        """
        embedding = self._get_embedding()
        if embedding is None:
            return

        prepared = embedding.prepare_text(row.title, row.summary, row.content)
        try:
            vec = await embedding.embed(prepared)
        except Exception as e:  # noqa: BLE001
            logger.warning("embed raised unexpectedly: %s", e)
            vec = None

        if vec is None:
            session.add(
                KnowledgeEvent(
                    knowledge_id=row.id,
                    event_type="embedding_failed",
                    payload_json=json.dumps(
                        {"model": self._config.embedding_model}
                    ),
                )
            )
            await session.commit()
            return

        try:
            await vr.upsert_vector(
                session,
                row.id,
                self._config.embedding_model,
                vec,
            )
            await session.commit()
        except Exception as e:  # noqa: BLE001
            logger.error("vector upsert failed for kid=%s: %s", row.id, e)
            await session.rollback()
            session.add(
                KnowledgeEvent(
                    knowledge_id=row.id,
                    event_type="embedding_failed",
                    payload_json=json.dumps(
                        {"model": self._config.embedding_model, "error": str(e)}
                    ),
                )
            )
            await session.commit()

    async def _auto_link_by_vector(
        self,
        session: AsyncSession,
        row: Knowledge,
    ) -> list[tuple[int, float]]:
        """Create ``related`` edges to the top-K nearest neighbors by cosine.

        Runs after ``_embed_and_store`` so the new row's vector is already in
        ``knowledge_vec_idx``. No-op when:
          - embedding service is unavailable (fail-quiet, FTS-only deployments
            shouldn't error on create)
          - the new row has no vector (embed failed / returned None)
          - similarity of every neighbor < ``auto_link_threshold``

        Duplicates are skipped via ``exists_edge`` with undirected=True so a
        prior wikilink/manual/auto edge in either direction is respected. Edge
        weight is the cosine similarity in [-1, 1].

        Returns the list of ``(target_id, cosine_similarity)`` that actually
        got linked — useful for tests and audit payloads.
        """
        embedding = self._get_embedding()
        if embedding is None:
            return []

        threshold = float(getattr(self._config, "auto_link_threshold", 0.7))
        top_k = int(getattr(self._config, "auto_link_top_k", 5))
        if top_k <= 0:
            return []

        # Look up the newly stored vector rather than re-embedding — this is
        # the exact bytes in knowledge_vec_idx and avoids a redundant Ollama
        # round-trip.
        from mnemo.models.knowledge import KnowledgeVec  # local import: keep
        # module imports minimal, and this helper is the only caller.

        vec_row = (
            await session.execute(
                select(KnowledgeVec.vector).where(
                    KnowledgeVec.knowledge_id == row.id,
                    KnowledgeVec.model_name == self._config.embedding_model,
                )
            )
        ).first()
        if vec_row is None:
            return []
        query_vec = list(vr._unpack(vec_row[0]))

        neighbors = await vr.topk_similar_ids(
            session, query_vec, k=top_k, exclude_id=row.id
        )
        linked: list[tuple[int, float]] = []
        for target_id, cosine in neighbors:
            if cosine < threshold:
                continue
            if await rr.exists_edge(
                session,
                source_id=row.id,
                target_id=target_id,
                undirected=True,
            ):
                continue
            await rr.create(
                session,
                source_id=row.id,
                target_id=target_id,
                relation_type=AUTO_LINK_RELATION_TYPE,
                weight=float(cosine),
            )
            linked.append((target_id, float(cosine)))
        return linked

    async def _auto_link_v2(
        self,
        session: AsyncSession,
        row: Knowledge,
    ) -> list[tuple[int, str, str | None, float]]:
        """Phase 5b fine-grained keyword auto-edge builder.

        Flow (docs/phase5b/FINE_EDGE_PLAN.md §3):
        1. Extract keywords from the new row's text via jieba.
        2. For each keyword, FTS5-search existing knowledge and collect hits.
        3. For every candidate target, compute whole-doc cosine vs. the new
           row; gate by ``fine_edge_whole_floor``.
        4. Create ``auto_related`` edges at initial weight ``0.3`` with
           ``extra_json`` recording ``kw_source/kw_target/helpful_count/
           misleading_count``. Existing edges of any type are respected.

        When ``fine_edge_enabled`` is False, delegates to ``_auto_link_by_vector``
        for Phase 4 equivalence — this preserves the regression-gate contract.

        Returns ``[(target_id, match_type, keyword, cos_whole), ...]`` for the
        edges actually created (useful for tests / audits).
        """
        if not getattr(self._config, "fine_edge_enabled", True):
            legacy = await self._auto_link_by_vector(session, row)
            return [(tid, "whole_doc_fallback", None, cos) for tid, cos in legacy]

        embedding = self._get_embedding()
        if embedding is None:
            return []

        top_keywords = int(getattr(self._config, "fine_edge_top_keywords", 20))
        fts_limit = int(getattr(self._config, "fine_edge_fts_limit", 10))
        whole_floor = float(getattr(self._config, "fine_edge_whole_floor", 0.3))
        if top_keywords <= 0:
            return []

        from mnemo.models.knowledge import KnowledgeVec  # local import: keep
        # module imports minimal; used here and in _auto_link_by_vector only.
        from mnemo.utils.tokenizer import extract_keywords_for_edge

        # 1. Load the new row's vector (already persisted by _embed_and_store).
        vec_row = (
            await session.execute(
                select(KnowledgeVec.vector).where(
                    KnowledgeVec.knowledge_id == row.id,
                    KnowledgeVec.model_name == self._config.embedding_model,
                )
            )
        ).first()
        if vec_row is None:
            return []
        new_vec = list(vr._unpack(vec_row[0]))

        # 2. Extract keywords from the combined title + summary + content.
        source_text_parts = [row.title or "", row.summary or "", row.content or ""]
        source_text = "\n".join(p for p in source_text_parts if p)
        keywords = extract_keywords_for_edge(source_text, top_n=top_keywords)
        if not keywords:
            return []

        # 3. Collect candidate targets per keyword via FTS5. We deduplicate
        # across keywords, recording the first (highest-ranked) keyword match
        # as the trigger for the edge.
        first_keyword_by_target: dict[int, str] = {}
        for kw in keywords:
            hits = await sr.fts_search(
                session,
                kw,
                limit=fts_limit,
                include_archived=False,
            )
            for hit in hits:
                if hit.id == row.id:
                    continue
                if hit.id in first_keyword_by_target:
                    continue
                first_keyword_by_target[hit.id] = kw

        if not first_keyword_by_target:
            return []

        # 4. Whole-doc cosine gate. Load target vectors in one round-trip.
        target_ids = list(first_keyword_by_target.keys())
        target_vecs = (
            await session.execute(
                select(KnowledgeVec.knowledge_id, KnowledgeVec.vector).where(
                    KnowledgeVec.knowledge_id.in_(target_ids),
                    KnowledgeVec.model_name == self._config.embedding_model,
                )
            )
        ).all()
        cos_whole_by_id: dict[int, float] = {}
        for tid, vec_bytes in target_vecs:
            stored = list(vr._unpack(vec_bytes))
            # vr._cosine_distance returns distance in [0, 2]; similarity is
            # 1 - distance in [-1, 1]. Use similarity for the floor so tests
            # can reason about positive correlation directly.
            cos_whole_by_id[tid] = 1.0 - vr._cosine_distance(new_vec, stored)

        linked: list[tuple[int, str, str | None, float]] = []
        for tid in target_ids:
            cos_whole = cos_whole_by_id.get(tid)
            if cos_whole is None or cos_whole < whole_floor:
                continue
            # Any existing edge (any type, either direction) blocks creation.
            # Manual ``related`` / ``wikilink`` / ``supersedes`` / ``contradicts``
            # must win over the weaker auto signal (§6 FINE_EDGE_PLAN).
            if await rr.exists_edge(
                session,
                source_id=row.id,
                target_id=tid,
                undirected=True,
            ):
                continue
            kw = first_keyword_by_target[tid]
            extra = json.dumps(
                {
                    "kw_source": kw,
                    "kw_target": kw,
                    "kw_match_type": "exact",
                    "cos_whole": float(cos_whole),
                    "helpful_count": 0,
                    "misleading_count": 0,
                    "last_feedback_at": None,
                    "created_by": "auto_link_v2",
                }
            )
            await rr.create(
                session,
                source_id=row.id,
                target_id=tid,
                relation_type=AUTO_RELATED_RELATION_TYPE,
                weight=AUTO_RELATED_INITIAL_WEIGHT,
                extra_json=extra,
            )
            linked.append((tid, "exact", kw, float(cos_whole)))
        return linked

    async def _run_p1_write_detectors(
        self, session: AsyncSession, knowledge_id: int
    ) -> None:
        """Best-effort P1-3 + P1-4 detection after a write.

        Failures are swallowed — health detection must never break the
        triggering write. Hits are pushed into the in-memory task queue.
        """
        try:
            for task in await health_detectors.detect_hash_duplicate(
                session, knowledge_id
            ):
                _add_health_task(task)
            for task in await health_detectors.detect_missing_vector(
                session, knowledge_id
            ):
                _add_health_task(task)
        except Exception as e:  # noqa: BLE001
            logger.warning("health P1 write-detectors failed kid=%s: %s", knowledge_id, e)

    async def _run_p2_write_detectors(
        self,
        session: AsyncSession,
        knowledge_id: int,
        written_tags: list[str],
        gate: dict[str, Any] | None,
    ) -> None:
        """Best-effort P2-1/2/3/7 detection after a write.

        P2-1 reuses ``gate['semantic_similar']`` when available — we don't
        re-run the cosine probe. P2-2/3 check the freshly written row. P2-7
        compares the written tag list against existing non-self tag rows.
        """
        try:
            if gate is not None:
                await health_quality.detect_high_similarity(
                    session, knowledge_id, gate.get("semantic_similar")
                )
            await health_quality.detect_island_knowledge(session, knowledge_id)
            await health_quality.detect_weak_evidence(session, knowledge_id)
            if written_tags:
                await health_quality.detect_tag_inconsistency(
                    session, written_tags, exclude_knowledge_id=knowledge_id
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("health P2 write-detectors failed kid=%s: %s", knowledge_id, e)

    async def _run_p2_search_detectors(
        self,
        session: AsyncSession,
        query: str,
        hits: list[dict[str, Any]],
    ) -> None:
        """P2-5 (zero hit) + P2-6 (feedback reminder on served ids)."""
        try:
            await health_quality.detect_search_blind_spot(
                session, query, len(hits)
            )
            if hits:
                await health_quality.detect_feedback_gap(
                    session, [h["id"] for h in hits if "id" in h]
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("health P2 search-detectors failed: %s", e)

    # ---- public API -------------------------------------------------------

    async def create_knowledge(
        self,
        *,
        title: str,
        summary: str,
        content: str,
        tags: str | Iterable[str] | None = None,
        scope: str = "global",
        project_name: str | None = None,
        session_id: str | None = None,
        source: str | None = None,
        claim_type: str | None = None,
        status: str = "active",
        extra_json: str | None = None,
        related_titles: Iterable[str] | None = None,
        contradicts_with: Iterable[int | str] | None = None,
        task_id: str | None = None,
        trigger_source: str | None = None,
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            content_hash = kr.compute_content_hash(content)

            # Same-title active entry → treat as a version bump, not a new node.
            existing_same_title = await kr.get_by_title(
                session,
                title,
                scope=scope,
                project_name=project_name,
            )
            if existing_same_title is not None:
                _, new_row = await kr.supersede(
                    session,
                    existing_same_title.id,
                    title=title,
                    summary=summary,
                    content=content,
                    tags=tags,
                    scope=scope,
                    project_name=project_name,
                    session_id=session_id,
                    source=source,
                    claim_type=claim_type,
                    extra_json=extra_json,
                )
                wikilinked = await self._apply_wikilinks(
                    session, new_row.id, content
                )
                manual = await self._apply_manual_relations(
                    session, new_row.id, related_titles
                )
                await self._apply_contradicts_with(
                    session, new_row.id, contradicts_with
                )
                if self._embedding is not None:
                    await vr.delete_vector(session, existing_same_title.id)
                await self._embed_and_store(session, new_row)
                await self._auto_link_v2(session, new_row)
                related = list(dict.fromkeys(wikilinked + manual))
                result = _to_dict(new_row, related=related)
                result["supersedes_id"] = existing_same_title.id
                gate = await run_write_gate(
                    session, new_row, self._embedding, self._config
                )
                if gate is not None:
                    result["write_gate"] = gate
                await self._run_p1_write_detectors(session, new_row.id)
                await self._run_p2_write_detectors(
                    session, new_row.id, _tags_list(new_row.tags), gate
                )
                if task_id is not None:
                    _try_record_task_completed(
                        self,
                        task_id=task_id,
                        action="create_knowledge",
                        knowledge_id=new_row.id,
                    )
                return result

            row = await kr.create(
                session,
                title=title,
                summary=summary,
                content=content,
                tags=tags,
                scope=scope,
                project_name=project_name,
                session_id=session_id,
                source=source,
                claim_type=claim_type,
                status=status,
                extra_json=extra_json,
            )
            wikilinked = await self._apply_wikilinks(session, row.id, content)
            manual = await self._apply_manual_relations(session, row.id, related_titles)
            await self._apply_contradicts_with(session, row.id, contradicts_with)
            await self._embed_and_store(session, row)
            await self._auto_link_v2(session, row)

            related = list(dict.fromkeys(wikilinked + manual))
            result = _to_dict(row, related=related)

            # Content-hash collision → warn but don't block: the agent may
            # intentionally record the same content under a different title.
            dup = await kr.find_duplicate_by_hash(
                session, content_hash, exclude_id=row.id
            )
            if dup is not None:
                result["duplicate_warning"] = {
                    "id": dup.id,
                    "title": dup.title,
                }
            gate = await run_write_gate(
                session, row, self._embedding, self._config
            )
            if gate is not None:
                result["write_gate"] = gate
            await self._run_p1_write_detectors(session, row.id)
            await self._run_p2_write_detectors(
                session, row.id, _tags_list(row.tags), gate
            )
            if task_id is not None:
                _try_record_task_completed(
                    self,
                    task_id=task_id,
                    action="create_knowledge",
                    knowledge_id=row.id,
                )
            return result

    async def get_knowledge(self, id_or_title: int | str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            kid = await self._resolve_id(session, id_or_title)
            if kid is None:
                return None
            row = await kr.get_by_id(session, kid)
            if row is None:
                return None
            await self._apply_stale_lifecycle(session, [row])
            related = await self._collect_related_titles(session, row.id)
            result = _to_dict(row, related=related)
            if row.status == kr.STATUS_SUPERSEDED:
                successor = await rr.find_successor(session, row.id)
                if successor is not None:
                    result["superseded_by"] = {
                        "id": successor.id,
                        "title": successor.title,
                    }
            # _apply_stale_lifecycle only flushes; persist any stale transition
            # and last_accessed touches before the session unwinds.
            await session.commit()
            return result

    def _state_machine_enabled(self) -> bool:
        """Read the state-machine flag, preferring the live env var.

        ``self._config`` is captured at service construction, but tests use
        ``monkeypatch.setenv`` to flip ``MNEMO_STATE_MACHINE_ENABLED`` after
        the service is built. A fresh ``MnemoConfig`` re-reads the env and
        wins when the test asks for it; otherwise we fall back to the
        captured config attribute.
        """
        env = os.environ.get("MNEMO_STATE_MACHINE_ENABLED")
        if env is not None:
            return env.strip().lower() not in {"0", "false", "no", "off", ""}
        return bool(getattr(self._config, "state_machine_enabled", True))

    async def _apply_stale_lifecycle(
        self,
        session: AsyncSession,
        rows: list[Knowledge],
    ) -> None:
        """Read-lazy stale transition + last_accessed touch (Phase 3 P3a-M3).

        Refreshes ``last_accessed_at`` for every surfaced row (subject to the
        60-second dedupe in ``touch_last_accessed``) and flips any row past
        its per-claim-type threshold to ``status='stale'``, writing a
        ``knowledge_event(event_type='stale_transition')`` in the same
        transaction.

        Gated by ``config.state_machine_enabled``. When off this is a no-op
        so Phase 2 callers see exactly their old behavior.
        """
        if not self._state_machine_enabled():
            return
        if not rows:
            return

        # The flag may have flipped via env since construction — build a
        # shim config that reflects the live state without rebuilding every
        # unrelated field.
        lifecycle_cfg = self._config
        if not self._config.state_machine_enabled and self._state_machine_enabled():
            # Env flipped on; synthesize an enabled view.
            from copy import copy as _copy

            lifecycle_cfg = _copy(self._config)
            lifecycle_cfg.state_machine_enabled = True

        # Check stale transition BEFORE touching last_accessed_at — touching
        # first would reset the "no_access_days" clock and prevent the row
        # from ever flipping.
        #
        # Core UPDATE still triggers ``Column.onupdate`` (SQLAlchemy fires the
        # hook for both ORM and Core updates). Pin ``updated_at`` explicitly
        # to its own column value so the ``onupdate=_utcnow`` hook cannot
        # bump it — a read-lazy stale flip must not reset the freshness
        # signal and re-trigger stale checks later. This mirrors
        # lifecycle_service.touch_last_accessed.
        transitioned_ids: list[int] = []
        for row in rows:
            if check_stale_transition(row, lifecycle_cfg):
                prior_status = row.status
                stmt = sa_update(Knowledge.__table__).where(
                    Knowledge.__table__.c.id == row.id
                ).values(status="stale", updated_at=Knowledge.__table__.c.updated_at)
                await session.execute(stmt)
                session.add(
                    KnowledgeEvent(
                        knowledge_id=row.id,
                        event_type="stale_transition",
                        payload_json=json.dumps(
                            {
                                "from_status": prior_status,
                                "to_status": "stale",
                                "claim_type": row.claim_type,
                            }
                        ),
                    )
                )
                transitioned_ids.append(row.id)

        ids = [r.id for r in rows]
        await touch_last_accessed(
            session,
            ids,
            dedupe_window_s=float(self._config.last_accessed_touch_interval_s),
        )
        # Read paths (search / get_knowledge) must not commit — a stray commit
        # on the read path can break caller-visible transaction boundaries and
        # is an extra sync point on every query. Flush to make the writes
        # visible within this session; the caller owns commit.
        await session.flush()
        # Refresh rows so attribute reads reflect the flushed status change.
        # Without this, `row.status` in the calling code still shows "active"
        # because the Core UPDATE bypassed the identity map.
        if transitioned_ids:
            for row in rows:
                if row.id in transitioned_ids:
                    await session.refresh(row)

    async def update_knowledge(
        self,
        knowledge_id: int,
        **fields: Any,
    ) -> dict[str, Any]:
        """Create a new immutable version.

        The row at ``knowledge_id`` is flipped to ``superseded`` and a new
        active row is inserted with ``version += 1``. The returned dict
        describes the new row (its id is different from the input).

        Manual "related" edges from the old row are carried over to the new
        one so hand-curated connections survive edits. Wikilinks are re-derived
        from the new content.

        Phase 5 tracking: ``task_id`` and ``trigger_source`` may be passed as
        keyword arguments. When a valid ``task_id`` accompanies a successful
        update, a best-effort ``task_completed`` marker is written via
        ``health.tracking`` (failures are swallowed).
        """
        contradicts_with = fields.pop("contradicts_with", None)
        task_id = fields.pop("task_id", None)
        fields.pop("trigger_source", None)
        async with self._session_factory() as session:
            old = await kr.get_by_id(session, knowledge_id)
            if old is None:
                raise ValueError(f"Knowledge id={knowledge_id} not found")

            manual_targets = await rr.get_outgoing_targets_by_type(
                session, old.id, MANUAL_RELATION_TYPE
            )
            # Carry existing contradicts edges over to the new version so
            # supersede doesn't silently drop them; the ``contradicts_with``
            # parameter then appends new marks on top.
            existing_contradicts_stmt_ids = await self._outgoing_target_ids(
                session, old.id, CONTRADICTS_RELATION_TYPE
            )

            _, new_row = await kr.supersede(session, old.id, **fields)

            await self._apply_wikilinks(session, new_row.id, new_row.content)
            await self._apply_manual_relations(
                session, new_row.id, manual_targets
            )
            if existing_contradicts_stmt_ids:
                await self._apply_contradicts_with(
                    session, new_row.id, existing_contradicts_stmt_ids
                )
            await self._apply_contradicts_with(
                session, new_row.id, contradicts_with
            )
            if self._embedding is not None:
                await vr.delete_vector(session, old.id)
            await self._embed_and_store(session, new_row)

            related = await self._collect_related_titles(session, new_row.id)
            result = _to_dict(new_row, related=related)
            result["supersedes_id"] = old.id
            if task_id is not None:
                _try_record_task_completed(
                    self,
                    task_id=task_id,
                    action="update_knowledge",
                    knowledge_id=new_row.id,
                )
            return result

    async def delete_knowledge(self, knowledge_id: int) -> bool:
        async with self._session_factory() as session:
            if self._embedding is not None:
                await vr.delete_vector(session, knowledge_id)
            return await kr.delete(session, knowledge_id)

    async def archive_knowledge(
        self,
        knowledge_id: int,
        *,
        reason: str | None = None,
        task_id: str | None = None,
        trigger_source: str | None = None,
    ) -> dict[str, Any]:
        """Flip a knowledge row to ``status='archived'`` + write an audit event.

        Thin facade over ``archive_service.archive_knowledge`` so callers
        (MCP / CLI) stay service-level. ``task_id`` / ``trigger_source`` are
        Phase 5 tracking fields written into the ``archived`` event payload
        so /stats can tell search-dispatch completion from agent-initiative
        archival.
        """
        async with self._session_factory() as session:
            result = await archive_kr(
                session,
                knowledge_id,
                reason=reason,
                config=self._config,
                task_id=task_id,
                trigger_source=trigger_source,
            )
        if result.get("success") and task_id is not None:
            _try_record_task_completed(
                self,
                task_id=task_id,
                action="archive_knowledge",
                knowledge_id=knowledge_id,
            )
        return result

    async def feedback_knowledge(
        self,
        *,
        knowledge_id: int,
        signal: str,
        reason: str | None = None,
        actor: str = "agent:unknown",
        task_id: str | None = None,
        trigger_source: str | None = None,
    ) -> dict[str, Any]:
        """Record agent feedback on a knowledge row.

        Thin facade over ``feedback_service.record_feedback``. Phase 5 task
        tracking: ``task_id`` / ``trigger_source`` are transparently written
        into the ``feedback`` event payload so /stats can split search-
        dispatch completion from agent-initiative feedback. Successful
        dispatch-sourced feedback also lands a ``task_completed`` marker
        via ``health.tracking`` (best-effort — import failures do not
        break the core write path).
        """
        result = await _feedback_service.record_feedback(
            self,
            knowledge_id=knowledge_id,
            signal=signal,
            reason=reason,
            actor=actor,
            config=self._config,
            task_id=task_id,
            trigger_source=trigger_source,
        )
        if result.get("success") and task_id is not None:
            _try_record_task_completed(
                self,
                task_id=task_id,
                action="feedback_knowledge",
                knowledge_id=knowledge_id,
            )
        return result

    async def unarchive_knowledge(self, knowledge_id: int) -> dict[str, Any]:
        """Restore an archived row to ``status='active'``."""
        async with self._session_factory() as session:
            return await unarchive_kr(session, knowledge_id, config=self._config)

    async def _apply_sort_by(
        self,
        session: AsyncSession,
        results: list[dict[str, Any]],
        sort_by: str,
    ) -> list[dict[str, Any]]:
        """Optionally re-order search results by ``created_at`` or feedback weight.

        ``relevance`` is a no-op and returns the input unchanged — the hot path
        must not pay any extra query cost. ``time`` pulls ``created_at`` for
        each surviving id in one round-trip and sorts desc. ``feedback`` reuses
        the rerank output's ``verification_mult`` when present (hybrid path) or
        falls back to a fresh ``batch_feedback_counts`` lookup for fts/vector
        paths. Ties preserve the caller-provided order (stable sort).
        """
        if sort_by == "relevance" or not results:
            return results
        if sort_by == "time":
            ids = [r["id"] for r in results]
            stmt = select(Knowledge.id, Knowledge.created_at).where(
                Knowledge.id.in_(ids)
            )
            rows = (await session.execute(stmt)).all()
            created_map = {kid: ts for kid, ts in rows}
            results.sort(
                key=lambda r: created_map.get(r["id"]) or _EPOCH,
                reverse=True,
            )
            return results
        if sort_by == "feedback":
            need_lookup = any("verification_mult" not in r for r in results)
            if need_lookup:
                ids = [r["id"] for r in results]
                counts = await batch_feedback_counts(
                    session, ids, config=self._config
                )
                sample_floor = int(
                    getattr(self._config, "feedback_sample_floor", 3)
                )
                misleading_weight = float(
                    getattr(self._config, "feedback_misleading_weight", 2.0)
                )
                low = float(getattr(self._config, "verification_mult_low", 0.7))
                high = float(getattr(self._config, "verification_mult_high", 1.3))
                verif_map: dict[int, float] = {}
                for kid, (h, m) in counts.items():
                    verif_map[kid] = compute_verification_mult(
                        h,
                        m,
                        sample_floor=sample_floor,
                        misleading_weight=misleading_weight,
                        low=low,
                        high=high,
                    )
                for r in results:
                    r.setdefault(
                        "verification_mult", verif_map.get(r["id"], 1.0)
                    )
            results.sort(
                key=lambda r: float(r.get("verification_mult", 1.0)),
                reverse=True,
            )
            return results
        return results

    async def _run_hybrid_search(
        self,
        session: AsyncSession,
        query: str,
        *,
        scope: str | None,
        project_name: str | None,
        limit: int,
        include_archived: bool,
        task_context: str | None,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Execute one pass of the hybrid FTS+vector pipeline.

        Returns ``(final_hits, degraded)``. ``degraded`` is True when the
        vector channel was unavailable and the pipeline collapsed to FTS-only
        — callers skip the auto-fallback retry in that case since FTS-only is
        already its own failure mode.
        """
        fts_hits = await sr.fts_search(
            session,
            query,
            scope=scope,
            project_name=project_name,
            limit=limit,
            include_archived=include_archived,
        )
        fts_dicts = [_summary_dict(h) for h in fts_hits]

        embedding = self._get_embedding()
        degraded_reason: str | None = None
        vec_dicts: list[dict[str, Any]] = []
        # 当 FTS 全 miss 时向量通道要用更严的阈值把 OOD query 挡住。
        # M3b 用 0.55（家常菜 0.728 / 外星人 0.587 都会被挡），但 REL-N-06
        # "kubernetes 集群"的 vec_only top-1 在 0.55 下还会滑进来。M4
        # task #3 网格搜 (1875 组合 neg=10/10 硬约束) 收敛到 0.60：让
        # REL-N-06 的 project-scoped 候选通过 scope_mismatch_penalty*rrf
        # 降到 vec_only_min_final 以下，同时保留跨语义正面 query
        # (如 INT-10 "中文搜不到怎么办" cos≈0.45) 的召回能力。
        # FTS 有命中时继续用默认 0.8，保证融合路径有合理召回。
        vector_threshold = (
            0.55 if not fts_hits else vr.DEFAULT_COSINE_DISTANCE_THRESHOLD
        )
        if embedding is None:
            degraded_reason = "embedding_service_unavailable"
        else:
            try:
                query_vec = await embedding.embed(query)
            except Exception as e:  # noqa: BLE001
                logger.warning("hybrid: query embed failed: %s", e)
                query_vec = None
                degraded_reason = f"embed_error:{type(e).__name__}"
            if query_vec is None:
                if degraded_reason is None:
                    degraded_reason = "embed_returned_none"
            else:
                vec_hits = await vr.vector_search(
                    session,
                    query_vec,
                    scope=scope,
                    project_name=project_name,
                    limit=limit,
                    include_archived=include_archived,
                    distance_threshold=vector_threshold,
                )
                vec_dicts = [_summary_dict(h) for h in vec_hits]

        if degraded_reason is not None:
            session.add(
                KnowledgeEvent(
                    knowledge_id=None,
                    event_type="hybrid_degraded",
                    payload_json=json.dumps(
                        {"reason": degraded_reason, "query": query}
                    ),
                )
            )
            await self._apply_stale_lifecycle(session, list(fts_hits))
            await session.commit()
            # Degraded path still needs rerank so stale_penalty / freshness /
            # scope_mismatch / feedback signals apply. Run FTS dicts through
            # rrf_fuse with an empty vec channel to attach rrf_score + source
            # fields that apply_rerank expects.
            fused = rrf_fuse(fts_dicts, [], k=60)
            reranked = await self._quality_rerank(
                session, fused, query_scope=scope, task_context=task_context
            )
            final_hits = reranked[:limit]
            if self._config.contradiction_pair_enabled:
                await self._attach_conflict_pairs(session, final_hits)
            return final_hits, True

        if not fts_dicts and not vec_dicts:
            return [], False

        fused = rrf_fuse(fts_dicts, vec_dicts, k=60)
        reranked = await self._quality_rerank(
            session, fused, query_scope=scope, task_context=task_context
        )
        final_hits = reranked[:limit]
        if final_hits:
            surviving_ids = [e["id"] for e in final_hits]
            survivors = [
                await kr.get_by_id(session, sid) for sid in surviving_ids
            ]
            await self._apply_stale_lifecycle(
                session, [r for r in survivors if r is not None]
            )
            await session.commit()
        if self._config.contradiction_pair_enabled:
            await self._attach_conflict_pairs(session, final_hits)
        return final_hits, False

    async def search(
        self,
        query: str,
        *,
        scope: str | None = None,
        project_name: str | None = None,
        limit: int = 20,
        mode: str = "hybrid",
        include_archived: bool = False,
        task_context: str | None = None,
        sort_by: str = "relevance",
    ) -> list[dict[str, Any]]:
        """Search knowledge. ``mode``:
        - ``"hybrid"`` (default, M2): run FTS + vector in parallel and fuse
          with RRF. When embedding is unavailable, silently degrades to pure
          FTS and records a ``hybrid_degraded`` knowledge_event.
        - ``"fts"``: FTS5 lexical search only (Phase 1 behavior).
        - ``"vector"``: KNN via sqlite-vec only. Empty result when embedding
          service is down.

        ``include_archived`` (Phase 3 P3a-M3): default hides ``status='archived'``
        rows. Pass ``True`` to surface them (e.g. admin / audit tools).

        ``sort_by`` controls the final result ordering:
        - ``"relevance"`` (default): RRF + rerank score, unchanged Phase 2+3 path.
        - ``"time"``: descending by ``created_at``.
        - ``"feedback"``: descending by ``verification_mult`` (P3a-M4 feedback
          weight). Candidate set is still the rerank output; only the final
          order is overridden.
        """
        if mode not in ("fts", "vector", "hybrid"):
            raise ValueError(f"unknown search mode: {mode!r}")
        if sort_by not in ("relevance", "time", "feedback"):
            raise ValueError(f"unknown sort_by: {sort_by!r}")

        # Normalize task_context up front so the WARNING log fires regardless
        # of search mode (fts / vector paths don't call _quality_rerank but
        # still need the enum-defence behavior).
        task_context = self._normalize_task_context(task_context)

        async with self._session_factory() as session:
            if mode == "vector":
                embedding = self._get_embedding()
                if embedding is None:
                    return []
                query_vec = await embedding.embed(query)
                if query_vec is None:
                    return []
                hits = await vr.vector_search(
                    session,
                    query_vec,
                    scope=scope,
                    project_name=project_name,
                    limit=limit,
                    include_archived=include_archived,
                )
                await self._apply_stale_lifecycle(session, list(hits))
                await session.commit()
                results = [_summary_dict(h) for h in hits]
                if self._config.contradiction_pair_enabled:
                    await self._attach_conflict_pairs(session, results)
                await self._run_p2_search_detectors(session, query, results)
                return await self._apply_sort_by(session, results, sort_by)

            if mode == "fts":
                hits = await sr.fts_search(
                    session,
                    query,
                    scope=scope,
                    project_name=project_name,
                    limit=limit,
                    include_archived=include_archived,
                )
                await self._apply_stale_lifecycle(session, list(hits))
                await session.commit()
                results = [_summary_dict(h) for h in hits]
                if self._config.contradiction_pair_enabled:
                    await self._attach_conflict_pairs(session, results)
                await self._run_p2_search_detectors(session, query, results)
                return await self._apply_sort_by(session, results, sort_by)

            # hybrid
            final_hits, degraded = await self._run_hybrid_search(
                session,
                query,
                scope=scope,
                project_name=project_name,
                limit=limit,
                include_archived=include_archived,
                task_context=task_context,
            )

            # Auto-fallback (P0 UX): when the first attempt yields zero hits
            # and the query has strippable stopwords, retry once with a
            # shortened form. Applies regardless of degraded status — FTS-only
            # paths also suffer from long-query dilution. Skipped only when the
            # flag is off. Tagged hits carry ``auto_fallback: true``.
            if (
                not final_hits
                and getattr(self._config, "search_auto_fallback_enabled", True)
            ):
                shortened = _shorten_query_for_fallback(query)
                if shortened:
                    retry_hits, _ = await self._run_hybrid_search(
                        session,
                        shortened,
                        scope=scope,
                        project_name=project_name,
                        limit=limit,
                        include_archived=include_archived,
                        task_context=task_context,
                    )
                    if retry_hits:
                        for h in retry_hits:
                            h["auto_fallback"] = True
                        final_hits = retry_hits

            await self._run_p2_search_detectors(session, query, final_hits)
            return await self._apply_sort_by(session, final_hits, sort_by)

    async def get_related(
        self,
        id_or_title: int | str,
        depth: int = 1,
    ) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            kid = await self._resolve_id(session, id_or_title)
            if kid is None:
                return []
            neighbors = await rr.get_related(session, kid, depth=depth)
            return [_summary_dict(n) for n in neighbors]

    async def list_tags(self, *, scope: str | None = None) -> list[str]:
        async with self._session_factory() as session:
            return await sr.list_tags(session, scope=scope)

    async def search_by_tag(
        self,
        tags: list[str],
        *,
        scope: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            hits = await sr.search_by_tag(session, tags, scope=scope, limit=limit)
            return [_summary_dict(h) for h in hits]

    async def list_knowledge(
        self,
        *,
        scope: str | None = None,
        project_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            rows = await kr.list_all(
                session,
                scope=scope,
                project_name=project_name,
                limit=limit,
                offset=offset,
            )
            return [_summary_dict(r) for r in rows]
