"""Unit tests for search_quality rules.

Each rule: one positive (fires) + one negative (stays silent) case.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mnemo.monitor.rules import RuleConfig
from mnemo.monitor.rules.search_quality import (
    evaluate_empty_streak,
    evaluate_latency_sustained,
    evaluate_loop_suspect,
    evaluate_low_top1,
    evaluate_no_follow_up_feedback,
)
from tests.monitor.conftest import make_event


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# empty_streak
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_streak_fires_when_last_n_all_empty(rules_session: AsyncSession) -> None:
    config = RuleConfig(mon_search_empty_streak_n=3)
    now = _now()
    for i in range(3):
        rules_session.add(
            make_event(
                tool="search",
                created_at=now - timedelta(seconds=10 - i),
                result_meta={"hits": 0},
            )
        )
    await rules_session.commit()

    result = await evaluate_empty_streak(rules_session, config)
    assert result is not None
    assert result.rule_id == "search.empty_streak"
    assert result.severity == "warning"
    assert result.details["streak"] == 3


@pytest.mark.asyncio
async def test_empty_streak_silent_when_one_hit(rules_session: AsyncSession) -> None:
    config = RuleConfig(mon_search_empty_streak_n=3)
    now = _now()
    rules_session.add_all([
        make_event(tool="search", created_at=now - timedelta(seconds=30), result_meta={"hits": 0}),
        make_event(tool="search", created_at=now - timedelta(seconds=20), result_meta={"hits": 2}),
        make_event(tool="search", created_at=now - timedelta(seconds=10), result_meta={"hits": 0}),
    ])
    await rules_session.commit()

    assert await evaluate_empty_streak(rules_session, config) is None


@pytest.mark.asyncio
async def test_empty_streak_silent_when_not_enough_samples(rules_session: AsyncSession) -> None:
    """<N events in DB → no decision, don't fire prematurely."""
    config = RuleConfig(mon_search_empty_streak_n=3)
    rules_session.add(
        make_event(tool="search", created_at=_now(), result_meta={"hits": 0})
    )
    await rules_session.commit()
    assert await evaluate_empty_streak(rules_session, config) is None


# ---------------------------------------------------------------------------
# low_top1
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_low_top1_fires_when_all_scores_below(rules_session: AsyncSession) -> None:
    config = RuleConfig(mon_search_low_top1=0.5, mon_search_low_top1_min_samples=3)
    now = _now()
    for i, score in enumerate([0.2, 0.3, 0.4]):
        rules_session.add(
            make_event(
                tool="search",
                created_at=now - timedelta(seconds=10 - i),
                result_meta={"top1_score": score, "hits": 1},
            )
        )
    await rules_session.commit()

    result = await evaluate_low_top1(rules_session, config)
    assert result is not None
    assert result.severity == "info"
    assert result.details["max_score"] == pytest.approx(0.4)


@pytest.mark.asyncio
async def test_low_top1_silent_when_one_score_passes(rules_session: AsyncSession) -> None:
    config = RuleConfig(mon_search_low_top1=0.5, mon_search_low_top1_min_samples=3)
    now = _now()
    for i, score in enumerate([0.2, 0.8, 0.3]):
        rules_session.add(
            make_event(
                tool="search",
                created_at=now - timedelta(seconds=10 - i),
                result_meta={"top1_score": score, "hits": 1},
            )
        )
    await rules_session.commit()

    assert await evaluate_low_top1(rules_session, config) is None


# ---------------------------------------------------------------------------
# loop_suspect
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_loop_suspect_fires_on_repeated_digest(rules_session: AsyncSession) -> None:
    config = RuleConfig(mon_search_loop_window_s=300, mon_search_loop_repeat_n=3)
    now = _now()
    for i in range(3):
        rules_session.add(
            make_event(
                tool="search",
                created_at=now - timedelta(seconds=30 - i * 10),
                args_digest="abc123",
                result_meta={"hits": 5},
            )
        )
    await rules_session.commit()

    result = await evaluate_loop_suspect(rules_session, config)
    assert result is not None
    assert result.details["repeat_count"] == 3
    assert result.details["session_id"] == "sess-1"


@pytest.mark.asyncio
async def test_loop_suspect_silent_when_digests_differ(rules_session: AsyncSession) -> None:
    config = RuleConfig(mon_search_loop_window_s=300, mon_search_loop_repeat_n=3)
    now = _now()
    for i, digest in enumerate(["a", "b", "c"]):
        rules_session.add(
            make_event(
                tool="search",
                created_at=now - timedelta(seconds=30 - i * 10),
                args_digest=digest,
            )
        )
    await rules_session.commit()

    assert await evaluate_loop_suspect(rules_session, config) is None


# ---------------------------------------------------------------------------
# latency_sustained
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_latency_sustained_fires_on_streak(rules_session: AsyncSession) -> None:
    config = RuleConfig(mon_search_latency_ms=500, mon_search_latency_streak_n=5)
    now = _now()
    for i in range(5):
        rules_session.add(
            make_event(
                tool="search",
                created_at=now - timedelta(seconds=10 - i),
                latency_ms=900.0,
            )
        )
    await rules_session.commit()

    result = await evaluate_latency_sustained(rules_session, config)
    assert result is not None
    assert result.severity == "critical"
    assert result.details["min_latency_ms"] == 900.0


@pytest.mark.asyncio
async def test_latency_sustained_silent_with_one_fast(rules_session: AsyncSession) -> None:
    config = RuleConfig(mon_search_latency_ms=500, mon_search_latency_streak_n=5)
    now = _now()
    latencies = [900.0, 900.0, 100.0, 900.0, 900.0]
    for i, lat in enumerate(latencies):
        rules_session.add(
            make_event(
                tool="search",
                created_at=now - timedelta(seconds=10 - i),
                latency_ms=lat,
            )
        )
    await rules_session.commit()

    assert await evaluate_latency_sustained(rules_session, config) is None


# ---------------------------------------------------------------------------
# no_follow_up_feedback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_follow_up_feedback_fires_when_zero_feedback(rules_session: AsyncSession) -> None:
    config = RuleConfig(
        mon_search_no_feedback_window_s=3600, mon_search_no_feedback_min_hits=3
    )
    now = _now()
    for i in range(4):
        rules_session.add(
            make_event(
                tool="search",
                created_at=now - timedelta(minutes=30 - i),
                result_meta={"hits": 5},
            )
        )
    await rules_session.commit()

    result = await evaluate_no_follow_up_feedback(rules_session, config)
    assert result is not None
    assert result.details["non_empty_searches"] == 4
    assert result.details["feedback_events"] == 0


@pytest.mark.asyncio
async def test_no_follow_up_feedback_silent_when_feedback_present(
    rules_session: AsyncSession,
) -> None:
    config = RuleConfig(
        mon_search_no_feedback_window_s=3600, mon_search_no_feedback_min_hits=3
    )
    now = _now()
    for i in range(4):
        rules_session.add(
            make_event(
                tool="search",
                created_at=now - timedelta(minutes=30 - i),
                result_meta={"hits": 5},
            )
        )
    rules_session.add(make_event(tool="feedback_knowledge", created_at=now))
    await rules_session.commit()

    assert await evaluate_no_follow_up_feedback(rules_session, config) is None
