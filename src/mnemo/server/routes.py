"""REST API router — thin HTTP adapter over ``KnowledgeService``.

Mirrors the 11 MCP tools (see ``mnemo.mcp.server``) as JSON endpoints so web
UI / CLI-over-HTTP / third-party callers can hit the same backend the MCP
transport uses. Endpoints intentionally do *not* wear the ``monitor_tool``
decorator — monitoring is collected at the MCP tool layer, and double-wiring
would duplicate events for agents that hit both surfaces.

Request / response shapes stay close to the service layer's dict output
(``_to_dict`` / ``_summary_dict``) so the REST contract tracks the service
contract with minimal translation.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from mnemo.config import MnemoConfig
from mnemo.models.knowledge import Knowledge, KnowledgeEvent, Relation
from mnemo.monitor.models import MonitorEvent
from mnemo.services import feedback_service
from mnemo.services.knowledge_service import KnowledgeService


router = APIRouter(tags=["knowledge"])


# ---------------------------------------------------------------------------
# Lightweight in-memory TTL cache for high-frequency viz polling endpoints.
# The viz dashboard polls /stats, /knowledge, /relations every few seconds
# from potentially many browser tabs. Without caching, each request fires
# multiple SQL aggregation queries against SQLite, pinning the CPU at ~100%.
# A 2-second TTL is short enough for "live" feel but collapses N concurrent
# identical requests into 1 real DB round-trip.
# ---------------------------------------------------------------------------

class _ResponseCache:
    """Simple single-slot TTL cache keyed by (endpoint, params) tuple."""

    __slots__ = ("_store",)

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str, ttl: float = 2.0) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, value = entry
        if time.monotonic() - ts > ttl:
            return None
        return value

    def put(self, key: str, value: Any) -> None:
        self._store[key] = (time.monotonic(), value)

    def invalidate(self) -> None:
        """Drop all cached entries — called after any write operation."""
        self._store.clear()


_cache = _ResponseCache()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _get_service(request: Request) -> KnowledgeService:
    """Pull the shared KnowledgeService out of app.state (set in lifespan)."""
    service = getattr(request.app.state, "service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="knowledge service not initialized",
        )
    return service


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class CreateKnowledgeRequest(BaseModel):
    title: str
    summary: str
    content: str
    tags: list[str] = Field(default_factory=list)
    scope: str = "global"
    project_name: str | None = None
    source: str | None = None
    claim_type: str | None = None
    related: list[str] = Field(default_factory=list)


class UpdateKnowledgeRequest(BaseModel):
    title: str | None = None
    summary: str | None = None
    content: str | None = None
    tags: list[str] | None = None


class ArchiveRequest(BaseModel):
    reason: str | None = None


class FeedbackRequest(BaseModel):
    signal: str
    reason: str | None = None
    actor: str = "agent:unknown"


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------


@router.post("/knowledge", status_code=status.HTTP_201_CREATED)
async def create_knowledge(
    payload: CreateKnowledgeRequest, request: Request
) -> dict[str, Any]:
    service = _get_service(request)
    result = await service.create_knowledge(
        title=payload.title,
        summary=payload.summary,
        content=payload.content,
        tags=payload.tags,
        scope=payload.scope,
        project_name=payload.project_name,
        source=payload.source,
        claim_type=payload.claim_type,
        related_titles=payload.related,
    )
    _cache.invalidate()
    return result


@router.get("/knowledge")
async def list_knowledge(
    request: Request,
    scope: str | None = None,
    project_name: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List knowledge entries (summary dicts) for UI browsing.

    Thin wrapper over ``KnowledgeService.list_knowledge``; used by the
    viz dashboard to populate the grid without needing a query term.
    Results are cached for 2 seconds to collapse concurrent viz polling.
    """
    cache_key = f"knowledge:{scope}:{project_name}:{limit}:{offset}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    service = _get_service(request)
    rows = await service.list_knowledge(
        scope=scope,
        project_name=project_name,
        limit=limit,
        offset=offset,
    )
    result = {"count": len(rows), "results": rows}
    _cache.put(cache_key, result)
    return result


