"""Phase 3 — Feedback loop tests (Task #5).

Written *before* the feature lands. Every test is gated by
``@pytest.mark.phase3`` and additionally skips when the required Phase 3
symbol / config field is not yet present, so the file is safely collectable
in the Phase 2 baseline but becomes a live gate the moment the feature
code merges.

Spec (docs/phase3/tech_research.md §8 + team-lead arbitration 2026-04-19):

1. New MCP tool: ``feedback_knowledge(knowledge_id, signal, reason=None,
   actor="agent:unknown")`` where ``signal ∈ {"helpful","misleading","outdated"}``.
2. Writes a ``KnowledgeEvent(event_type="feedback", actor=..., payload_json=
   json.dumps({"signal": ..., "reason": ...}))``. Zero new tables.
3. ``verification_mult = 0.7 + 0.6 · sigmoid(helpful_count − 2·misleading_count)``
   — range [0.7, 1.3]; *misleading penalty is 2× helpful reward*.
4. Sample floor: ``helpful + misleading < 3`` → mult = 1.0 (neutral prior).
5. Anti-spam dedup: same ``actor`` on same ``knowledge_id`` within 24h is
   collapsed to the latest event (no double-counting).
6. Window: only events in the last 30 days contribute to the counts.
7. ``reason`` is optional and capped at 500 chars (server-side reject).
8. Three consecutive ``misleading`` events (no ``helpful`` in between) on
   the same knowledge transition its status to ``stale``.
9. Feature flag ``MnemoConfig.feedback_loop_enabled``:
   - OFF → MCP tool returns a structured ``feature_disabled`` signal and no
     event is written; ``verification_mult`` falls back to 1.0 in rerank.

Red-line compliance: no mocks; real SQLite (``sqlite+aiosqlite:///``) with
the production schema from ``models.knowledge.Base``. The formula-purity
tests do not need a DB and exercise the pure function directly.
"""

from __future__ import annotations

import importlib
import json
import math
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mnemo.config import MnemoConfig
from mnemo.models.knowledge import Base, Knowledge, KnowledgeEvent
from mnemo.services.knowledge_service import KnowledgeService

from tests.conftest import flag_available


pytestmark = pytest.mark.phase3


# ---------------------------------------------------------------------------
# Phase 3 symbol loaders (tolerant until the feature ships)
# ---------------------------------------------------------------------------

FEEDBACK_SIGNALS = ("helpful", "misleading", "outdated")


def _require_flag(cfg: MnemoConfig) -> None:
    if not flag_available(cfg, "feedback_loop_enabled"):
        pytest.skip("feedback_loop_enabled flag not yet declared on MnemoConfig")


def _load_feedback_service():
    """Return the feedback service module once it lands, else skip."""
    try:
        return importlib.import_module("mnemo.services.feedback_service")
    except ModuleNotFoundError:
        pytest.skip("mnemo.services.feedback_service not yet implemented")


def _load_feedback_repo():
    try:
        return importlib.import_module("mnemo.repository.feedback_repository")
    except ModuleNotFoundError:
        pytest.skip("mnemo.repository.feedback_repository not yet implemented")


def _load_verification_module():
    try:
        return importlib.import_module("mnemo.ranking.verification")
    except ModuleNotFoundError:
        pytest.skip("mnemo.ranking.verification not yet implemented")


def _load_mcp_tool_name() -> str:
    """Return the feedback tool's registered MCP name once it's registered."""
    from mnemo.mcp import server as mcp_server

    # FastMCP exposes tool registry via `_tool_manager` or similar in v3; we
    # try both attribute styles plus a call to `call_tool` to detect presence.
    # If no tool with this name exists, calling it will error — we use that
    # behaviour in the actual test, not here.
    return "feedback_knowledge"


# ---------------------------------------------------------------------------
# DB fixture (independent of conftest.flag_service so formula tests stay fast)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
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
    async with factory() as s:
        yield s
    await engine.dispose()


async def _mk_knowledge(s: AsyncSession, title: str = "T") -> Knowledge:
    k = Knowledge(title=title, summary="s", content="c", tags="[]")
    s.add(k)
    await s.commit()
    await s.refresh(k)
    return k


