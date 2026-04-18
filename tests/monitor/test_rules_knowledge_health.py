"""Unit tests for knowledge_health rules (7 rules)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mnemo.models.knowledge import Knowledge
from mnemo.monitor.rules import RuleConfig
from mnemo.monitor.rules.knowledge_health import (
    evaluate_evidence_weak_spike,
    evaluate_gate_supersede_spike,
    evaluate_high_empty_ratio,
    evaluate_misleading_threshold,
    evaluate_monitor_backpressure,
    evaluate_no_writes,
    evaluate_stale_ratio,
)
from tests.monitor.conftest import make_event


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _k(*, title: str, status: str) -> Knowledge:
    return Knowledge(
        title=title,
        summary="",
        content="",
        tags="[]",
        scope="global",
        status=status,
    )


# ---------------------------------------------------------------------------
# stale_ratio
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stale_ratio_fires_over_threshold(rules_session: AsyncSession) -> None:
    config = RuleConfig(mon_health_stale_ratio=0.4)
    rules_session.add_all([
        _k(title="a", status="stale"),
        _k(title="b", status="stale"),
        _k(title="c", status="active"),
    ])
    await rules_session.commit()

    result = await evaluate_stale_ratio(rules_session, config)
    assert result is not None
    assert result.details["stale"] == 2
    assert result.details["ratio"] == pytest.approx(2 / 3)


@pytest.mark.asyncio
async def test_stale_ratio_silent_under_threshold(rules_session: AsyncSession) -> None:
    config = RuleConfig(mon_health_stale_ratio=0.4)
    rules_session.add_all([
        _k(title="a", status="active"),
        _k(title="b", status="active"),
        _k(title="c", status="active"),
        _k(title="d", status="stale"),
    ])
    await rules_session.commit()
    assert await evaluate_stale_ratio(rules_session, config) is None


@pytest.mark.asyncio
async def test_stale_ratio_silent_on_empty_corpus(rules_session: AsyncSession) -> None:
    assert await evaluate_stale_ratio(rules_session, RuleConfig()) is None


# ---------------------------------------------------------------------------
# no_writes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_writes_fires_when_stale(rules_session: AsyncSession) -> None:
    config = RuleConfig(mon_health_no_writes_days=7)
    rules_session.add(_k(title="a", status="active"))
    now = _now()
    rules_session.add(
        make_event(
            tool="create_knowledge",
            created_at=now - timedelta(days=15),
            status="ok",
        )
    )
    await rules_session.commit()

    result = await evaluate_no_writes(rules_session, config)
    assert result is not None
    assert result.details["days_since"] > 7


@pytest.mark.asyncio
async def test_no_writes_silent_with_recent_write(rules_session: AsyncSession) -> None:
    config = RuleConfig(mon_health_no_writes_days=7)
    rules_session.add(_k(title="a", status="active"))
    rules_session.add(
        make_event(
            tool="update_knowledge",
            created_at=_now() - timedelta(days=1),
            status="ok",
        )
    )
    await rules_session.commit()
    assert await evaluate_no_writes(rules_session, config) is None


@pytest.mark.asyncio
async def test_no_writes_silent_on_empty_db(rules_session: AsyncSession) -> None:
    assert await evaluate_no_writes(rules_session, RuleConfig()) is None


# ---------------------------------------------------------------------------
# high_empty_ratio
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_high_empty_ratio_fires(rules_session: AsyncSession) -> None:
    config = RuleConfig(
        mon_health_empty_ratio_threshold=0.5,
        mon_health_empty_ratio_min_samples=4,
    )
    now = _now()
    # 3 empty, 1 non-empty → 0.75 ≥ 0.5
    for i in range(3):
        rules_session.add(
            make_event(tool="search", created_at=now - timedelta(minutes=i * 5), result_meta={"hits": 0})
        )
    rules_session.add(make_event(tool="search", created_at=now, result_meta={"hits": 3}))
    await rules_session.commit()

    result = await evaluate_high_empty_ratio(rules_session, config)
    assert result is not None
    assert result.details["ratio"] == pytest.approx(0.75)


@pytest.mark.asyncio
async def test_high_empty_ratio_silent_below_threshold(rules_session: AsyncSession) -> None:
    config = RuleConfig(
        mon_health_empty_ratio_threshold=0.5,
        mon_health_empty_ratio_min_samples=4,
    )
    now = _now()
    for i in range(4):
        rules_session.add(make_event(tool="search", created_at=now - timedelta(minutes=i), result_meta={"hits": 5}))
    await rules_session.commit()
    assert await evaluate_high_empty_ratio(rules_session, config) is None


# ---------------------------------------------------------------------------
# monitor_backpressure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backpressure_fires_on_error_events(rules_session: AsyncSession) -> None:
    config = RuleConfig(mon_health_backpressure_dropped_min=2)
    now = _now()
    for i in range(3):
        rules_session.add(
            make_event(
                tool="search",
                created_at=now - timedelta(minutes=i),
                status="error",
            )
        )
    await rules_session.commit()

    result = await evaluate_monitor_backpressure(rules_session, config)
    assert result is not None
    assert result.details["error_count"] == 3


@pytest.mark.asyncio
async def test_backpressure_silent_when_all_ok(rules_session: AsyncSession) -> None:
    config = RuleConfig(mon_health_backpressure_dropped_min=1)
    rules_session.add(make_event(tool="search", created_at=_now(), status="ok"))
    await rules_session.commit()
    assert await evaluate_monitor_backpressure(rules_session, config) is None


# ---------------------------------------------------------------------------
# feedback.misleading_threshold
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_misleading_threshold_fires(rules_session: AsyncSession) -> None:
    config = RuleConfig(
        mon_feedback_misleading_threshold=3, mon_feedback_misleading_window_days=30
    )
    now = _now()
    for i in range(3):
        rules_session.add(
            make_event(
                tool="feedback_knowledge",
                created_at=now - timedelta(days=i),
                knowledge_id=42,
                result_meta={"signal": "misleading"},
            )
        )
    await rules_session.commit()

    result = await evaluate_misleading_threshold(rules_session, config)
    assert result is not None
    assert result.details["knowledge_id"] == 42
    assert result.details["count"] == 3


@pytest.mark.asyncio
async def test_misleading_threshold_silent_when_other_signal(rules_session: AsyncSession) -> None:
    config = RuleConfig(mon_feedback_misleading_threshold=3)
    for i in range(3):
        rules_session.add(
            make_event(
                tool="feedback_knowledge",
                created_at=_now() - timedelta(days=i),
                knowledge_id=42,
                result_meta={"signal": "correct"},
            )
        )
    await rules_session.commit()
    assert await evaluate_misleading_threshold(rules_session, config) is None


# ---------------------------------------------------------------------------
# write.gate_supersede_spike
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_supersede_spike_fires(rules_session: AsyncSession) -> None:
    config = RuleConfig(
        mon_write_gate_supersede_ratio=0.4, mon_write_gate_supersede_min_samples=5
    )
    now = _now()
    for i in range(3):
        rules_session.add(
            make_event(
                tool="create_knowledge",
                created_at=now - timedelta(minutes=i),
                result_meta={"recommended_action": "supersede"},
            )
        )
    for i in range(2):
        rules_session.add(
            make_event(
                tool="create_knowledge",
                created_at=now - timedelta(minutes=10 + i),
                result_meta={"recommended_action": "insert"},
            )
        )
    await rules_session.commit()

    result = await evaluate_gate_supersede_spike(rules_session, config)
    assert result is not None
    assert result.details["supersede_count"] == 3


@pytest.mark.asyncio
async def test_supersede_spike_silent_under_samples(rules_session: AsyncSession) -> None:
    config = RuleConfig(
        mon_write_gate_supersede_ratio=0.4, mon_write_gate_supersede_min_samples=5
    )
    rules_session.add(
        make_event(
            tool="create_knowledge",
            created_at=_now(),
            result_meta={"recommended_action": "supersede"},
        )
    )
    await rules_session.commit()
    assert await evaluate_gate_supersede_spike(rules_session, config) is None


# ---------------------------------------------------------------------------
# write.evidence_weak_spike
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evidence_weak_spike_fires(rules_session: AsyncSession) -> None:
    config = RuleConfig(
        mon_write_evidence_weak_ratio=0.3, mon_write_evidence_weak_min_samples=5
    )
    now = _now()
    for i in range(3):
        rules_session.add(
            make_event(
                tool="create_knowledge",
                created_at=now - timedelta(minutes=i),
                result_meta={"evidence_weak_reason": "short_content"},
            )
        )
    for i in range(3):
        rules_session.add(
            make_event(
                tool="create_knowledge",
                created_at=now - timedelta(minutes=10 + i),
                result_meta={},
            )
        )
    await rules_session.commit()

    result = await evaluate_evidence_weak_spike(rules_session, config)
    assert result is not None
    assert result.details["weak_count"] == 3


@pytest.mark.asyncio
async def test_evidence_weak_spike_silent_all_strong(rules_session: AsyncSession) -> None:
    config = RuleConfig(
        mon_write_evidence_weak_ratio=0.3, mon_write_evidence_weak_min_samples=3
    )
    for i in range(4):
        rules_session.add(
            make_event(
                tool="create_knowledge",
                created_at=_now() - timedelta(minutes=i),
                result_meta={},
            )
        )
    await rules_session.commit()
    assert await evaluate_evidence_weak_spike(rules_session, config) is None
