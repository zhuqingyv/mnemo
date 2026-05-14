"""FTS5 search and tag-based queries."""

from __future__ import annotations

import json
import re

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mnemo.models.knowledge import Knowledge
from mnemo.utils.tokenizer import tokenize_for_fts


_FTS_SPECIAL = re.compile(r'[\"\':;,.!?()\[\]{}*+\-~^=<>|&/\\]')


def _sanitize_query(query: str, *, max_tokens: int | None = None) -> str:
    """Turn a free-form query into a safe FTS5 MATCH expression.

    1. Run jieba segmentation so Chinese phrases break into the same tokens
       that ``_insert_fts`` produced at index time.
    2. Strip FTS operator characters from every segment.
    3. Quote each remaining token as a literal phrase; tokens combine with
       implicit AND.
    4. If ``max_tokens`` is set, keep only the first N tokens. Used by
       progressive trim in the hybrid search path.
    """
    if not query:
        return ""
    segmented = tokenize_for_fts(query)
    cleaned = _FTS_SPECIAL.sub(" ", segmented)
    tokens = [tok for tok in cleaned.split() if tok]
    if not tokens:
        return ""
    if max_tokens is not None and len(tokens) > max_tokens:
        tokens = tokens[:max_tokens]
    return " ".join(f'"{tok}"' for tok in tokens)


async def fts_search(
    session: AsyncSession,
    query: str,
    *,
    scope: str | None = None,
    project_name: str | None = None,
    limit: int = 20,
    include_superseded: bool = False,
    include_archived: bool = False,
    max_tokens: int | None = None,
) -> list[Knowledge]:
    match_expr = _sanitize_query(query, max_tokens=max_tokens)
    if not match_expr:
        return []

    sql = (
        "SELECT k.id FROM knowledge k "
        "JOIN knowledge_fts f ON f.rowid = k.id "
        "WHERE knowledge_fts MATCH :q"
    )
    params: dict[str, object] = {"q": match_expr, "limit": limit}
    if not include_superseded and not include_archived:
        sql += " AND k.status NOT IN ('superseded', 'archived')"
    elif not include_superseded:
        sql += " AND k.status != 'superseded'"
    elif not include_archived:
        sql += " AND k.status != 'archived'"
    if scope is not None:
        sql += " AND k.scope = :scope"
        params["scope"] = scope
    if project_name is not None:
        sql += " AND k.project_name = :project_name"
        params["project_name"] = project_name
    sql += " ORDER BY bm25(knowledge_fts) LIMIT :limit"

    result = await session.execute(text(sql), params)
    ids = [row[0] for row in result.all()]
    if not ids:
        return []

    stmt = select(Knowledge).where(Knowledge.id.in_(ids))
    rows = (await session.execute(stmt)).scalars().all()
    order = {kid: i for i, kid in enumerate(ids)}
    return sorted(rows, key=lambda k: order.get(k.id, 1 << 30))


async def list_tags(
    session: AsyncSession,
    *,
    scope: str | None = None,
    include_superseded: bool = False,
) -> list[str]:
    stmt = select(Knowledge.tags)
    if not include_superseded:
        stmt = stmt.where(Knowledge.status == "active")
    if scope is not None:
        stmt = stmt.where(Knowledge.scope == scope)
    result = await session.execute(stmt)
    seen: set[str] = set()
    for (raw,) in result.all():
        if not raw:
            continue
        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(items, list):
            continue
        for item in items:
            tag = str(item).strip()
            if tag:
                seen.add(tag)
    return sorted(seen)


async def search_by_tag(
    session: AsyncSession,
    tags: list[str],
    *,
    scope: str | None = None,
    limit: int = 20,
    include_superseded: bool = False,
) -> list[Knowledge]:
    """AND-match tags via the flat knowledge_tag index.

    Pre-M4 this scanned every active Knowledge row and json.loads()'d tags in
    Python — at 500K rows that was ~6.5s per call. The flat index with
    (tag, knowledge_id) composite lets SQLite pick the smallest matching set
    and intersect via GROUP BY … HAVING COUNT(DISTINCT tag) = N.
    """
    required = [t for t in (tags or []) if t]
    if not required:
        return []

    # De-dupe so HAVING COUNT matches the intent even if caller passed dupes.
    unique_tags: list[str] = []
    seen: set[str] = set()
    for t in required:
        if t not in seen:
            seen.add(t)
            unique_tags.append(t)

    placeholders = ", ".join(f":t{i}" for i in range(len(unique_tags)))
    params: dict[str, object] = {
        f"t{i}": tag for i, tag in enumerate(unique_tags)
    }
    params["n"] = len(unique_tags)
    params["limit"] = limit

    sql = (
        f"SELECT kt.knowledge_id FROM knowledge_tag kt "
        f"JOIN knowledge k ON k.id = kt.knowledge_id "
        f"WHERE kt.tag IN ({placeholders})"
    )
    if not include_superseded:
        sql += " AND k.status = 'active'"
    if scope is not None:
        sql += " AND k.scope = :scope"
        params["scope"] = scope
    sql += (
        " GROUP BY kt.knowledge_id "
        "HAVING COUNT(DISTINCT kt.tag) = :n "
        "ORDER BY MAX(k.updated_at) DESC "
        "LIMIT :limit"
    )

    result = await session.execute(text(sql), params)
    ids = [row[0] for row in result.all()]
    if not ids:
        return []

    rows = (
        await session.execute(select(Knowledge).where(Knowledge.id.in_(ids)))
    ).scalars().all()
    order = {kid: i for i, kid in enumerate(ids)}
    return sorted(rows, key=lambda k: order.get(k.id, 1 << 30))