async def _insert_feedback_event(
    s: AsyncSession,
    knowledge_id: int,
    signal: str,
    actor: str = "agent:alice",
    reason: str | None = None,
    at: datetime | None = None,
) -> KnowledgeEvent:
    """Raw event insertion used when exercising the *reader* side of the
    feedback loop (counts, sigmoid, window, dedup) before the service writer
    is in place. Lets the repository-layer tests run independently of the
    service-layer tests."""
    payload: dict[str, Any] = {"signal": signal}
    if reason is not None:
        payload["reason"] = reason
    ev = KnowledgeEvent(
        knowledge_id=knowledge_id,
        event_type="feedback",
        actor=actor,
        payload_json=json.dumps(payload, ensure_ascii=False),
    )
    if at is not None:
        ev.created_at = at
    s.add(ev)
    await s.commit()
    await s.refresh(ev)
    return ev


# ===========================================================================
# 1. MCP tool legality — signature, signal enum, reason length
# ===========================================================================

async def test_mcp_tool_registered_and_accepts_three_signals(flag_service) -> None:
    """Tool must exist, enforce signal ∈ {helpful, misleading, outdated},
    accept ``reason`` and ``actor`` parameters, and return a non-empty body.

    Covers spec items 1, 7 (reason field) and 10 (flag ON)."""
    service, cfg = flag_service
    _require_flag(cfg)
    setattr(cfg, "feedback_loop_enabled", True)
    _load_feedback_service()  # ensures the wiring has landed

    from mnemo.mcp import server as mcp_server
    mcp_server.set_service(service)

    created = await service.create_knowledge(
        title="FB target", summary="s", content="c"
    )
    kid = created["id"]

    for signal in FEEDBACK_SIGNALS:
        result = await mcp_server.mcp.call_tool(
            _load_mcp_tool_name(),
            {
                "knowledge_id": kid,
                "signal": signal,
                "reason": f"why-{signal}",
                "actor": f"agent:test-{signal}",
            },
        )
        body = result.content[0].text
        assert body, f"feedback tool returned empty body for signal={signal}"
        # Body should mention either the signal or a confirmation id.
        assert signal in body.lower() or "recorded" in body.lower() or "ok" in body.lower()


async def test_mcp_tool_rejects_unknown_signal(flag_service) -> None:
    """Illegal ``signal`` must be refused — either by MCP schema validation
    or by service-level error. Spec item 1."""
    service, cfg = flag_service
    _require_flag(cfg)
    setattr(cfg, "feedback_loop_enabled", True)
    _load_feedback_service()

    from mnemo.mcp import server as mcp_server
    mcp_server.set_service(service)

    created = await service.create_knowledge(
        title="FB bogus signal", summary="s", content="c"
    )
    kid = created["id"]

    with pytest.raises(Exception):
        await mcp_server.mcp.call_tool(
            _load_mcp_tool_name(),
            {"knowledge_id": kid, "signal": "totally-made-up"},
        )


async def test_reason_over_500_chars_rejected(flag_service) -> None:
    """Server-side ``reason`` max length is 500 chars. Spec item 7/9."""
    service, cfg = flag_service
    _require_flag(cfg)
    setattr(cfg, "feedback_loop_enabled", True)
    fs = _load_feedback_service()

    created = await service.create_knowledge(
        title="FB long reason", summary="s", content="c"
    )
    kid = created["id"]

    # 501 chars — exactly one over the limit.
    too_long = "x" * 501
    with pytest.raises(Exception):
        # The service must expose record_feedback; the call is the same for
        # MCP and CLI paths so we test here at the service boundary.
        await fs.record_feedback(  # type: ignore[attr-defined]
            service,
            knowledge_id=kid,
            signal="misleading",
            reason=too_long,
            actor="agent:alice",
        )

    # Exactly 500 is OK.
    out = await fs.record_feedback(  # type: ignore[attr-defined]
        service,
        knowledge_id=kid,
        signal="misleading",
        reason="x" * 500,
        actor="agent:alice",
    )
    assert out is not None


