"""Vector storage & KNN search via sqlite-vec.

Two sinks per write:
- ``KnowledgeVec`` (ORM): persistent source of truth, keeps model_name so we
  can audit which model produced each vector and trigger a full reindex on
  model switch.
- ``knowledge_vec_idx`` (vec0 virtual table): single-model KNN index used at
  query time. Rebuilt from ``KnowledgeVec`` when the active model changes.

Vectors are packed little-endian float32 (sqlite-vec's on-disk format).
"""

from __future__ import annotations

import math
import struct

from sqlalchemy import delete as sa_delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mnemo.db import VECTOR_DIM
from mnemo.models.knowledge import Knowledge, KnowledgeVec


DEFAULT_COSINE_DISTANCE_THRESHOLD = 0.8


def _unpack(vector_bytes: bytes) -> list[float]:
    return list(struct.unpack(f"{VECTOR_DIM}f", vector_bytes))


def _cosine_distance(a: list[float], b: list[float]) -> float:
    """Return cosine distance in [0, 2]. Zero vectors → 2.0 (fully unrelated)."""
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 2.0
    cos = dot / (math.sqrt(na) * math.sqrt(nb))
    if cos > 1.0:
        cos = 1.0
    elif cos < -1.0:
        cos = -1.0
    return 1.0 - cos


def _pack(vector: list[float]) -> bytes:
    if len(vector) != VECTOR_DIM:
        raise ValueError(
            f"vector dimension mismatch: got {len(vector)}, expected {VECTOR_DIM}"
        )
    return struct.pack(f"{VECTOR_DIM}f", *vector)


async def upsert_vector(
    session: AsyncSession,
    knowledge_id: int,
    model_name: str,
    vector: list[float],
) -> None:
    """Write both the ORM row and the ANN index entry.

    KnowledgeVec keeps history per (knowledge_id, model_name); the ANN index
    keeps only the latest vector for the active model.
    """
    packed = _pack(vector)

    existing = await session.execute(
        select(KnowledgeVec).where(
            KnowledgeVec.knowledge_id == knowledge_id,
            KnowledgeVec.model_name == model_name,
        )
    )
    row = existing.scalar_one_or_none()
    if row is None:
        session.add(
            KnowledgeVec(
                knowledge_id=knowledge_id,
                model_name=model_name,
                vector=packed,
            )
        )
    else:
        row.vector = packed

    await session.execute(
        text("DELETE FROM knowledge_vec_idx WHERE knowledge_id = :kid"),
        {"kid": knowledge_id},
    )
    await session.execute(
        text(
            "INSERT INTO knowledge_vec_idx (knowledge_id, embedding) "
            "VALUES (:kid, :emb)"
        ),
        {"kid": knowledge_id, "emb": packed},
    )


async def delete_vector(session: AsyncSession, knowledge_id: int) -> None:
    """Remove the vector from both ORM table and ANN index.

    Knowledge→KnowledgeVec already cascades at the FK level, but the vec0
    virtual table has no FK, so we clean it up explicitly here and any caller
    that deletes a Knowledge row should call this first.
    """
    await session.execute(
        sa_delete(KnowledgeVec).where(KnowledgeVec.knowledge_id == knowledge_id)
    )
    await session.execute(
        text("DELETE FROM knowledge_vec_idx WHERE knowledge_id = :kid"),
        {"kid": knowledge_id},
    )