@router.get("/relations")
async def list_relations(
    request: Request,
    limit: int = Query(1000, ge=1, le=5000),
) -> dict[str, Any]:
    """List relations for graph visualization.

    Viz needs edge-level detail (``relation_type`` per edge) that
    ``/knowledge/{id}/related`` flattens away. One batch fetch is cheaper than
    N+1 calls when the frontend renders a 500-node graph.
    Results are cached for 2 seconds to collapse concurrent viz polling.
    """
    cache_key = f"relations:{limit}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    service = _get_service(request)
    async with service._session_factory() as session:
        # The viz node list is ordered by recently updated knowledge. Once a
        # database has more relations than this endpoint's cap, SQLite's
        # natural order returns the oldest edges first, which often leaves the
        # current node window with no matching links. Prefer recent relation
        # rows so graph edges stay aligned with the default knowledge window.
        stmt = select(Relation).order_by(Relation.id.desc()).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()
        result = {
            "count": len(rows),
            "results": [
                {
                    "id": r.id,
                    "source_id": r.source_id,
                    "target_id": r.target_id,
                    "relation_type": r.relation_type,
                    "weight": r.weight,
                }
                for r in rows
            ],
        }
    _cache.put(cache_key, result)
    return result


@router.get("/stats")
async def get_stats(request: Request) -> dict[str, Any]:
    """Aggregate counts for the knowledge-viz dashboard.

    Single round-trip view of:
    - knowledge status distribution
    - feedback signal distribution (helpful / misleading / outdated)
    - tool-usage counts (all-time, from monitor_event)
    - contradicts-relation count

    Results are cached for 2 seconds to collapse concurrent polling from
    multiple viz tabs into a single DB round-trip.
    """
    cached = _cache.get("stats")
    if cached is not None:
        return cached

    service = _get_service(request)
    async with service._session_factory() as session:
        status_rows = (
            await session.execute(
                select(Knowledge.status, func.count(Knowledge.id)).group_by(
                    Knowledge.status
                )
            )
        ).all()
        knowledge_by_status = {row[0]: int(row[1]) for row in status_rows}

        relation_rows = (
            await session.execute(
                select(Relation.relation_type, func.count(Relation.id)).group_by(
                    Relation.relation_type
                )
            )
        ).all()
        relations_by_type = {row[0]: int(row[1]) for row in relation_rows}

        tool_rows = (
            await session.execute(
                select(MonitorEvent.tool_name, func.count(MonitorEvent.id)).group_by(
                    MonitorEvent.tool_name
                )
            )
        ).all()
        tool_calls = {row[0]: int(row[1]) for row in tool_rows}

        # feedback signal is stored inside knowledge_event.payload_json; the
        # feedback_service writes event_type='feedback' rows. JSON extract keeps
        # this single-query and avoids pulling every payload into Python.
        feedback_rows = (
            await session.execute(
                select(
                    func.json_extract(KnowledgeEvent.payload_json, "$.signal"),
                    func.count(KnowledgeEvent.id),
                )
                .where(KnowledgeEvent.event_type == "feedback")
                .group_by(func.json_extract(KnowledgeEvent.payload_json, "$.signal"))
            )
        ).all()
        feedback_by_signal = {
            (row[0] or "unknown"): int(row[1]) for row in feedback_rows
        }

        # task tracking — see docs/phase5/TASK_TRACKING_DESIGN.md §7.
        # dispatched = task_dispatched events (may pre-date any completion).
        # completed  = feedback/task_completed events whose payload.trigger_source
        #              is "search_dispatch" (i.e. closing a dispatched task).
        # initiative = agent-initiated reinforcement: feedback/task_completed with
        #              trigger_source="agent_initiative" OR absent (legacy data).
        dispatched = (
            await session.execute(
                select(func.count(KnowledgeEvent.id)).where(
                    KnowledgeEvent.event_type == "task_dispatched"
                )
            )
        ).scalar() or 0
        completed = (
            await session.execute(
                select(func.count(KnowledgeEvent.id))
                .where(KnowledgeEvent.event_type.in_(["feedback", "task_completed"]))
                .where(
                    func.json_extract(KnowledgeEvent.payload_json, "$.trigger_source")
                    == "search_dispatch"
                )
            )
        ).scalar() or 0
        initiative = (
            await session.execute(
                select(func.count(KnowledgeEvent.id))
                .where(KnowledgeEvent.event_type.in_(["feedback", "task_completed"]))
                .where(
                    (
                        func.json_extract(
                            KnowledgeEvent.payload_json, "$.trigger_source"
                        )
                        == "agent_initiative"
                    )
                    | (
                        func.json_extract(
                            KnowledgeEvent.payload_json, "$.trigger_source"
                        ).is_(None)
                    )
                )
            )
        ).scalar() or 0

    total_knowledge = sum(knowledge_by_status.values())
    total_feedback = sum(feedback_by_signal.values())
    completion_rate = round(completed / dispatched, 3) if dispatched > 0 else 0.0
    result = {
        "knowledge": {
            "total": total_knowledge,
            "by_status": knowledge_by_status,
        },
        "feedback": {
            "total": total_feedback,
            "by_signal": feedback_by_signal,
        },
        "tool_calls": tool_calls,
        "relations": {
            "by_type": relations_by_type,
            "contradictions": relations_by_type.get("contradicts", 0),
        },
        "tasks": {
            "dispatched": int(dispatched),
            "completed": int(completed),
            "completion_rate": completion_rate,
            "initiative_count": int(initiative),
        },
    }
    _cache.put("stats", result)
    return result