# ===========================================================================
# 2. knowledge_event persistence
# ===========================================================================

async def test_feedback_writes_knowledge_event_row(flag_service) -> None:
    """Every accepted feedback must land as ``KnowledgeEvent(event_type="feedback",
    actor=..., payload_json has {signal, reason?})``. Spec item 2."""
    service, cfg = flag_service
    _require_flag(cfg)
    setattr(cfg, "feedback_loop_enabled", True)
    fs = _load_feedback_service()

    created = await service.create_knowledge(
        title="FB row", summary="s", content="c"
    )
    kid = created["id"]

    await fs.record_feedback(  # type: ignore[attr-defined]
        service,
        knowledge_id=kid,
        signal="helpful",
        reason="good hit",
        actor="agent:alice",
    )

    async with service._session_factory() as s:  # type: ignore[attr-defined]
        rows = (
            await s.execute(
                select(KnowledgeEvent).where(
                    KnowledgeEvent.knowledge_id == kid,
                    KnowledgeEvent.event_type == "feedback",
                )
            )
        ).scalars().all()

    assert len(rows) == 1
    ev = rows[0]
    assert ev.actor == "agent:alice"
    payload = json.loads(ev.payload_json or "{}")
    assert payload["signal"] == "helpful"
    assert payload.get("reason") == "good hit"


# ===========================================================================
# 3. verification_mult sigmoid correctness
# ===========================================================================

def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _expected_mult(helpful: int, misleading: int) -> float:
    if helpful + misleading < 3:
        return 1.0
    return 0.7 + 0.6 * _sigmoid(helpful - 2 * misleading)


def test_verification_mult_pure_formula_monotonic_and_bounded() -> None:
    """Pure formula check — no DB, no service. Spec item 3.

    - Bounded in [0.7, 1.3].
    - Strictly increasing in ``helpful`` (for a fixed misleading, once the
      3-sample floor is cleared).
    - Strictly decreasing in ``misleading``.
    - Symmetric pivot: helpful==2·misleading → mult == 1.0 (sigmoid(0)=0.5
      → 0.7 + 0.6·0.5 = 1.0).
    """
    vs = _load_verification_module()

    # Bounds
    for h in range(0, 50):
        for m in range(0, 50):
            mult = vs.verification_mult(h, m)  # type: ignore[attr-defined]
            assert 0.7 - 1e-9 <= mult <= 1.3 + 1e-9

    # Pivot: helpful == 2 * misleading → exactly 1.0 (when sample floor passed)
    # Pick values where helpful + misleading ≥ 3.
    assert math.isclose(
        vs.verification_mult(2, 1),  # type: ignore[attr-defined]
        1.0,
        abs_tol=1e-9,
    )
    assert math.isclose(
        vs.verification_mult(4, 2),  # type: ignore[attr-defined]
        1.0,
        abs_tol=1e-9,
    )

    # Monotonic in helpful (misleading=1, so we start eligible at h=2)
    prev = -math.inf
    for h in range(2, 20):
        curr = vs.verification_mult(h, 1)  # type: ignore[attr-defined]
        assert curr >= prev
        prev = curr

    # Monotonic-decreasing in misleading (helpful=3, eligible from m=0)
    prev = math.inf
    for m in range(0, 20):
        curr = vs.verification_mult(3, m)  # type: ignore[attr-defined]
        assert curr <= prev
        prev = curr


def test_verification_mult_matches_reference_values() -> None:
    """Regression-grade anchors for the exact formula. Spec item 3."""
    vs = _load_verification_module()

    cases = [
        (3, 0, _expected_mult(3, 0)),
        (5, 0, _expected_mult(5, 0)),
        (0, 3, _expected_mult(0, 3)),
        (10, 0, _expected_mult(10, 0)),
        (10, 5, _expected_mult(10, 5)),
    ]
    for h, m, expected in cases:
        got = vs.verification_mult(h, m)  # type: ignore[attr-defined]
        assert math.isclose(got, expected, abs_tol=1e-9), (
            f"verification_mult({h},{m}) = {got!r}, expected {expected!r}"
        )


