"""CRUD operations for Knowledge, with FTS5 index kept in sync."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import delete as sql_delete
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mnemo.models.knowledge import Knowledge, KnowledgeTag, Relation
from mnemo.utils.tokenizer import tokenize_for_fts


STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"
SUPERSEDES_RELATION_TYPE = "supersedes"


def _normalize_tags(tags: str | Iterable[str] | None) -> str:
    if tags is None:
        return "[]"
    if isinstance(tags, str):
        stripped = tags.strip()
        if not stripped:
            return "[]"
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = [t.strip() for t in stripped.split(",") if t.strip()]
        if not isinstance(parsed, list):
            parsed = [str(parsed)]
        return json.dumps(parsed, ensure_ascii=False)
    return json.dumps([str(t) for t in tags], ensure_ascii=False)


def _fts_tags(tags_json: str) -> str:
    try:
        items = json.loads(tags_json)
    except json.JSONDecodeError:
        return tags_json
    if isinstance(items, list):
        return " ".join(str(t) for t in items)
    return str(items)


def compute_content_hash(content: str) -> str:
    """SHA256 hex digest of the content string (UTF-8 encoded)."""
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


async def _insert_fts(
    session: AsyncSession,
    knowledge_id: int,
    title: str,
    summary: str,
    content: str,
    tags_json: str,
) -> None:
    await session.execute(
        text(
            "INSERT INTO knowledge_fts (rowid, title, summary, content, tags, knowledge_id) "
            "VALUES (:rowid, :title, :summary, :content, :tags, :knowledge_id)"
        ),
        {
            "rowid": knowledge_id,
            "title": tokenize_for_fts(title),
            "summary": tokenize_for_fts(summary),
            "content": tokenize_for_fts(content),
            "tags": tokenize_for_fts(_fts_tags(tags_json)),
            "knowledge_id": knowledge_id,
        },
    )


async def _delete_fts(session: AsyncSession, knowledge_id: int) -> None:
    await session.execute(
        text("DELETE FROM knowledge_fts WHERE rowid = :rowid"),
        {"rowid": knowledge_id},
    )


def _parse_tags(tags_json: str) -> list[str]:
    try:
        parsed = json.loads(tags_json) if tags_json else []
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in parsed:
        tag = str(item).strip()
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out


async def _sync_tag_index(
    session: AsyncSession,
    knowledge_id: int,
    tags_json: str,
) -> None:
    """Replace knowledge_tag rows for *knowledge_id* with the parsed tags.

    Called on every create / supersede / update path so the flat tag index
    stays consistent with the JSON column. Dedupe is applied before write.
    """
    await session.execute(
        sql_delete(KnowledgeTag).where(KnowledgeTag.knowledge_id == knowledge_id)
    )
    for tag in _parse_tags(tags_json):
        session.add(KnowledgeTag(knowledge_id=knowledge_id, tag=tag))


async def create(
    session: AsyncSession,
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
    version: int = 1,
    extra_json: str | None = None,
) -> Knowledge:
    tags_json = _normalize_tags(tags)
    row = Knowledge(
        title=title,
        tags=tags_json,
        summary=summary,
        content=content,
        scope=scope,
        project_name=project_name,
        session_id=session_id,
        source=source,
        claim_type=claim_type,
        status=status,
        content_hash=compute_content_hash(content),
        version=version,
        extra_json=extra_json,
    )
    session.add(row)
    await session.flush()
    await _insert_fts(session, row.id, title, summary, content, tags_json)
    await _sync_tag_index(session, row.id, tags_json)
    await session.commit()
    await session.refresh(row)
    return row


async def get_by_id(session: AsyncSession, knowledge_id: int) -> Knowledge | None:
    return await session.get(Knowledge, knowledge_id)


async def get_by_title(
    session: AsyncSession,
    title: str,
    *,
    scope: str | None = None,
    project_name: str | None = None,
    include_superseded: bool = False,
) -> Knowledge | None:
    stmt = select(Knowledge).where(Knowledge.title == title)
    if not include_superseded:
        stmt = stmt.where(Knowledge.status == STATUS_ACTIVE)
    if scope is not None:
        stmt = stmt.where(Knowledge.scope == scope)
    if project_name is not None:
        stmt = stmt.where(Knowledge.project_name == project_name)
    stmt = stmt.order_by(Knowledge.version.desc())
    result = await session.execute(stmt.limit(1))
    return result.scalar_one_or_none()


async def find_duplicate_by_hash(
    session: AsyncSession,
    content_hash: str,
    *,
    exclude_id: int | None = None,
) -> Knowledge | None:
    """Return the first active row with a matching content hash, if any."""
    if not content_hash:
        return None
    stmt = (
        select(Knowledge)
        .where(Knowledge.content_hash == content_hash)
        .where(Knowledge.status == STATUS_ACTIVE)
    )
    if exclude_id is not None:
        stmt = stmt.where(Knowledge.id != exclude_id)
    result = await session.execute(stmt.limit(1))
    return result.scalar_one_or_none()


async def update(session: AsyncSession, knowledge_id: int, **fields: Any) -> Knowledge:
    row = await session.get(Knowledge, knowledge_id)
    if row is None:
        raise ValueError(f"Knowledge id={knowledge_id} not found")

    if "tags" in fields:
        fields["tags"] = _normalize_tags(fields["tags"])

    allowed = {
        "title",
        "tags",
        "summary",
        "content",
        "scope",
        "project_name",
        "session_id",
        "source",
        "claim_type",
        "status",
        "extra_json",
        "version",
    }
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"Cannot update field {key!r}")
        setattr(row, key, value)

    if "content" in fields:
        row.content_hash = compute_content_hash(row.content)

    await session.flush()
    await _delete_fts(session, row.id)
    await _insert_fts(session, row.id, row.title, row.summary, row.content, row.tags)
    if "tags" in fields:
        await _sync_tag_index(session, row.id, row.tags)
    await session.commit()
    await session.refresh(row)
    return row


async def delete(session: AsyncSession, knowledge_id: int) -> bool:
    row = await session.get(Knowledge, knowledge_id)
    if row is None:
        return False

    await session.execute(
        sql_delete(Relation).where(
            (Relation.source_id == knowledge_id) | (Relation.target_id == knowledge_id)
        )
    )
    await session.execute(
        sql_delete(KnowledgeTag).where(KnowledgeTag.knowledge_id == knowledge_id)
    )
    await _delete_fts(session, knowledge_id)
    await session.delete(row)
    await session.commit()
    return True


async def list_titles_by_scope(
    session: AsyncSession,
    scope: str,
    project_name: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Return active ``{id, title}`` rows inside *scope* for Write-gate L1.

    Used as a small candidate pool (≤ limit) to run Python-side Levenshtein /
    Jaccard against. At O(50) rows the scan is <10ms so there's no need for an
    FTS-style prefilter here — callers own the scoring pass.
    """
    stmt = (
        select(Knowledge.id, Knowledge.title)
        .where(Knowledge.status == STATUS_ACTIVE)
        .where(Knowledge.scope == scope)
    )
    if project_name is not None:
        stmt = stmt.where(Knowledge.project_name == project_name)
    stmt = stmt.order_by(Knowledge.updated_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return [{"id": kid, "title": title} for kid, title in result.all()]


async def batch_updated_at_and_claim_type(
    session: AsyncSession,
    knowledge_ids: Iterable[int],
) -> dict[int, tuple[datetime, str | None]]:
    """Batch lookup of ``updated_at`` + ``claim_type`` for the given ids.

    Feeds the rerank freshness multiplier (P3a-M2). Missing ids are omitted
    so callers can default them to a neutral multiplier.
    """
    ids = list(knowledge_ids)
    if not ids:
        return {}
    stmt = select(Knowledge.id, Knowledge.updated_at, Knowledge.claim_type).where(
        Knowledge.id.in_(ids)
    )
    result = await session.execute(stmt)
    return {kid: (updated_at, claim_type) for kid, updated_at, claim_type in result.all()}


async def batch_lifecycle_fields(
    session: AsyncSession,
    knowledge_ids: Iterable[int],
) -> dict[int, dict[str, Any]]:
    """Batch lookup of all lifecycle-relevant fields for rerank / stale check.

    Returns ``{id: {"updated_at", "claim_type", "status", "last_accessed_at"}}``.
    Combines P3a-M2 freshness inputs with the P3a-M3 stale-transition inputs
    so ``_quality_rerank`` does one roundtrip per search instead of two.
    """
    ids = list(knowledge_ids)
    if not ids:
        return {}
    stmt = select(
        Knowledge.id,
        Knowledge.updated_at,
        Knowledge.claim_type,
        Knowledge.status,
        Knowledge.last_accessed_at,
    ).where(Knowledge.id.in_(ids))
    result = await session.execute(stmt)
    return {
        kid: {
            "updated_at": updated_at,
            "claim_type": claim_type,
            "status": status,
            "last_accessed_at": last_accessed_at,
        }
        for kid, updated_at, claim_type, status, last_accessed_at in result.all()
    }


async def list_all(
    session: AsyncSession,
    *,
    scope: str | None = None,
    project_name: str | None = None,
    limit: int = 50,
    offset: int = 0,
    include_superseded: bool = False,
) -> list[Knowledge]:
    stmt = select(Knowledge).order_by(Knowledge.updated_at.desc())
    if not include_superseded:
        stmt = stmt.where(Knowledge.status == STATUS_ACTIVE)
    if scope is not None:
        stmt = stmt.where(Knowledge.scope == scope)
    if project_name is not None:
        stmt = stmt.where(Knowledge.project_name == project_name)
    stmt = stmt.limit(limit).offset(offset)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def supersede(
    session: AsyncSession,
    old_id: int,
    **fields: Any,
) -> tuple[Knowledge, Knowledge]:
    """Create a new immutable version of the knowledge at *old_id*.

    The old row is flipped to status=superseded and its FTS entry is dropped.
    A new row is inserted that inherits every field from the old one, overlaid
    with *fields*. A supersedes relation (new -> old) is created so version
    chains can be walked backwards.

    Returns ``(old, new)``.
    """
    old = await session.get(Knowledge, old_id)
    if old is None:
        raise ValueError(f"Knowledge id={old_id} not found")

    if "tags" in fields:
        fields["tags"] = _normalize_tags(fields["tags"])

    allowed = {
        "title",
        "tags",
        "summary",
        "content",
        "scope",
        "project_name",
        "session_id",
        "source",
        "claim_type",
        "extra_json",
    }
    for key in fields:
        if key not in allowed:
            raise ValueError(f"Cannot update field {key!r}")

    new_title = fields.get("title", old.title)
    new_tags_json = fields.get("tags", old.tags)
    new_summary = fields.get("summary", old.summary)
    new_content = fields.get("content", old.content)
    new_scope = fields.get("scope", old.scope)
    new_project = fields.get("project_name", old.project_name)
    new_session = fields.get("session_id", old.session_id)
    new_source = fields.get("source", old.source)
    new_claim_type = fields.get("claim_type", old.claim_type)
    new_extra = fields.get("extra_json", old.extra_json)

    old.status = STATUS_SUPERSEDED
    await _delete_fts(session, old.id)

    new_row = Knowledge(
        title=new_title,
        tags=new_tags_json,
        summary=new_summary,
        content=new_content,
        scope=new_scope,
        project_name=new_project,
        session_id=new_session,
        source=new_source,
        claim_type=new_claim_type,
        status=STATUS_ACTIVE,
        content_hash=compute_content_hash(new_content),
        version=(old.version or 1) + 1,
        extra_json=new_extra,
    )
    session.add(new_row)
    await session.flush()
    await _insert_fts(
        session,
        new_row.id,
        new_title,
        new_summary,
        new_content,
        new_tags_json,
    )
    await _sync_tag_index(session, new_row.id, new_tags_json)

    link = Relation(
        source_id=new_row.id,
        target_id=old.id,
        relation_type=SUPERSEDES_RELATION_TYPE,
    )
    session.add(link)

    await session.commit()
    await session.refresh(old)
    await session.refresh(new_row)
    return old, new_row