@router.get("/events/recent")
async def recent_events(
    request: Request,
    tool: str = Query("search"),
    seconds: int = Query(5, ge=1, le=60),
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """Return recent monitor_event rows — used by the live viz to pulse nodes
    when agents in other processes hit mnemo. ``seconds`` is the tail window,
    computed as ``now(UTC) - seconds`` to line up with the naive-UTC timestamps
    the collector writes via ``datetime.now(timezone.utc)``.
    """
    service = _get_service(request)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        seconds=seconds
    )
    async with service._session_factory() as session:
        stmt = (
            select(MonitorEvent)
            .where(MonitorEvent.tool_name == tool)
            .where(MonitorEvent.created_at >= cutoff)
            .order_by(MonitorEvent.created_at.desc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).scalars().all()
        return {
            "tool": tool,
            "seconds": seconds,
            "count": len(rows),
            "results": [
                {
                    "id": r.id,
                    "tool_name": r.tool_name,
                    "params_json": r.params_json,
                    "result_summary": r.result_summary,
                    "result_meta": r.result_meta,
                    "actor": r.actor,
                    "status": r.status,
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ],
        }


@router.get("/timeline")
async def get_timeline(
    request: Request,
    since: str | None = Query(
        None, description="ISO datetime, only events/nodes after this time"
    ),
    until: str | None = Query(
        None, description="ISO datetime, only events/nodes before this time"
    ),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Unified timeline of knowledge_event + monitor_event, chronologically
    ordered. Also returns node snapshots (knowledge rows) in the same window so
    the viz timeline player can know when each node first appeared.
    """
    service = _get_service(request)

    since_dt: datetime | None = None
    until_dt: datetime | None = None
    try:
        if since:
            since_dt = datetime.fromisoformat(since)
        if until:
            until_dt = datetime.fromisoformat(until)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid iso datetime: {e}") from e

    # Stored timestamps are naive UTC (see collector); strip tzinfo to compare.
    if since_dt is not None and since_dt.tzinfo is not None:
        since_dt = since_dt.astimezone(timezone.utc).replace(tzinfo=None)
    if until_dt is not None and until_dt.tzinfo is not None:
        until_dt = until_dt.astimezone(timezone.utc).replace(tzinfo=None)

    async with service._session_factory() as session:
        ke_stmt = select(
            KnowledgeEvent.id,
            KnowledgeEvent.knowledge_id,
            KnowledgeEvent.event_type,
            KnowledgeEvent.actor,
            KnowledgeEvent.payload_json,
            KnowledgeEvent.created_at,
        )
        me_stmt = select(
            MonitorEvent.id,
            MonitorEvent.tool_name,
            MonitorEvent.params_json,
            MonitorEvent.result_summary,
            MonitorEvent.actor,
            MonitorEvent.created_at,
            MonitorEvent.latency_ms,
            MonitorEvent.status,
        )
        k_stmt = select(
            Knowledge.id,
            Knowledge.title,
            Knowledge.status,
            Knowledge.scope,
            Knowledge.project_name,
            Knowledge.created_at,
            Knowledge.updated_at,
        )

        if since_dt is not None:
            ke_stmt = ke_stmt.where(KnowledgeEvent.created_at >= since_dt)
            me_stmt = me_stmt.where(MonitorEvent.created_at >= since_dt)
            k_stmt = k_stmt.where(Knowledge.created_at >= since_dt)
        if until_dt is not None:
            ke_stmt = ke_stmt.where(KnowledgeEvent.created_at <= until_dt)
            me_stmt = me_stmt.where(MonitorEvent.created_at <= until_dt)
            k_stmt = k_stmt.where(Knowledge.created_at <= until_dt)

        ke_rows = (await session.execute(ke_stmt)).all()
        me_rows = (await session.execute(me_stmt)).all()
        k_rows = (await session.execute(k_stmt.order_by(Knowledge.created_at))).all()

    events: list[dict[str, Any]] = []
    for r in ke_rows:
        events.append(
            {
                "source": "knowledge_event",
                "id": r.id,
                "knowledge_id": r.knowledge_id,
                "event_type": r.event_type,
                "actor": r.actor,
                "payload": r.payload_json,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )
    for r in me_rows:
        events.append(
            {
                "source": "monitor_event",
                "id": r.id,
                "tool_name": r.tool_name,
                "params": r.params_json,
                "result_summary": r.result_summary,
                "actor": r.actor,
                "latency_ms": r.latency_ms,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )

    events.sort(key=lambda e: e["created_at"] or "")
    total = len(events)
    events = events[offset : offset + limit]

    nodes = [
        {
            "id": r.id,
            "title": r.title,
            "status": r.status,
            "scope": r.scope,
            "project_name": r.project_name,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in k_rows
    ]

    return {
        "total_events": total,
        "offset": offset,
        "limit": limit,
        "events": events,
        "nodes": nodes,
    }


@router.get("/knowledge/search")
async def search_knowledge(
    request: Request,
    query: str = Query(..., min_length=1),
    scope: str | None = None,
    project_name: str | None = None,
    limit: int = Query(20, ge=1, le=200),
    include_archived: bool = False,
    task_context: str | None = None,
    mode: str = "hybrid",
    sort_by: str = "relevance",
) -> dict[str, Any]:
    service = _get_service(request)
    try:
        hits = await service.search(
            query,
            scope=scope,
            project_name=project_name,
            limit=limit,
            mode=mode,
            include_archived=include_archived,
            task_context=task_context,
            sort_by=sort_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"query": query, "count": len(hits), "results": hits}


@router.get("/knowledge/{knowledge_id}")
async def get_knowledge(knowledge_id: int, request: Request) -> dict[str, Any]:
    service = _get_service(request)
    result = await service.get_knowledge(knowledge_id)
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"knowledge id={knowledge_id} not found"
        )
    return result


@router.get("/knowledge/{knowledge_id}/detail")
async def get_knowledge_detail(knowledge_id: int, request: Request) -> dict[str, Any]:
    """Aggregated detail: knowledge + feedback counts + relations.

    Single round-trip for the viz detail panel so it doesn't need 3 fetches.
    """
    from mnemo.repository import feedback_repository as fr

    service = _get_service(request)
    result = await service.get_knowledge(knowledge_id)
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"knowledge id={knowledge_id} not found"
        )

    async with service._session_factory() as session:
        counts = await fr.batch_feedback_counts(session, [knowledge_id])
        h, m = counts.get(knowledge_id, (0, 0))
        outdated_row = (
            await session.execute(
                select(func.count(KnowledgeEvent.id))
                .where(KnowledgeEvent.knowledge_id == knowledge_id)
                .where(KnowledgeEvent.event_type == "feedback")
                .where(
                    func.json_extract(KnowledgeEvent.payload_json, "$.signal")
                    == "outdated"
                )
            )
        ).scalar() or 0

        rel_stmt = select(Relation).where(
            (Relation.source_id == knowledge_id)
            | (Relation.target_id == knowledge_id)
        )
        rels = (await session.execute(rel_stmt)).scalars().all()

        peer_ids = set()
        for r in rels:
            peer_ids.add(r.source_id if r.source_id != knowledge_id else r.target_id)

        peer_titles: dict[int, str] = {}
        if peer_ids:
            title_rows = (
                await session.execute(
                    select(Knowledge.id, Knowledge.title).where(
                        Knowledge.id.in_(peer_ids)
                    )
                )
            ).all()
            peer_titles = {row[0]: row[1] for row in title_rows}

        last_accessed = (
            await session.execute(
                select(Knowledge.last_accessed_at).where(
                    Knowledge.id == knowledge_id
                )
            )
        ).scalar()

    result["feedback"] = {
        "helpful": h,
        "misleading": m,
        "outdated": outdated_row,
        "total": h + m + outdated_row,
    }
    result["relations"] = [
        {
            "id": r.id,
            "source_id": r.source_id,
            "target_id": r.target_id,
            "relation_type": r.relation_type,
            "weight": round(r.weight, 3) if r.weight else r.weight,
            "peer_id": r.target_id if r.source_id == knowledge_id else r.source_id,
            "peer_title": peer_titles.get(
                r.target_id if r.source_id == knowledge_id else r.source_id, ""
            ),
            "direction": "outgoing" if r.source_id == knowledge_id else "incoming",
        }
        for r in rels
    ]
    if last_accessed:
        result["last_accessed_at"] = last_accessed.isoformat()
    return result


@router.patch("/knowledge/{knowledge_id}")
async def update_knowledge(
    knowledge_id: int,
    payload: UpdateKnowledgeRequest,
    request: Request,
) -> dict[str, Any]:
    service = _get_service(request)
    fields: dict[str, Any] = {}
    if payload.title is not None:
        fields["title"] = payload.title
    if payload.summary is not None:
        fields["summary"] = payload.summary
    if payload.content is not None:
        fields["content"] = payload.content
    if payload.tags is not None:
        fields["tags"] = payload.tags
    if not fields:
        raise HTTPException(status_code=400, detail="no fields to update")
    try:
        result = await service.update_knowledge(knowledge_id, **fields)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    _cache.invalidate()
    return result


@router.delete("/knowledge/{knowledge_id}")
async def delete_knowledge(knowledge_id: int, request: Request) -> dict[str, Any]:
    service = _get_service(request)
    deleted = await service.delete_knowledge(knowledge_id)
    if not deleted:
        raise HTTPException(
            status_code=404, detail=f"knowledge id={knowledge_id} not found"
        )
    _cache.invalidate()
    return {"success": True, "id": knowledge_id}


@router.post("/knowledge/{knowledge_id}/archive")
async def archive_knowledge(
    knowledge_id: int,
    request: Request,
    payload: ArchiveRequest | None = None,
) -> dict[str, Any]:
    service = _get_service(request)
    cfg = getattr(service, "_config", None)
    if cfg is not None and not getattr(cfg, "state_machine_enabled", True):
        raise HTTPException(status_code=400, detail="feature_disabled")
    reason = payload.reason if payload else None
    result = await service.archive_knowledge(knowledge_id, reason=reason)
    if not result.get("success"):
        err = result.get("reason") or result.get("error") or "archive failed"
        code = 404 if err in {"not_found", "missing_id"} else 400
        raise HTTPException(status_code=code, detail=err)
    _cache.invalidate()
    return result


@router.post("/knowledge/{knowledge_id}/unarchive")
async def unarchive_knowledge(
    knowledge_id: int, request: Request
) -> dict[str, Any]:
    service = _get_service(request)
    result = await service.unarchive_knowledge(knowledge_id)
    if not result.get("success"):
        err = result.get("reason") or result.get("error") or "unarchive failed"
        code = 404 if err in {"not_found"} else 400
        raise HTTPException(status_code=code, detail=err)
    _cache.invalidate()
    return result


@router.post("/knowledge/{knowledge_id}/feedback")
async def post_feedback(
    knowledge_id: int,
    payload: FeedbackRequest,
    request: Request,
) -> dict[str, Any]:
    service = _get_service(request)
    cfg = getattr(service, "_config", None) or MnemoConfig()
    if not getattr(cfg, "feedback_loop_enabled", True):
        raise HTTPException(status_code=400, detail="feature_disabled")
    try:
        result = await feedback_service.record_feedback(
            service,
            knowledge_id=knowledge_id,
            signal=payload.signal,
            reason=payload.reason,
            actor=payload.actor,
            config=cfg,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not result.get("success"):
        reason = result.get("reason")
        if reason == "knowledge_not_found":
            raise HTTPException(status_code=404, detail=reason)
        if reason in {"feature_disabled", "deduplicated_within_window"}:
            raise HTTPException(status_code=400, detail=reason)
    _cache.invalidate()
    return result


@router.get("/knowledge/{knowledge_id}/related")
async def get_related(
    knowledge_id: int,
    request: Request,
    depth: int = Query(1, ge=1, le=5),
) -> dict[str, Any]:
    service = _get_service(request)
    neighbors = await service.get_related(knowledge_id, depth=depth)
    return {
        "id": knowledge_id,
        "depth": depth,
        "count": len(neighbors),
        "results": neighbors,
    }


@router.get("/tags")
async def list_tags(
    request: Request, scope: str | None = None
) -> dict[str, Any]:
    service = _get_service(request)
    tags = await service.list_tags(scope=scope)
    return {"scope": scope, "count": len(tags), "tags": tags}


@router.get("/tags/{tag}/knowledge")
async def search_by_tag(
    tag: str,
    request: Request,
    scope: str | None = None,
    limit: int = Query(20, ge=1, le=200),
) -> dict[str, Any]:
    service = _get_service(request)
    hits = await service.search_by_tag([tag], scope=scope, limit=limit)
    return {
        "tag": tag,
        "scope": scope,
        "count": len(hits),
        "results": hits,
    }
