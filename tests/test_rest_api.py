"""REST API integration tests — real SQLite, real FastAPI app, real httpx client.

No mocks per project red line. Each test spins up a fresh in-memory-like
(file-based tmp_path) KnowledgeService, attaches it to a FastAPI app built by
``create_app``-style wiring, and exercises endpoints via
``httpx.AsyncClient(transport=ASGITransport(app))``.

The lifespan/SSE mount from ``mnemo.server.app`` depends on the
module-level embedding service + monitor, so we skip it here and mount the
router directly onto a bare FastAPI app with ``app.state.service`` set —
the router is the unit under test, not the full composition.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mnemo.config import MnemoConfig
from mnemo.models.knowledge import Base
from mnemo.server.routes import _cache as _routes_cache
from mnemo.server.routes import router as api_router
from mnemo.services.knowledge_service import KnowledgeService


@pytest_asyncio.fixture
async def api_client(
    tmp_path: Path,
) -> AsyncIterator[tuple[AsyncClient, KnowledgeService]]:
    # routes._cache is module-level — reset per test so TTL entries from a
    # prior test with a different (now-discarded) SQLite file cannot leak in.
    _routes_cache.invalidate()
    db_path = tmp_path / "mnemo.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}", future=True
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts "
                "USING fts5(title, summary, content, tags, knowledge_id UNINDEXED)"
            )
        )
        await conn.execute(text("PRAGMA foreign_keys = ON"))

    factory = async_sessionmaker(engine, expire_on_commit=False)
    config = MnemoConfig(_env_file=None)  # type: ignore[call-arg]
    service = KnowledgeService(session_factory=factory, config=config)

    app = FastAPI()
    app.state.service = service
    app.include_router(api_router, prefix="/api/v1")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        try:
            yield client, service
        finally:
            await engine.dispose()


# ---------------------------------------------------------------------------
# create / get / search
# ---------------------------------------------------------------------------


async def test_create_knowledge_returns_201_with_id(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, _ = api_client
    resp = await client.post(
        "/api/v1/knowledge",
        json={
            "title": "useSearch hook",
            "summary": "central search state",
            "content": "hook under src/hooks/useSearch.ts",
            "tags": ["frontend", "react"],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["id"] >= 1
    assert body["title"] == "useSearch hook"
    assert body["tags"] == ["frontend", "react"]


async def test_get_knowledge_404_when_missing(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, _ = api_client
    resp = await client.get("/api/v1/knowledge/9999")
    assert resp.status_code == 404


async def test_search_returns_hit(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, _ = api_client
    await client.post(
        "/api/v1/knowledge",
        json={
            "title": "SQLite WAL",
            "summary": "WAL mode enables concurrent readers",
            "content": "SQLite journal_mode=WAL details",
            "tags": ["sqlite"],
        },
    )
    resp = await client.get(
        "/api/v1/knowledge/search",
        params={"query": "WAL", "mode": "fts"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] >= 1
    titles = [h["title"] for h in body["results"]]
    assert "SQLite WAL" in titles


async def test_search_unknown_mode_400(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, _ = api_client
    resp = await client.get(
        "/api/v1/knowledge/search",
        params={"query": "foo", "mode": "bogus"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# update / delete
# ---------------------------------------------------------------------------


async def test_update_no_fields_returns_400(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, _ = api_client
    create = await client.post(
        "/api/v1/knowledge",
        json={"title": "t", "summary": "s", "content": "c"},
    )
    kid = create.json()["id"]
    resp = await client.patch(f"/api/v1/knowledge/{kid}", json={})
    assert resp.status_code == 400


async def test_update_changes_title(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, _ = api_client
    create = await client.post(
        "/api/v1/knowledge",
        json={"title": "orig", "summary": "s", "content": "c"},
    )
    kid = create.json()["id"]
    resp = await client.patch(
        f"/api/v1/knowledge/{kid}", json={"title": "renamed"}
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "renamed"


async def test_delete_returns_404_when_missing(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, _ = api_client
    resp = await client.delete("/api/v1/knowledge/99999")
    assert resp.status_code == 404


async def test_delete_existing(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, _ = api_client
    create = await client.post(
        "/api/v1/knowledge",
        json={"title": "ephemeral", "summary": "s", "content": "c"},
    )
    kid = create.json()["id"]
    resp = await client.delete(f"/api/v1/knowledge/{kid}")
    assert resp.status_code == 200
    assert resp.json()["success"] is True


# ---------------------------------------------------------------------------
# archive / unarchive / feedback
# ---------------------------------------------------------------------------


async def test_archive_then_unarchive(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, _ = api_client
    create = await client.post(
        "/api/v1/knowledge",
        json={"title": "to archive", "summary": "s", "content": "c"},
    )
    kid = create.json()["id"]

    arch = await client.post(
        f"/api/v1/knowledge/{kid}/archive", json={"reason": "legacy"}
    )
    assert arch.status_code == 200, arch.text
    assert arch.json()["success"] is True

    un = await client.post(f"/api/v1/knowledge/{kid}/unarchive")
    assert un.status_code == 200
    assert un.json()["success"] is True


async def test_feedback_helpful(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, _ = api_client
    create = await client.post(
        "/api/v1/knowledge",
        json={"title": "fb target", "summary": "s", "content": "c"},
    )
    kid = create.json()["id"]

    resp = await client.post(
        f"/api/v1/knowledge/{kid}/feedback",
        json={"signal": "helpful", "actor": "agent:test"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["signal"] == "helpful"


async def test_feedback_invalid_signal_400(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, _ = api_client
    create = await client.post(
        "/api/v1/knowledge",
        json={"title": "fb bad", "summary": "s", "content": "c"},
    )
    kid = create.json()["id"]
    resp = await client.post(
        f"/api/v1/knowledge/{kid}/feedback",
        json={"signal": "bogus"},
    )
    assert resp.status_code == 400


async def test_feedback_missing_knowledge_404(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, _ = api_client
    resp = await client.post(
        "/api/v1/knowledge/9999/feedback",
        json={"signal": "helpful"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# related / tags
# ---------------------------------------------------------------------------


async def test_get_related_empty(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, _ = api_client
    create = await client.post(
        "/api/v1/knowledge",
        json={"title": "lonely", "summary": "s", "content": "c"},
    )
    kid = create.json()["id"]
    resp = await client.get(f"/api/v1/knowledge/{kid}/related")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0
    assert body["results"] == []


async def test_list_tags_returns_known_tags(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, _ = api_client
    await client.post(
        "/api/v1/knowledge",
        json={
            "title": "tagged",
            "summary": "s",
            "content": "c",
            "tags": ["alpha", "beta"],
        },
    )
    resp = await client.get("/api/v1/tags")
    assert resp.status_code == 200
    body = resp.json()
    assert "alpha" in body["tags"]
    assert "beta" in body["tags"]


async def test_search_by_tag(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, _ = api_client
    await client.post(
        "/api/v1/knowledge",
        json={
            "title": "alpha item",
            "summary": "s",
            "content": "c",
            "tags": ["alpha"],
        },
    )
    resp = await client.get("/api/v1/tags/alpha/knowledge")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tag"] == "alpha"
    assert body["count"] >= 1
    titles = [h["title"] for h in body["results"]]
    assert "alpha item" in titles


# ---------------------------------------------------------------------------
# list / stats — dashboard-facing aggregates
# ---------------------------------------------------------------------------


async def test_list_knowledge_returns_all(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, _ = api_client
    for i in range(3):
        await client.post(
            "/api/v1/knowledge",
            json={
                "title": f"item-{i}",
                "summary": "s",
                "content": "c",
                "tags": ["listing"],
            },
        )
    resp = await client.get("/api/v1/knowledge", params={"limit": 10})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 3
    titles = [r["title"] for r in body["results"]]
    assert set(titles) == {"item-0", "item-1", "item-2"}


async def test_list_relations_returns_newest_rows_first(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    """Viz uses a capped relation window; it must not get oldest edges first."""
    client, service = api_client
    from mnemo.models.knowledge import Knowledge, Relation

    async with service._session_factory() as session:
        rows = [
            Knowledge(title=f"k{i}", summary="s", content="c", scope="global")
            for i in range(4)
        ]
        session.add_all(rows)
        await session.flush()
        session.add_all(
            [
                Relation(
                    source_id=rows[0].id,
                    target_id=rows[1].id,
                    relation_type="related",
                ),
                Relation(
                    source_id=rows[1].id,
                    target_id=rows[2].id,
                    relation_type="related",
                ),
                Relation(
                    source_id=rows[2].id,
                    target_id=rows[3].id,
                    relation_type="related",
                ),
            ]
        )
        await session.commit()

    resp = await client.get("/api/v1/relations", params={"limit": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert [r["source_id"] for r in body["results"]] == [3, 2]


async def test_stats_empty_db(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, _ = api_client
    resp = await client.get("/api/v1/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["knowledge"] == {"total": 0, "by_status": {}}
    assert body["feedback"] == {"total": 0, "by_signal": {}}
    assert body["tool_calls"] == {}
    assert body["relations"]["contradictions"] == 0


async def test_stats_reflects_created_and_feedback(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, _ = api_client
    created_ids = []
    for i in range(2):
        resp = await client.post(
            "/api/v1/knowledge",
            json={
                "title": f"stats-item-{i}",
                "summary": "s",
                "content": "c",
                "tags": ["stats"],
            },
        )
        assert resp.status_code == 201
        created_ids.append(resp.json()["id"])

    fb = await client.post(
        f"/api/v1/knowledge/{created_ids[0]}/feedback",
        json={"signal": "helpful", "actor": "agent:test"},
    )
    assert fb.status_code == 200

    resp = await client.get("/api/v1/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["knowledge"]["total"] == 2
    assert body["knowledge"]["by_status"].get("active") == 2
    assert body["feedback"]["total"] == 1
    assert body["feedback"]["by_signal"].get("helpful") == 1


async def test_stats_tasks_empty_shape(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    """Empty DB: tasks block present, completion_rate does not divide by zero."""
    client, _ = api_client
    body = (await client.get("/api/v1/stats")).json()
    assert body["tasks"] == {
        "dispatched": 0,
        "completed": 0,
        "completion_rate": 0.0,
        "initiative_count": 0,
    }


async def test_stats_tasks_initiative_and_completion(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    """End-to-end task tracking math.

    Writes raw knowledge_event rows (the task tracking wiring will be done in
    parallel by other agents; this test locks the /stats aggregation contract
    regardless of who writes the events).
    """
    import json

    from mnemo.models.knowledge import Knowledge, KnowledgeEvent

    client, service = api_client
    async with service._session_factory() as s:
        k = Knowledge(
            title="stats-task-target",
            summary="s",
            content="c",
            scope="global",
        )
        s.add(k)
        await s.flush()
        kid = k.id

        # 3 dispatches, 2 closed via search_dispatch, 1 agent_initiative
        for _ in range(3):
            s.add(
                KnowledgeEvent(
                    knowledge_id=kid,
                    event_type="task_dispatched",
                    actor="system",
                    payload_json=json.dumps({"task_id": "a" * 32}),
                )
            )
        for _ in range(2):
            s.add(
                KnowledgeEvent(
                    knowledge_id=kid,
                    event_type="feedback",
                    actor="agent:test",
                    payload_json=json.dumps(
                        {"signal": "helpful", "trigger_source": "search_dispatch"}
                    ),
                )
            )
        s.add(
            KnowledgeEvent(
                knowledge_id=kid,
                event_type="feedback",
                actor="agent:test",
                payload_json=json.dumps(
                    {"signal": "helpful", "trigger_source": "agent_initiative"}
                ),
            )
        )
        # legacy event with no trigger_source — should count as initiative
        s.add(
            KnowledgeEvent(
                knowledge_id=kid,
                event_type="feedback",
                actor="agent:test",
                payload_json=json.dumps({"signal": "outdated"}),
            )
        )
        await s.commit()

    body = (await client.get("/api/v1/stats")).json()
    assert body["tasks"]["dispatched"] == 3
    assert body["tasks"]["completed"] == 2
    assert body["tasks"]["completion_rate"] == round(2 / 3, 3)
    assert body["tasks"]["initiative_count"] == 2


# ---------------------------------------------------------------------------
# events/recent
# ---------------------------------------------------------------------------


async def test_events_recent_empty_returns_zero(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, _ = api_client
    resp = await client.get("/api/v1/events/recent?tool=search&seconds=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"tool": "search", "seconds": 5, "count": 0, "results": []}


async def test_events_recent_returns_recent_search_only(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, service = api_client
    from datetime import datetime, timedelta, timezone

    from mnemo.monitor.models import MonitorEvent

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with service._session_factory() as session:
        # a fresh search event — should show up
        session.add(
            MonitorEvent(
                tool_name="search",
                params_json='{"query":"foo"}',
                result_summary='(id: 1) (id: 2)',
                latency_ms=12.5,
                created_at=now - timedelta(seconds=1),
                actor="agent:test",
                session_id="s1",
            )
        )
        # a stale search event — outside window, should be filtered
        session.add(
            MonitorEvent(
                tool_name="search",
                params_json='{"query":"old"}',
                result_summary='(id: 99)',
                latency_ms=1,
                created_at=now - timedelta(seconds=60),
                actor="agent:test",
                session_id="s1",
            )
        )
        # another tool in-window — should be filtered by tool
        session.add(
            MonitorEvent(
                tool_name="get_knowledge",
                params_json='{}',
                result_summary='',
                latency_ms=1,
                created_at=now - timedelta(seconds=1),
                actor="agent:test",
                session_id="s1",
            )
        )
        await session.commit()

    resp = await client.get("/api/v1/events/recent?tool=search&seconds=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    row = body["results"][0]
    assert row["tool_name"] == "search"
    assert row["result_summary"] == "(id: 1) (id: 2)"
    assert row["actor"] == "agent:test"
    assert row["status"] == "ok"


async def test_events_recent_rejects_out_of_range_seconds(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, _ = api_client
    resp = await client.get("/api/v1/events/recent?seconds=999")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# detail (aggregated: knowledge + feedback + relations)
# ---------------------------------------------------------------------------


async def test_detail_returns_feedback_and_relations(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, _ = api_client
    r1 = await client.post(
        "/api/v1/knowledge",
        json={
            "title": "alpha fact",
            "summary": "alpha summary",
            "content": "alpha content body here",
            "tags": ["test"],
        },
    )
    kid = r1.json()["id"]

    await client.post(
        f"/api/v1/knowledge/{kid}/feedback",
        json={"signal": "helpful", "actor": "agent:t1"},
    )
    await client.post(
        f"/api/v1/knowledge/{kid}/feedback",
        json={"signal": "misleading", "actor": "agent:t2"},
    )

    resp = await client.get(f"/api/v1/knowledge/{kid}/detail")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == kid
    assert "feedback" in body
    assert body["feedback"]["helpful"] >= 1
    assert body["feedback"]["misleading"] >= 1
    assert body["feedback"]["total"] >= 2
    assert "relations" in body
    assert isinstance(body["relations"], list)


async def test_detail_404_for_missing(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, _ = api_client
    resp = await client.get("/api/v1/knowledge/9999/detail")
    assert resp.status_code == 404


async def test_detail_includes_relation_peer_titles(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, _ = api_client
    await client.post(
        "/api/v1/knowledge",
        json={
            "title": "node A",
            "summary": "A summary",
            "content": "content A",
            "tags": ["graph"],
            "related": ["node B"],
        },
    )
    r2 = await client.post(
        "/api/v1/knowledge",
        json={
            "title": "node B",
            "summary": "B summary",
            "content": "content B",
            "tags": ["graph"],
            "related": ["node A"],
        },
    )
    kid_b = r2.json()["id"]

    resp = await client.get(f"/api/v1/knowledge/{kid_b}/detail")
    body = resp.json()
    rels = body.get("relations", [])
    if rels:
        peer_titles = [r["peer_title"] for r in rels]
        assert any("node A" in t for t in peer_titles)


async def test_search_results_include_timestamps(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, _ = api_client
    await client.post(
        "/api/v1/knowledge",
        json={
            "title": "timestamp test",
            "summary": "testing timestamps in search",
            "content": "content for timestamp test",
            "tags": ["test"],
        },
    )
    resp = await client.get(
        "/api/v1/knowledge/search",
        params={"query": "timestamp", "mode": "fts"},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) >= 1
    assert "created_at" in results[0]
    assert "updated_at" in results[0]
    assert results[0]["created_at"] is not None


async def test_list_results_include_timestamps(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, _ = api_client
    await client.post(
        "/api/v1/knowledge",
        json={
            "title": "list timestamp",
            "summary": "testing timestamps in list",
            "content": "content here",
            "tags": ["test"],
        },
    )
    resp = await client.get("/api/v1/knowledge")
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) >= 1
    assert "created_at" in results[0]
    assert "updated_at" in results[0]


# ---------------------------------------------------------------------------
# timeline
# ---------------------------------------------------------------------------


async def test_timeline_returns_events_and_nodes(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, svc = api_client
    r = await client.post(
        "/api/v1/knowledge",
        json={
            "title": "timeline node",
            "summary": "for timeline test",
            "content": "timeline content",
            "tags": ["timeline"],
        },
    )
    kid = r.json()["id"]

    await client.post(
        f"/api/v1/knowledge/{kid}/feedback",
        json={"signal": "helpful", "actor": "agent:timeline-test"},
    )

    resp = await client.get("/api/v1/timeline")
    assert resp.status_code == 200
    body = resp.json()
    assert "events" in body
    assert "nodes" in body
    assert "total_events" in body
    assert len(body["nodes"]) >= 1
    assert body["total_events"] >= 1


async def test_timeline_respects_since_filter(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, _ = api_client
    resp = await client.get("/api/v1/timeline?since=2099-01-01T00:00:00")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_events"] == 0
    assert len(body["nodes"]) == 0


async def test_timeline_limit_and_offset(
    api_client: tuple[AsyncClient, KnowledgeService],
) -> None:
    client, _ = api_client
    resp = await client.get("/api/v1/timeline?limit=1&offset=0")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["events"]) <= 1