# ===========================================================================
# 4. Small-sample protection (count < 3 → 1.0)
# ===========================================================================

async def test_small_sample_returns_neutral_mult(session: AsyncSession) -> None:
    """``helpful + misleading < 3`` — repository must return mult=1.0
    regardless of balance. Spec item 4."""
    fr = _load_feedback_repo()
    k = await _mk_knowledge(session)

    # 0 events.
    mult = await fr.get_verification_mult(session, k.id)  # type: ignore[attr-defined]
    assert mult == 1.0

    # 1 helpful, 0 misleading → still under floor.
    await _insert_feedback_event(session, k.id, "helpful", actor="agent:a")
    mult = await fr.get_verification_mult(session, k.id)  # type: ignore[attr-defined]
    assert mult == 1.0

    # 2 helpful from distinct actors → still under floor (< 3).
    await _insert_feedback_event(session, k.id, "helpful", actor="agent:b")
    mult = await fr.get_verification_mult(session, k.id)  # type: ignore[attr-defined]
    assert mult == 1.0

    # 3rd distinct-actor event → floor cleared, mult must change.
    await _insert_feedback_event(session, k.id, "helpful", actor="agent:c")
    mult = await fr.get_verification_mult(session, k.id)  # type: ignore[attr-defined]
    assert mult > 1.0  # three helpfuls should lift above neutral


# ===========================================================================
# 5. Anti-spam 24h dedup (same actor → only latest counts)
# ===========================================================================

async def test_same_actor_within_24h_deduped_to_latest(session: AsyncSession) -> None:
    """Same ``actor`` on same ``knowledge_id`` within 24h → count once,
    latest wins. Spec item 5."""
    fr = _load_feedback_repo()
    k = await _mk_knowledge(session)
    now = datetime.now(timezone.utc)

    # Agent alice flip-flops three times in 6h — should count as one
    # (the latest) when computing counts.
    await _insert_feedback_event(
        session, k.id, "helpful", actor="agent:alice",
        at=now - timedelta(hours=6),
    )
    await _insert_feedback_event(
        session, k.id, "misleading", actor="agent:alice",
        at=now - timedelta(hours=4),
    )
    await _insert_feedback_event(
        session, k.id, "misleading", actor="agent:alice",
        at=now - timedelta(hours=1),
    )
    # Three distinct agents each give one helpful → floor cleared so mult
    # ≠ 1.0 regardless of dedup; we check that alice contributed only her
    # latest (misleading), not all three.
    await _insert_feedback_event(session, k.id, "helpful", actor="agent:b", at=now)
    await _insert_feedback_event(session, k.id, "helpful", actor="agent:c", at=now)

    counts = await fr.get_feedback_counts(session, k.id)  # type: ignore[attr-defined]
    # Expected: helpful=2 (b,c), misleading=1 (alice-latest), outdated=0.
    assert counts.get("helpful") == 2
    assert counts.get("misleading") == 1
    assert counts.get("outdated", 0) == 0


async def test_same_actor_beyond_24h_counts_separately(session: AsyncSession) -> None:
    """After the 24h window rolls over, the same actor's earlier vote is
    treated as independent. Spec item 5 boundary."""
    fr = _load_feedback_repo()
    k = await _mk_knowledge(session)
    now = datetime.now(timezone.utc)

    # 25h apart — outside the dedup window but still inside the 30d counting
    # window.
    await _insert_feedback_event(
        session, k.id, "helpful", actor="agent:alice",
        at=now - timedelta(hours=25),
    )
    await _insert_feedback_event(
        session, k.id, "helpful", actor="agent:alice",
        at=now,
    )
    # One more distinct actor to clear the 3-sample floor.
    await _insert_feedback_event(session, k.id, "helpful", actor="agent:b", at=now)

    counts = await fr.get_feedback_counts(session, k.id)  # type: ignore[attr-defined]
    assert counts.get("helpful") == 3


# ===========================================================================
# 6. 30-day window: old events do not contribute
# ===========================================================================