async def vector_search(
    session: AsyncSession,
    query_vec: list[float],
    *,
    scope: str | None = None,
    project_name: str | None = None,
    limit: int = 20,
    offset: int = 0,
    include_superseded: bool = False,
    include_archived: bool = False,
    distance_threshold: float = DEFAULT_COSINE_DISTANCE_THRESHOLD,
    candidate_ids: list[int] | None = None,
) -> list[Knowledge]:
    """KNN search, optionally scoped. Returns Knowledge rows ordered by cosine
    distance ascending (closest first).

    The vec0 index uses L2 distance on un-normalized vectors, which gives an
    unstable scale for threshold-based filtering. To get a stable [0, 2]
    scale, we re-score candidates with cosine distance from the raw vectors
    stored in ``KnowledgeVec`` and drop anything above ``distance_threshold``.
    This prevents KNN from returning Top-K matches for queries that have no
    semantically related entry at all (e.g. "外星人入侵").

    Scope/project filters are applied after the vec0 KNN because vec0 does
    not support joined WHERE filters — we over-fetch 3x (cap 100) then filter.

    When ``candidate_ids`` is passed, vec0 is bypassed entirely: cosine is
    computed directly against those ``KnowledgeVec`` rows. This is the
    FTS-prefilter fast path — at 500K rows vec0 brute-forces ~330ms per call,
    but scoring a handful of FTS hits in Python costs <5ms. M4 task #5.
    """
    if candidate_ids is not None:
        if not candidate_ids:
            return []
        vec_rows = (
            await session.execute(
                select(KnowledgeVec.knowledge_id, KnowledgeVec.vector).where(
                    KnowledgeVec.knowledge_id.in_(candidate_ids)
                )
            )
        ).all()
    else:
        packed = _pack(query_vec)
        # Empirically vec0 KNN latency at 500K is near-constant for
        # k ∈ [20, 200] (brute-force regardless), so larger over-fetch buys no
        # recall and adds Python rescore work. 3x margin covers scope/project
        # post-filter drop.
        knn_limit = min(max(limit * 3, limit), 100)
        knn = await session.execute(
            text(
                "SELECT knowledge_id, distance FROM knowledge_vec_idx "
                "WHERE embedding MATCH :emb AND k = :k "
                "ORDER BY distance"
            ),
            {"emb": packed, "k": knn_limit},
        )
        hits = knn.all()
        if not hits:
            return []

        knn_candidate_ids = [row[0] for row in hits]
        vec_rows = (
            await session.execute(
                select(KnowledgeVec.knowledge_id, KnowledgeVec.vector).where(
                    KnowledgeVec.knowledge_id.in_(knn_candidate_ids)
                )
            )
        ).all()

    cosine_by_id: dict[int, float] = {}
    for kid, vec_bytes in vec_rows:
        stored = _unpack(vec_bytes)
        cos_d = _cosine_distance(query_vec, stored)
        if cos_d <= distance_threshold:
            cosine_by_id[kid] = cos_d
    if not cosine_by_id:
        return []

    stmt = select(Knowledge).where(Knowledge.id.in_(list(cosine_by_id.keys())))
    if not include_superseded and not include_archived:
        stmt = stmt.where(Knowledge.status.notin_(["superseded", "archived"]))
    elif not include_superseded:
        stmt = stmt.where(Knowledge.status != "superseded")
    elif not include_archived:
        stmt = stmt.where(Knowledge.status != "archived")
    if scope is not None:
        stmt = stmt.where(Knowledge.scope == scope)
    if project_name is not None:
        stmt = stmt.where(Knowledge.project_name == project_name)

    rows = (await session.execute(stmt)).scalars().all()
    rows_sorted = sorted(rows, key=lambda k: cosine_by_id.get(k.id, 2.0))
    return rows_sorted[offset : offset + limit]


async def topk_cosine_by_scope(
    session: AsyncSession,
    query_vec: list[float],
    scope: str,
    project_name: str | None = None,
    k: int = 50,
) -> list[dict]:
    """Return active top-k ``{id, title, cosine}`` inside *scope* for Write-gate L2.

    Two-stage: vec0 KNN over-fetch (k*3 capped at 100) then rescoring with
    exact cosine similarity from ``KnowledgeVec``. Scope / project / status
    filters are applied after KNN because vec0 doesn't join. ``cosine`` in
    the return payload is the similarity in [-1, 1] (not distance) — callers
    can threshold directly (e.g. ≥ 0.92).
    """
    if k <= 0:
        return []

    packed = _pack(query_vec)
    knn_limit = min(max(k * 3, k), 100)
    knn = await session.execute(
        text(
            "SELECT knowledge_id FROM knowledge_vec_idx "
            "WHERE embedding MATCH :emb AND k = :k "
            "ORDER BY distance"
        ),
        {"emb": packed, "k": knn_limit},
    )
    candidate_ids = [row[0] for row in knn.all()]
    if not candidate_ids:
        return []

    vec_rows = (
        await session.execute(
            select(KnowledgeVec.knowledge_id, KnowledgeVec.vector).where(
                KnowledgeVec.knowledge_id.in_(candidate_ids)
            )
        )
    ).all()
    cosine_by_id: dict[int, float] = {}
    for kid, vec_bytes in vec_rows:
        stored = _unpack(vec_bytes)
        cos_d = _cosine_distance(query_vec, stored)
        cosine_by_id[kid] = 1.0 - cos_d

    stmt = (
        select(Knowledge.id, Knowledge.title)
        .where(Knowledge.id.in_(list(cosine_by_id.keys())))
        .where(Knowledge.status == "active")
        .where(Knowledge.scope == scope)
    )
    if project_name is not None:
        stmt = stmt.where(Knowledge.project_name == project_name)

    rows = (await session.execute(stmt)).all()
    ranked = [
        {"id": kid, "title": title, "cosine": cosine_by_id[kid]}
        for kid, title in rows
        if kid in cosine_by_id
    ]
    ranked.sort(key=lambda r: r["cosine"], reverse=True)
    return ranked[:k]


