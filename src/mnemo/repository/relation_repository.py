"""CRUD and traversal for knowledge relations."""

from __future__ import annotations

from sqlalchemy import delete as sql_delete
from sqlalchemy import or_, select, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from mnemo.models.knowledge import Knowledge, Relation


AUTO_RELATED_TYPE = "auto_related"


async def create(
    session: AsyncSession,
    *,
    source_id: int,
    target_id: int,
    relation_type: str = "related",
    weight: float = 1.0,
    extra_json: str | None = None,
) -> Relation:
    if source_id == target_id:
        raise ValueError("Relation cannot point to itself")

    row = Relation(
        source_id=source_id,
        target_id=target_id,
        relation_type=relation_type,
        weight=weight,
        extra_json=extra_json,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def exists_edge(
    session: AsyncSession,
    *,
    source_id: int,
    target_id: int,
    relation_type: str | None = None,
    undirected: bool = False,
) -> bool:
    """Check whether an edge already exists between two nodes.

    When ``undirected`` is True the check also accepts the reverse direction —
    auto-link uses this to avoid building a second edge when a manual/wikilink
    edge already connects the pair from the other side.
    """
    conds = [Relation.source_id == source_id, Relation.target_id == target_id]
    stmt = select(Relation.id).where(*conds)
    if relation_type is not None:
        stmt = stmt.where(Relation.relation_type == relation_type)
    first = (await session.execute(stmt)).first()
    if first is not None:
        return True
    if not undirected:
        return False
    rev = select(Relation.id).where(
        Relation.source_id == target_id,
        Relation.target_id == source_id,
    )
    if relation_type is not None:
        rev = rev.where(Relation.relation_type == relation_type)
    return (await session.execute(rev)).first() is not None


async def _neighbor_ids(session: AsyncSession, knowledge_id: int) -> set[int]:
    stmt = select(Relation.source_id, Relation.target_id).where(
        or_(Relation.source_id == knowledge_id, Relation.target_id == knowledge_id)
    )
    result = await session.execute(stmt)
    neighbors: set[int] = set()
    for src, tgt in result.all():
        if src != knowledge_id:
            neighbors.add(src)
        if tgt != knowledge_id:
            neighbors.add(tgt)
    return neighbors


async def get_related(
    session: AsyncSession,
    knowledge_id: int,
    depth: int = 1,
    *,
    include_superseded: bool = False,
) -> list[Knowledge]:
    if depth < 1:
        return []

    visited: set[int] = {knowledge_id}
    frontier: set[int] = {knowledge_id}
    collected: set[int] = set()

    for _ in range(depth):
        next_frontier: set[int] = set()
        for node in frontier:
            neighbors = await _neighbor_ids(session, node)
            for nb in neighbors:
                if nb not in visited:
                    next_frontier.add(nb)
                    collected.add(nb)
                    visited.add(nb)
        if not next_frontier:
            break
        frontier = next_frontier

    if not collected:
        return []

    stmt = select(Knowledge).where(Knowledge.id.in_(collected))
    if not include_superseded:
        stmt = stmt.where(Knowledge.status == "active")
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_backlinks(session: AsyncSession, knowledge_id: int) -> list[Knowledge]:
    stmt = (
        select(Knowledge)
        .join(Relation, Relation.source_id == Knowledge.id)
        .where(Relation.target_id == knowledge_id)
        .distinct()
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def delete_by_knowledge(session: AsyncSession, knowledge_id: int) -> int:
    stmt = sql_delete(Relation).where(
        or_(Relation.source_id == knowledge_id, Relation.target_id == knowledge_id)
    )
    result = await session.execute(stmt)
    await session.commit()
    return int(result.rowcount or 0)


async def find_successor(
    session: AsyncSession,
    superseded_id: int,
) -> Knowledge | None:
    """Given a superseded row, return the row that superseded it.

    Walks the supersedes edge: new -> (supersedes) -> old.
    """
    stmt = (
        select(Knowledge)
        .join(Relation, Relation.source_id == Knowledge.id)
        .where(Relation.target_id == superseded_id)
        .where(Relation.relation_type == "supersedes")
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_outgoing_targets_by_type(
    session: AsyncSession,
    source_id: int,
    relation_type: str,
) -> list[str]:
    """Return titles of every target reached by an outgoing relation of type."""
    stmt = (
        select(Knowledge.title)
        .join(Relation, Relation.target_id == Knowledge.id)
        .where(Relation.source_id == source_id)
        .where(Relation.relation_type == relation_type)
    )
    result = await session.execute(stmt)
    return [row[0] for row in result.all()]


async def get_contradiction_pairs(
    session: AsyncSession,
    ids: list[int],
) -> list[dict]:
    """Return every ``contradicts`` edge touching any id in *ids*.

    Used by search to attach per-result ``conflicts_with`` lists. One edge is
    returned per row regardless of direction — the caller decides which end
    is the "other side" for a given result id.
    """
    if not ids:
        return []
    stmt = select(Relation.id, Relation.source_id, Relation.target_id).where(
        Relation.relation_type == "contradicts",
        or_(Relation.source_id.in_(ids), Relation.target_id.in_(ids)),
    )
    result = await session.execute(stmt)
    return [
        {"relation_id": rid, "source_id": src, "target_id": tgt}
        for rid, src, tgt in result.all()
    ]


async def delete_outgoing_by_type(
    session: AsyncSession,
    source_id: int,
    relation_type: str,
) -> int:
    """Delete all outgoing relations of a given type from *source_id*.

    Used by the service layer to refresh auto-derived links (e.g. wikilinks)
    without touching user-authored relations of other types.
    """
    stmt = sql_delete(Relation).where(
        Relation.source_id == source_id,
        Relation.relation_type == relation_type,
    )
    result = await session.execute(stmt)
    await session.commit()
    return int(result.rowcount or 0)


async def get_auto_edges_touching(
    session: AsyncSession,
    knowledge_id: int,
) -> list[Relation]:
    """Return every ``auto_related`` edge with *knowledge_id* as source or
    target.

    Used by the feedback-propagation hook (Phase 5b M2): one feedback signal
    on a knowledge node lifts/lowers the weight of every auto edge that
    touches it. Manual ``related`` / ``wikilink`` / ``supersedes`` /
    ``contradicts`` edges are intentionally excluded — those carry agent-
    declared semantics that feedback must not overwrite.
    """
    stmt = select(Relation).where(
        Relation.relation_type == AUTO_RELATED_TYPE,
        or_(Relation.source_id == knowledge_id, Relation.target_id == knowledge_id),
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def update_weight(
    session: AsyncSession,
    relation_id: int,
    *,
    new_weight: float,
    extra_json: str | None,
) -> None:
    """Persist a new ``weight`` + ``extra_json`` on a single relation row.

    Caller owns the surrounding transaction semantics (we commit here because
    the feedback hook is invoked outside the feedback_service session and
    expects writes to be durable on return). Passing ``extra_json=None``
    clears the column.
    """
    stmt = (
        sql_update(Relation)
        .where(Relation.id == relation_id)
        .values(weight=new_weight, extra_json=extra_json)
    )
    await session.execute(stmt)
    await session.commit()