async def test_events_older_than_30d_excluded(session: AsyncSession) -> None:
    """Events >30 days old must not contribute. Spec item 6."""
    fr = _load_feedback_repo()
    k = await _mk_knowledge(session)
    now = datetime.now(timezone.utc)

    # Three ancient misleadings — all outside 30d, must be ignored.
    for actor in ("agent:x", "agent:y", "agent:z"):
        await _insert_feedback_event(
            session, k.id, "misleading", actor=actor,
            at=now - timedelta(days=45),
        )
    counts = await fr.get_feedback_counts(session, k.id)  # type: ignore[attr-defined]
    assert counts.get("helpful", 0) == 0
    assert counts.get("misleading", 0) == 0

    mult = await fr.get_verification_mult(session, k.id)  # type: ignore[attr-defined]
    assert mult == 1.0  # no eligible events → floor → neutral.


async def test_window_boundary_inside_counts(session: AsyncSession) -> None:
    """Event at t-29d is counted; event at t-31d is not. Spec item 6 boundary."""
    fr = _load_feedback_repo()
    k = await _mk_knowledge(session)
    now = datetime.now(timezone.utc)

    await _insert_feedback_event(
        session, k.id, "helpful", actor="agent:a",
        at=now - timedelta(days=29),
    )
    await _insert_feedback_event(
        session, k.id, "helpful", actor="agent:b",
        at=now - timedelta(days=31),  # outside
    )
    await _insert_feedback_event(
        session, k.id, "helpful", actor="agent:c",
        at=now - timedelta(days=2),
    )
    await _insert_feedback_event(
        session, k.id, "helpful", actor="agent:d",
        at=now,
    )

    counts = await fr.get_feedback_counts(session, k.id)  # type: ignore[attr-defined]
    # a (29d), c (2d), d (now) inside → 3. b (31d) excluded.
    assert counts.get("helpful") == 3


# ===========================================================================
# 7. Three consecutive misleadings → stale transition
# ===========================================================================

async def test_three_misleadings_in_a_row_mark_stale(flag_service) -> None:
    """Three ``misleading`` events from distinct actors with no ``helpful``
    in between must flip ``status`` to ``stale``. Spec item 8."""
    service, cfg = flag_service
    _require_flag(cfg)
    setattr(cfg, "feedback_loop_enabled", True)
    fs = _load_feedback_service()

    created = await service.create_knowledge(
        title="Triple strike", summary="s", content="c"
    )
    kid = created["id"]

    for actor in ("agent:a", "agent:b", "agent:c"):
        await fs.record_feedback(  # type: ignore[attr-defined]
            service,
            knowledge_id=kid,
            signal="misleading",
            actor=actor,
        )

    refreshed = await service.get_knowledge(kid)
    assert refreshed is not None
    assert refreshed["status"] == "stale"


async def test_helpful_resets_misleading_streak(flag_service) -> None:
    """A ``helpful`` between two ``misleading`` events breaks the streak;
    status must stay ``active``. Spec item 8 (negative)."""
    service, cfg = flag_service
    _require_flag(cfg)
    setattr(cfg, "feedback_loop_enabled", True)
    fs = _load_feedback_service()

    created = await service.create_knowledge(
        title="Not quite stale", summary="s", content="c"
    )
    kid = created["id"]

    await fs.record_feedback(service, knowledge_id=kid, signal="misleading", actor="a")  # type: ignore[attr-defined]
    await fs.record_feedback(service, knowledge_id=kid, signal="helpful", actor="b")    # type: ignore[attr-defined]
    await fs.record_feedback(service, knowledge_id=kid, signal="misleading", actor="c")  # type: ignore[attr-defined]
    await fs.record_feedback(service, knowledge_id=kid, signal="misleading", actor="d")  # type: ignore[attr-defined]

    refreshed = await service.get_knowledge(kid)
    assert refreshed is not None
    # Only two consecutive misleadings at the tail → not stale yet.
    assert refreshed["status"] != "stale"


# ===========================================================================
# 8. Feature flag on/off
# ===========================================================================

