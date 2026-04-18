"""Phase 3 state-machine stale lifecycle tests (task #7).

Scope: only the极简 state-machine design from
docs/phase3/tech_research.md §6 (team-lead 2026-04-19 arbitration):

  - Add `stale` state + `last_accessed_at` field
  - read-lazy transition active → stale on search / get_knowledge
  - per-claim_type thresholds (fact / decision / procedure / hypothesis)
  - last_accessed_at refresh resets the threshold clock
  - rerank penalty multiplier 0.6 for stale rows
  - archive_knowledge MCP tool + search filters archived by default
  - last_accessed_at dedupe window (60s)
  - feature flag `state_machine_enabled` off → no transition, no penalty
  - stale + freshness signals stack multiplicatively

Project red lines:
  - no mocks — every test exercises a real aiosqlite DB + real services
  - every test is marked @pytest.mark.phase3 so the suite is skipped by
    default until the implementation lands (same gate used by the other
    Phase 3 test files).

These tests are written **before** the implementation; they pin the
contract that implementer must satisfy. A test that fails here after
M3 lands is a real regression — not a design question to be relitigated.

Where the implementation module / API name is ambiguous, the tests import
from the planned path and let pytest surface ``ModuleNotFoundError`` /
``ImportError`` — that is the unambiguous signal "contract not yet met".
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mnemo.models.knowledge import Base, Knowledge, KnowledgeEvent
from mnemo.services.knowledge_service import KnowledgeService


pytestmark = pytest.mark.phase3


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def service(tmp_path: Path) -> AsyncIterator[KnowledgeService]:
    """Fresh file-based SQLite with FTS5 + ORM — no mocks, no shared state."""
    db_path = tmp_path / "mnemo.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
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
    try:
        yield KnowledgeService(session_factory=factory)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(service: KnowledgeService) -> async_sessionmaker:
    """Expose the service's session factory so tests can backdate timestamps
    directly — aging real wall-clock time is not an option."""
    return service._session_factory  # noqa: SLF001 — test-only access


def _utc(days_ago: int = 0, seconds_ago: int = 0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago, seconds=seconds_ago)


async def _backdate(
    session_factory: async_sessionmaker,
    knowledge_id: int,
    *,
    updated_days_ago: int | None = None,
    last_accessed_days_ago: int | None = None,
    status: str | None = None,
) -> None:
    """Rewrite timestamps on an existing row so a stale-threshold test does
    not have to wait 181 real days."""
    values: dict = {}
    if updated_days_ago is not None:
        values["updated_at"] = _utc(days_ago=updated_days_ago)
    if last_accessed_days_ago is not None:
        values["last_accessed_at"] = _utc(days_ago=last_accessed_days_ago)
    if status is not None:
        values["status"] = status
    async with session_factory() as session:
        await session.execute(
            update(Knowledge).where(Knowledge.id == knowledge_id).values(**values)
        )
        await session.commit()


async def _status(session_factory: async_sessionmaker, knowledge_id: int) -> str:
    async with session_factory() as session:
        row = await session.get(Knowledge, knowledge_id)
        assert row is not None, f"knowledge id={knowledge_id} missing"
        return row.status


async def _last_accessed(
    session_factory: async_sessionmaker, knowledge_id: int
) -> datetime | None:
    async with session_factory() as session:
        row = await session.get(Knowledge, knowledge_id)
        assert row is not None
        return row.last_accessed_at


async def _create(
    service: KnowledgeService,
    *,
    title: str,
    content: str,
    claim_type: str,
    summary: str | None = None,
    tags: list[str] | None = None,
) -> int:
    result = await service.create_knowledge(
        title=title,
        summary=summary or title,
        content=content,
        tags=tags or [],
        claim_type=claim_type,
    )
    return result["id"]


# Per-claim_type stale thresholds from tech_research.md §6.2 — (updated, accessed)
# Both conditions must hold simultaneously for the transition to fire.
STALE_THRESHOLDS = {
    "fact": (180, 90),
    "decision": (120, 60),
    "procedure": (60, 30),
    "hypothesis": (30, 14),
}


# ---------------------------------------------------------------------------
# 1. Threshold per claim_type — read-lazy transitions to stale
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("claim_type", list(STALE_THRESHOLDS.keys()))
@pytest.mark.asyncio
async def test_read_lazy_transition_when_both_conditions_exceed_threshold(
    service, session_factory, claim_type
):
    """For every claim_type, a row whose updated_at and last_accessed_at both
    exceed the §6.2 threshold must flip active→stale on the next read."""
    updated_days, accessed_days = STALE_THRESHOLDS[claim_type]
    kid = await _create(
        service,
        title=f"陈旧的{claim_type}条目",
        content=f"{claim_type} 内容占位",
        claim_type=claim_type,
    )
    # Push both clocks one day past the threshold → transition must fire.
    await _backdate(
        session_factory,
        kid,
        updated_days_ago=updated_days + 1,
        last_accessed_days_ago=accessed_days + 1,
    )
    assert await _status(session_factory, kid) == "active"

    got = await service.get_knowledge(kid)
    assert got is not None
    # Implementation contract: read-lazy either writes status in the same
    # transaction (observed on re-read) or returns the already-transitioned
    # status. Either way the next observation must be "stale".
    assert await _status(session_factory, kid) == "stale"


@pytest.mark.parametrize("claim_type", list(STALE_THRESHOLDS.keys()))
@pytest.mark.asyncio
async def test_no_transition_when_only_one_condition_met(
    service, session_factory, claim_type
):
    """Only one of {updated, last_accessed} past its threshold → stay active.
    §6.2 requires BOTH conditions (同时满足)."""
    updated_days, accessed_days = STALE_THRESHOLDS[claim_type]

    # Case A: updated_at old, last_accessed_at recent → stay active.
    kid_a = await _create(
        service,
        title=f"A-{claim_type}-half",
        content="只旧不冷",
        claim_type=claim_type,
    )
    await _backdate(
        session_factory,
        kid_a,
        updated_days_ago=updated_days + 10,
        last_accessed_days_ago=max(0, accessed_days - 5),
    )
    await service.get_knowledge(kid_a)
    assert await _status(session_factory, kid_a) == "active"

    # Case B: last_accessed_at old, updated_at recent → stay active.
    kid_b = await _create(
        service,
        title=f"B-{claim_type}-half",
        content="只冷不旧",
        claim_type=claim_type,
    )
    await _backdate(
        session_factory,
        kid_b,
        updated_days_ago=max(0, updated_days - 5),
        last_accessed_days_ago=accessed_days + 10,
    )
    await service.get_knowledge(kid_b)
    assert await _status(session_factory, kid_b) == "active"


@pytest.mark.asyncio
async def test_hypothesis_threshold_tightest(service, session_factory):
    """A hypothesis aged 31/15 days must flip; a fact aged the same amount
    must NOT — confirms the per-claim_type dispatch."""
    # Same age, different claim_type.
    hypo_id = await _create(
        service,
        title="推测 31 天",
        content="临时假设",
        claim_type="hypothesis",
    )
    fact_id = await _create(
        service,
        title="事实 31 天",
        content="稳定事实",
        claim_type="fact",
    )
    for kid in (hypo_id, fact_id):
        await _backdate(
            session_factory,
            kid,
            updated_days_ago=31,
            last_accessed_days_ago=15,
        )
        await service.get_knowledge(kid)

    # Hypothesis threshold (30/14) exceeded → stale.
    # Fact threshold (180/90) far from exceeded → active.
    assert await _status(session_factory, hypo_id) == "stale"
    assert await _status(session_factory, fact_id) == "active"


# ---------------------------------------------------------------------------
# 2. Access-reset behavior — last_accessed_at refresh starts the clock over
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_access_refresh_resets_the_stale_clock(service, session_factory):
    """§6.2 "被读就会刷新活跃度" — a read updates last_accessed_at, so a row
    that WOULD have gone stale next read now stays active until the accessed
    threshold elapses again."""
    kid = await _create(
        service,
        title="读后复位 procedure",
        content="procedure 复位",
        claim_type="procedure",
    )
    # Procedure thresholds: 60d updated / 30d accessed.
    # Backdate only `updated_at` (old), keep `last_accessed_at` NULL.
    await _backdate(session_factory, kid, updated_days_ago=70)

    # First read → refreshes last_accessed_at to ~now. Row should remain
    # active because last_accessed_at just got reset to recent.
    await service.get_knowledge(kid)
    assert await _status(session_factory, kid) == "active"

    la_after_first = await _last_accessed(session_factory, kid)
    assert la_after_first is not None
    assert (datetime.now(timezone.utc) - la_after_first) < timedelta(seconds=10)

    # Still shouldn't be stale on subsequent read — updated_at old but access
    # clock was reset.
    await service.get_knowledge(kid)
    assert await _status(session_factory, kid) == "active"


# ---------------------------------------------------------------------------
# 3. Rerank penalty — stale × 0.6
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_penalty_multiplier_is_0_6():
    """stale_penalty_multiplier = 0.6 applied as a final-score乘子."""
    from mnemo.ranking.rerank import apply_rerank

    # Two identical rrf candidates, same scope, no authority, no contradiction.
    # Differ only in `status`. Implementation must multiply stale row's final
    # score by 0.6.
    fused = [
        {
            "id": 1,
            "rrf_score": 0.02,
            "fts_rank": 1,
            "vec_rank": 1,
            "source": "both",
            "status": "active",
        },
        {
            "id": 2,
            "rrf_score": 0.02,
            "fts_rank": 1,
            "vec_rank": 1,
            "source": "both",
            "status": "stale",
        },
    ]
    out = apply_rerank(
        fused,
        authority_lookup=lambda _kid: 0.0,
        contradiction_lookup=lambda _kid: False,
    )
    by_id = {e["id"]: e for e in out}
    # Active row first; stale row penalized.
    assert [e["id"] for e in out] == [1, 2]
    assert abs(by_id[2]["final_score"] - 0.02 * 0.6) < 1e-9
    assert abs(by_id[1]["final_score"] - 0.02) < 1e-9


# ---------------------------------------------------------------------------
# 4. archive_knowledge + search filters archived by default
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_knowledge_hides_from_default_search(service, session_factory):
    """archive_knowledge(id) sets status=archived; search default filter
    (include_archived=False) must drop it."""
    kid = await _create(
        service,
        title="archive me decision",
        content="archive decision keyword",
        claim_type="decision",
    )
    # Archive via the service facade. Planned API per §6.3.
    archive_fn = getattr(service, "archive_knowledge", None)
    assert archive_fn is not None, "KnowledgeService.archive_knowledge missing"
    res = await archive_fn(kid, reason="superseded by newer decision")
    assert res.get("success") is True

    assert await _status(session_factory, kid) == "archived"

    # Default search excludes archived.
    hits = await service.search("archive", mode="fts")
    assert all(h["id"] != kid for h in hits), (
        f"archived id={kid} leaked into default search: {[h['id'] for h in hits]}"
    )

    # include_archived=True brings it back (contract §6.3).
    hits_all = await service.search("archive", mode="fts", include_archived=True)
    assert any(h["id"] == kid for h in hits_all)


@pytest.mark.asyncio
async def test_archive_event_recorded(service, session_factory):
    """archive action must write a knowledge_event(event_type='archived')."""
    kid = await _create(
        service,
        title="审计 archive",
        content="审计内容",
        claim_type="fact",
    )
    await service.archive_knowledge(kid, reason="not relevant anymore")

    async with session_factory() as s:
        rows = (
            await s.execute(
                select(KnowledgeEvent).where(
                    KnowledgeEvent.knowledge_id == kid,
                    KnowledgeEvent.event_type == "archived",
                )
            )
        ).scalars().all()
    assert len(rows) == 1, f"expected 1 archived event, got {len(rows)}"
    payload = json.loads(rows[0].payload_json or "{}")
    assert "not relevant" in (payload.get("reason") or payload.get("detail") or "")


@pytest.mark.asyncio
async def test_stale_transition_event_recorded(service, session_factory):
    """read-lazy transition must write knowledge_event(event_type=
    'stale_transition') with from/to payload (§6.2)."""
    kid = await _create(
        service,
        title="审计 stale",
        content="审计",
        claim_type="hypothesis",
    )
    await _backdate(
        session_factory, kid, updated_days_ago=40, last_accessed_days_ago=20
    )
    await service.get_knowledge(kid)

    async with session_factory() as s:
        rows = (
            await s.execute(
                select(KnowledgeEvent).where(
                    KnowledgeEvent.knowledge_id == kid,
                    KnowledgeEvent.event_type == "stale_transition",
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    payload = json.loads(rows[0].payload_json or "{}")
    assert payload.get("from_status") == "active"
    assert payload.get("to_status") == "stale"


# ---------------------------------------------------------------------------
# 5. Stale + freshness signals stack multiplicatively
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_and_freshness_stack_multiplicatively():
    """§6.5 risk "stale × 0.6 和 freshness exp(-λt) 信号重合":
    两者乘性叠加 — stale 提供阈值外硬降权 + freshness 提供连续衰减.
    Both multipliers must appear in the final score simultaneously."""
    from mnemo.ranking.rerank import apply_rerank

    # Candidate with freshness_mult 0.5 and stale status. Final must be
    # rrf * 1.0 (authority) * 0.7^0 * 0.5 (freshness) * 0.6 (stale) if both
    # are present. Implementation may accept freshness via lookup.
    try:
        out = apply_rerank(
            [
                {
                    "id": 1,
                    "rrf_score": 1.0,
                    "fts_rank": 1,
                    "vec_rank": 1,
                    "source": "both",
                    "status": "stale",
                    "freshness_mult": 0.5,
                }
            ],
            authority_lookup=lambda _kid: 0.0,
            contradiction_lookup=lambda _kid: False,
        )
    except TypeError:
        # New signature may introduce a freshness_lookup keyword — if so,
        # call it with that style and a fixed map.
        out = apply_rerank(  # type: ignore[call-arg]
            [
                {
                    "id": 1,
                    "rrf_score": 1.0,
                    "fts_rank": 1,
                    "vec_rank": 1,
                    "source": "both",
                    "status": "stale",
                }
            ],
            authority_lookup=lambda _kid: 0.0,
            contradiction_lookup=lambda _kid: False,
            freshness_lookup=lambda _kid: 0.5,
        )

    # Expected = rrf * stale_mult * freshness_mult = 1.0 * 0.6 * 0.5 = 0.30
    assert abs(out[0]["final_score"] - 0.30) < 1e-9, (
        f"signals did not stack multiplicatively: {out[0]}"
    )


# ---------------------------------------------------------------------------
# 6. Feature flag — state_machine_enabled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feature_flag_off_no_transition(service, session_factory, monkeypatch):
    """With state_machine_enabled=False, read-lazy transition must NOT fire
    and rerank multiplier must stay at 1.0 (neutral) — §15 "off 时默认值中性"."""
    monkeypatch.setenv("MNEMO_STATE_MACHINE_ENABLED", "false")

    kid = await _create(
        service,
        title="flag off procedure",
        content="flag off",
        claim_type="procedure",
    )
    await _backdate(
        session_factory, kid, updated_days_ago=365, last_accessed_days_ago=365
    )
    await service.get_knowledge(kid)

    # No transition despite exceeding every threshold.
    assert await _status(session_factory, kid) == "active"


@pytest.mark.asyncio
async def test_feature_flag_off_rerank_neutral(monkeypatch):
    """With the flag off, a stale-labelled row receives no penalty — so that
    a partial rollback stays safe (existing rows already marked stale must
    not be silently demoted)."""
    from mnemo.ranking.rerank import apply_rerank

    monkeypatch.setenv("MNEMO_STATE_MACHINE_ENABLED", "false")

    out = apply_rerank(
        [
            {
                "id": 1,
                "rrf_score": 0.02,
                "fts_rank": 1,
                "vec_rank": 1,
                "source": "both",
                "status": "stale",
            }
        ],
        authority_lookup=lambda _kid: 0.0,
        contradiction_lookup=lambda _kid: False,
    )
    # Flag off: multiplier is neutral 1.0, not 0.6.
    assert abs(out[0]["final_score"] - 0.02) < 1e-9, (
        f"flag off must not penalize: {out[0]}"
    )


# ---------------------------------------------------------------------------
# 7. last_accessed_at 60s dedupe window
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_last_accessed_at_dedupes_within_60s(service, session_factory):
    """§6.5 风险缓解 "last_accessed_at 写入打爆 IOPS → 60s 内去重更新".
    Two reads within 60s must not cause two timestamp writes."""
    kid = await _create(
        service,
        title="去重 60s",
        content="dedupe",
        claim_type="fact",
    )
    # Seed last_accessed_at to a known value ~5s in the past.
    seed = _utc(seconds_ago=5)
    async with session_factory() as s:
        await s.execute(
            update(Knowledge).where(Knowledge.id == kid).values(last_accessed_at=seed)
        )
        await s.commit()

    # Two reads in quick succession.
    await service.get_knowledge(kid)
    await service.get_knowledge(kid)

    la = await _last_accessed(session_factory, kid)
    assert la is not None
    # Implementation keeps the seeded value because <60s has elapsed since
    # the last write. Allow ±1s tolerance for timezone / ORM coercion.
    assert abs((la - seed).total_seconds()) < 1.0, (
        f"expected dedupe to keep seed {seed}, got {la}"
    )


@pytest.mark.asyncio
async def test_stale_transition_does_not_bump_updated_at(service, session_factory):
    """Regression: the read-lazy stale flip must NOT reset ``updated_at``.
    SQLAlchemy Core UPDATE still fires ``Column.onupdate``, so the flip
    statement must pin ``updated_at`` explicitly — otherwise a stale flip
    would refresh the freshness clock, erasing the very signal that
    triggered it and preventing subsequent stale detection.
    """
    kid = await _create(
        service,
        title="stale updated_at regression",
        content="backdated fact",
        claim_type="fact",
    )
    # Backdate both clocks past the stale threshold for "fact" (180/90).
    await _backdate(
        session_factory,
        kid,
        updated_days_ago=200,
        last_accessed_days_ago=100,
    )
    async with session_factory() as session:
        before = (
            await session.execute(
                select(Knowledge.updated_at).where(Knowledge.id == kid)
            )
        ).scalar_one()

    # Trigger the read-lazy transition.
    await service.get_knowledge(kid)

    # Status flipped...
    assert await _status(session_factory, kid) == "stale"

    # ...but updated_at must be byte-identical to the backdated value.
    async with session_factory() as session:
        after = (
            await session.execute(
                select(Knowledge.updated_at).where(Knowledge.id == kid)
            )
        ).scalar_one()
    assert after == before, (
        f"stale flip bumped updated_at: {before} -> {after} "
        "(freshness clock was reset, which would hide the staleness)"
    )