async def topk_similar_ids(
    session: AsyncSession,
    query_vec: list[float],
    *,
    k: int,
    exclude_id: int | None = None,
) -> list[tuple[int, float]]:
    """Return ``[(knowledge_id, cosine_similarity), ...]`` for the K nearest
    active rows to ``query_vec``, sorted by similarity descending.

    Similarity is cosine in ``[-1, 1]`` (not distance). ``exclude_id`` lets the
    caller drop the self-row (auto-link by vector asks for neighbors of the
    newly written row — its own vector ranks first at distance 0).

    Scope / project filters are intentionally not applied here — auto-link
    callers want semantic neighbors regardless of scope. Status filters drop
    superseded/archived rows so auto-links only point at live knowledge.
    """
    if k <= 0:
        return []

    packed = _pack(query_vec)
    knn_limit = min(max((k + 1) * 3, k + 1), 100)
    knn = await session.execute(
        text(
            "SELECT knowledge_id FROM knowledge_vec_idx "
            "WHERE embedding MATCH :emb AND k = :k "
            "ORDER BY distance"
        ),
        {"emb": packed, "k": knn_limit},
    )
    candidate_ids = [row[0] for row in knn.all()]
    if exclude_id is not None:
        candidate_ids = [cid for cid in candidate_ids if cid != exclude_id]
    if not candidate_ids:
        return []

    vec_rows = (
        await session.execute(
            select(KnowledgeVec.knowledge_id, KnowledgeVec.vector).where(
                KnowledgeVec.knowledge_id.in_(candidate_ids)
            )
        )
    ).all()
    cosine_by_id: dict[int, float] = {}
    for kid, vec_bytes in vec_rows:
        stored = _unpack(vec_bytes)
        cos_d = _cosine_distance(query_vec, stored)
        cosine_by_id[kid] = 1.0 - cos_d

    stmt = select(Knowledge.id).where(
        Knowledge.id.in_(list(cosine_by_id.keys())),
        Knowledge.status.notin_(["superseded", "archived"]),
    )
    live_ids = {row[0] for row in (await session.execute(stmt)).all()}

    ranked = [
        (kid, cos) for kid, cos in cosine_by_id.items() if kid in live_ids
    ]
    ranked.sort(key=lambda r: r[1], reverse=True)
    return ranked[:k]


async def rebuild_index(session: AsyncSession, *, model_name: str | None = None) -> int:
    """Wipe and repopulate the ANN index from KnowledgeVec.

    ``model_name`` limits the source rows — essential when switching models
    (only the new model's vectors should populate the index). Returns the
    number of rows inserted.
    """
    await session.execute(text("DELETE FROM knowledge_vec_idx"))

    stmt = select(KnowledgeVec.knowledge_id, KnowledgeVec.vector)
    if model_name is not None:
        stmt = stmt.where(KnowledgeVec.model_name == model_name)

    rows = (await session.execute(stmt)).all()
    count = 0
    for knowledge_id, vector_bytes in rows:
        await session.execute(
            text(
                "INSERT INTO knowledge_vec_idx (knowledge_id, embedding) "
                "VALUES (:kid, :emb)"
            ),
            {"kid": knowledge_id, "emb": vector_bytes},
        )
        count += 1
    return count