async def test_feature_flag_off_tool_returns_disabled_and_writes_nothing(flag_service, flags) -> None:
    """Flag OFF → MCP tool responds with a ``feature_disabled`` marker and
    must not write any ``feedback`` event. Spec item 10 (OFF)."""
    service, cfg = flag_service
    _require_flag(cfg)
    _load_feedback_service()

    from mnemo.mcp import server as mcp_server
    mcp_server.set_service(service)

    created = await service.create_knowledge(
        title="FB flag-off", summary="s", content="c"
    )
    kid = created["id"]

    with flags(feedback_loop_enabled=False):
        result = await mcp_server.mcp.call_tool(
            _load_mcp_tool_name(),
            {"knowledge_id": kid, "signal": "helpful", "actor": "agent:a"},
        )
        body = result.content[0].text
        assert "feature_disabled" in body or "disabled" in body.lower()

    async with service._session_factory() as s:  # type: ignore[attr-defined]
        rows = (
            await s.execute(
                select(KnowledgeEvent).where(
                    KnowledgeEvent.knowledge_id == kid,
                    KnowledgeEvent.event_type == "feedback",
                )
            )
        ).scalars().all()
    assert rows == []


async def test_feature_flag_off_verification_mult_is_neutral(session: AsyncSession, flags) -> None:
    """Flag OFF → reader (used in rerank) returns 1.0 even if stale feedback
    events exist in the DB. Spec item 10 (OFF, read path)."""
    fr = _load_feedback_repo()
    k = await _mk_knowledge(session)
    now = datetime.now(timezone.utc)
    # Plant enough events to normally push the mult off 1.0.
    for actor in ("agent:a", "agent:b", "agent:c", "agent:d"):
        await _insert_feedback_event(session, k.id, "helpful", actor=actor, at=now)

    with flags(feedback_loop_enabled=False) as cfg:
        mult = await fr.get_verification_mult(session, k.id, config=cfg)  # type: ignore[attr-defined]
        assert mult == 1.0


# ===========================================================================
# 9. Outdated signal accounted as its own bucket (not misleading)
# ===========================================================================

async def test_outdated_signal_is_tracked_but_not_weighted_as_misleading(
    session: AsyncSession,
) -> None:
    """``outdated`` is a distinct bucket. Spec item 1 + §8.2 payload shape.

    It should NOT appear in the ``misleading`` count and must NOT push the
    mult below 1.0 by itself (the mult formula only uses helpful/misleading)."""
    fr = _load_feedback_repo()
    k = await _mk_knowledge(session)
    now = datetime.now(timezone.utc)

    for actor in ("agent:a", "agent:b", "agent:c", "agent:d"):
        await _insert_feedback_event(session, k.id, "outdated", actor=actor, at=now)

    counts = await fr.get_feedback_counts(session, k.id)  # type: ignore[attr-defined]
    assert counts.get("misleading", 0) == 0
    assert counts.get("outdated") == 4

    # Formula only uses helpful/misleading → 0 helpful + 0 misleading < 3 →
    # mult stays at 1.0.
    mult = await fr.get_verification_mult(session, k.id)  # type: ignore[attr-defined]
    assert mult == 1.0


# ===========================================================================
# 10. End-to-end: feedback affects verification_mult at service layer
# ===========================================================================

async def test_mult_reflects_recorded_feedback_end_to_end(flag_service) -> None:
    """Service-level check: record 4 helpfuls from distinct actors → mult >
    1.0 (floor cleared, all helpful). Covers items 2, 3, 4 end-to-end."""
    service, cfg = flag_service
    _require_flag(cfg)
    setattr(cfg, "feedback_loop_enabled", True)
    fs = _load_feedback_service()
    fr = _load_feedback_repo()

    created = await service.create_knowledge(
        title="FB e2e", summary="s", content="c"
    )
    kid = created["id"]

    for actor in ("agent:a", "agent:b", "agent:c", "agent:d"):
        await fs.record_feedback(  # type: ignore[attr-defined]
            service,
            knowledge_id=kid,
            signal="helpful",
            actor=actor,
        )

    async with service._session_factory() as s:  # type: ignore[attr-defined]
        mult = await fr.get_verification_mult(s, kid)  # type: ignore[attr-defined]

    assert mult > 1.0
    # Bounded.
    assert mult <= 1.3 + 1e-9
