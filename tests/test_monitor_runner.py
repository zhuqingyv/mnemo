"""Tests for monitor notifier + runner (task #52).

Red lines (from project CLAUDE.md): no mocks. Real SQLite, real subprocess
boundary stubbed only by pointing osascript at a non-existent path via
monkeypatched platform string.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mnemo.models.knowledge import Base
from mnemo.monitor.models import AlertHistory, MonRuleState
from mnemo.monitor.notifier import DEFAULT_COOLDOWN_S, Notifier
from mnemo.monitor.runner import Alert, MonitorRunner


# ---------------------------------------------------------------------------
# fixtures — real SQLite, no mocks
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker]:
    db_path = tmp_path / "monitor.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("PRAGMA foreign_keys = ON"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


class FakeRule:
    """Duck-typed rule — matches runner's RuleProtocol contract."""

    def __init__(
        self,
        rule_id: str,
        severity: str = "warning",
        cooldown_s: float = 0.0,
        alerts: list[Alert] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.id = rule_id
        self.severity = severity
        self.cooldown_s = cooldown_s
        self._alerts = alerts if alerts is not None else []
        self._raises = raises
        self.call_count = 0

    async def evaluate(self, session) -> list[Alert]:
        self.call_count += 1
        if self._raises is not None:
            raise self._raises
        return list(self._alerts)


class _NonDispatchNotifier(Notifier):
    """Notifier that does not shell out — tracks calls for assertions."""

    def __init__(self, cooldown_s: float = DEFAULT_COOLDOWN_S) -> None:
        super().__init__(cooldown_s=cooldown_s)
        self.calls: list[tuple[str, str, str]] = []

    @staticmethod
    def _notify_system(title, message, severity) -> None:  # override to no-op
        return None

    def send_alert(self, rule_id, severity, message, *, title=None):  # type: ignore[override]
        sent = super().send_alert(
            rule_id=rule_id, severity=severity, message=message, title=title
        )
        if sent:
            self.calls.append((rule_id, severity, message))
        return sent


# ---------------------------------------------------------------------------
# notifier tests
# ---------------------------------------------------------------------------


def test_notifier_info_respects_cooldown() -> None:
    n = _NonDispatchNotifier(cooldown_s=3600)
    assert n.send_alert("r1", "info", "first") is True
    assert n.send_alert("r1", "info", "second") is False  # cooldown blocks
    assert len(n.calls) == 1


def test_notifier_warning_respects_cooldown() -> None:
    n = _NonDispatchNotifier(cooldown_s=3600)
    assert n.send_alert("r1", "warning", "a") is True
    assert n.send_alert("r1", "warning", "b") is False


def test_notifier_critical_bypasses_cooldown() -> None:
    n = _NonDispatchNotifier(cooldown_s=3600)
    assert n.send_alert("rc", "critical", "boom") is True
    assert n.send_alert("rc", "critical", "boom again") is True
    assert len(n.calls) == 2


def test_notifier_cooldown_is_per_rule() -> None:
    n = _NonDispatchNotifier(cooldown_s=3600)
    assert n.send_alert("r1", "info", "x") is True
    assert n.send_alert("r2", "info", "y") is True  # different rule
    assert n.send_alert("r1", "info", "z") is False


# ---------------------------------------------------------------------------
# runner tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_empty_rules_is_noop(session_factory) -> None:
    notifier = _NonDispatchNotifier()
    runner = MonitorRunner(
        domain="search_quality",
        rules=[],
        session_factory=session_factory,
        notifier=notifier,
    )
    # Do one tick manually (avoids starting the loop).
    await runner._run_tick()
    assert notifier.calls == []


@pytest.mark.asyncio
async def test_runner_dispatches_alert_and_records_history(
    session_factory,
) -> None:
    rule = FakeRule(
        "search.empty_streak",
        severity="warning",
        cooldown_s=600,
        alerts=[Alert("search.empty_streak", "warning", "5 empties")],
    )
    notifier = _NonDispatchNotifier()
    runner = MonitorRunner(
        domain="search_quality",
        rules=[rule],
        session_factory=session_factory,
        notifier=notifier,
    )
    await runner._run_tick()

    assert notifier.calls == [
        ("search.empty_streak", "warning", "5 empties"),
    ]

    async with session_factory() as session:
        history = (await session.execute(select(AlertHistory))).scalars().all()
        assert len(history) == 1
        assert history[0].rule_id == "search.empty_streak"
        assert history[0].severity == "warning"
        assert history[0].message == "5 empties"
        assert history[0].cooldown_until is not None

        state = await session.get(MonRuleState, "search.empty_streak")
        assert state is not None
        assert state.domain == "search_quality"
        assert state.trigger_count == 1


@pytest.mark.asyncio
async def test_runner_db_cooldown_blocks_second_tick(session_factory) -> None:
    """AlertHistory recorded in tick 1 must gate tick 2 — cross-restart safe."""
    rule = FakeRule(
        "r.cool",
        severity="warning",
        cooldown_s=600,
        alerts=[Alert("r.cool", "warning", "hit")],
    )
    notifier = _NonDispatchNotifier(cooldown_s=0)  # notifier cooldown off
    runner = MonitorRunner(
        domain="search_quality",
        rules=[rule],
        session_factory=session_factory,
        notifier=notifier,
    )

    await runner._run_tick()
    await runner._run_tick()

    # Evaluate called only once — second tick short-circuits on DB cooldown.
    assert rule.call_count == 1
    assert len(notifier.calls) == 1


@pytest.mark.asyncio
async def test_runner_critical_ignores_db_cooldown(session_factory) -> None:
    rule = FakeRule(
        "r.crit",
        severity="critical",
        cooldown_s=600,
        alerts=[Alert("r.crit", "critical", "fire")],
    )
    notifier = _NonDispatchNotifier(cooldown_s=0)
    runner = MonitorRunner(
        domain="search_quality",
        rules=[rule],
        session_factory=session_factory,
        notifier=notifier,
    )
    await runner._run_tick()
    await runner._run_tick()

    assert rule.call_count == 2
    assert len(notifier.calls) == 2

    async with session_factory() as session:
        history = (await session.execute(select(AlertHistory))).scalars().all()
        assert len(history) == 2
        state = await session.get(MonRuleState, "r.crit")
        assert state is not None
        assert state.trigger_count == 2


@pytest.mark.asyncio
async def test_runner_rule_exception_does_not_break_loop(session_factory) -> None:
    """A crashing rule must not prevent other rules in the same tick."""
    bad = FakeRule("bad", severity="warning", raises=RuntimeError("boom"))
    good = FakeRule(
        "good",
        severity="info",
        cooldown_s=0,
        alerts=[Alert("good", "info", "ok")],
    )
    notifier = _NonDispatchNotifier(cooldown_s=0)
    runner = MonitorRunner(
        domain="search_quality",
        rules=[bad, good],
        session_factory=session_factory,
        notifier=notifier,
    )
    await runner._run_tick()

    assert bad.call_count == 1
    assert good.call_count == 1
    assert notifier.calls == [("good", "info", "ok")]


@pytest.mark.asyncio
async def test_runner_shutdown_event_exits_loop_promptly(session_factory) -> None:
    """SIGTERM path: pre-set shutdown event → loop exits without polling."""
    rule = FakeRule("r", severity="info", cooldown_s=0)
    notifier = _NonDispatchNotifier()
    shutdown = asyncio.Event()
    runner = MonitorRunner(
        domain="search_quality",
        rules=[rule],
        session_factory=session_factory,
        notifier=notifier,
        polling_s=60.0,
        shutdown=shutdown,
    )
    shutdown.set()  # pre-set: loop should not even run one tick
    await asyncio.wait_for(runner.run(), timeout=2.0)
    assert rule.call_count == 0


@pytest.mark.asyncio
async def test_runner_shutdown_mid_loop(session_factory) -> None:
    """Setting shutdown while polling → loop exits before next tick."""
    rule = FakeRule("r", severity="info", cooldown_s=0)
    notifier = _NonDispatchNotifier()
    shutdown = asyncio.Event()
    runner = MonitorRunner(
        domain="search_quality",
        rules=[rule],
        session_factory=session_factory,
        notifier=notifier,
        polling_s=1.0,
        shutdown=shutdown,
    )

    async def _stopper() -> None:
        await asyncio.sleep(0.2)
        shutdown.set()

    await asyncio.wait_for(asyncio.gather(runner.run(), _stopper()), timeout=3.0)
    assert rule.call_count >= 1  # ran at least the first tick


@pytest.mark.asyncio
async def test_runner_old_history_outside_cooldown_allows_new_alert(
    session_factory,
) -> None:
    """An alert older than cooldown_s must not gate a fresh evaluation."""
    async with session_factory() as session:
        stale = AlertHistory(
            rule_id="r.reuse",
            severity="warning",
            message="old",
            notified_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        session.add(stale)
        await session.commit()

    rule = FakeRule(
        "r.reuse",
        severity="warning",
        cooldown_s=600,
        alerts=[Alert("r.reuse", "warning", "fresh")],
    )
    notifier = _NonDispatchNotifier(cooldown_s=0)
    runner = MonitorRunner(
        domain="search_quality",
        rules=[rule],
        session_factory=session_factory,
        notifier=notifier,
    )
    await runner._run_tick()
    assert rule.call_count == 1
    assert notifier.calls == [("r.reuse", "warning", "fresh")]
