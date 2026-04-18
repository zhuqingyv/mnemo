"""Unit tests for behavior_compliance rules (3 rules)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mnemo.monitor.rules import RuleConfig
from mnemo.monitor.rules.behavior_compliance import (
    evaluate_create_without_search,
    evaluate_long_idle_no_search,
    evaluate_misleading_no_update,
)
from tests.monitor.conftest import make_event


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# long_idle_no_search
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_long_idle_fires_on_writes_without_search(rules_session: AsyncSession) -> None:
    config = RuleConfig(
        mon_behavior_idle_no_search_window_s=7200,
        mon_behavior_idle_min_non_search_calls=3,
    )
    now = _now()
    for i, tool in enumerate(("create_knowledge", "feedback_knowledge", "update_knowledge")):
        rules_session.add(
            make_event(tool=tool, created_at=now - timedelta(minutes=i * 5), session_id="sess-A")
        )
    await rules_session.commit()

    result = await evaluate_long_idle_no_search(rules_session, config)
    assert result is not None
    assert result.details["session_id"] == "sess-A"
    assert result.details["write_count"] == 3


@pytest.mark.asyncio
async def test_long_idle_silent_when_search_present(rules_session: AsyncSession) -> None:
    config = RuleConfig(
        mon_behavior_idle_no_search_window_s=7200,
        mon_behavior_idle_min_non_search_calls=3,
    )
    now = _now()
    for i in range(3):
        rules_session.add(
            make_event(tool="create_knowledge", created_at=now - timedelta(minutes=i), session_id="sess-A")
        )
    rules_session.add(
        make_event(tool="search", created_at=now, session_id="sess-A")
    )
    await rules_session.commit()

    assert await evaluate_long_idle_no_search(rules_session, config) is None


# ---------------------------------------------------------------------------
# misleading_no_update
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_misleading_no_update_fires(rules_session: AsyncSession) -> None:
    config = RuleConfig(
        mon_behavior_misleading_no_update_days=30,
        mon_behavior_misleading_no_update_threshold=3,
    )
    now = _now()
    for i in range(3):
        rules_session.add(
            make_event(
                tool="feedback_knowledge",
                created_at=now - timedelta(days=i + 1),
                knowledge_id=7,
                result_meta={"signal": "misleading"},
            )
        )
    await rules_session.commit()

    result = await evaluate_misleading_no_update(rules_session, config)
    assert result is not None
    assert result.details["knowledge_id"] == 7
    assert result.details["misleading_count"] == 3


@pytest.mark.asyncio
async def test_misleading_no_update_silent_when_update_follows(rules_session: AsyncSession) -> None:
    config = RuleConfig(
        mon_behavior_misleading_no_update_days=30,
        mon_behavior_misleading_no_update_threshold=3,
    )
    now = _now()
    for i in range(3):
        rules_session.add(
            make_event(
                tool="feedback_knowledge",
                created_at=now - timedelta(days=i + 2),
                knowledge_id=7,
                result_meta={"signal": "misleading"},
            )
        )
    # Update fires after the earliest misleading
    rules_session.add(
        make_event(tool="update_knowledge", created_at=now - timedelta(days=1), knowledge_id=7)
    )
    await rules_session.commit()

    assert await evaluate_misleading_no_update(rules_session, config) is None


# ---------------------------------------------------------------------------
# create_without_search
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_without_search_fires(rules_session: AsyncSession) -> None:
    config = RuleConfig(mon_behavior_create_presearch_window_s=60)
    now = _now()
    rules_session.add(
        make_event(tool="create_knowledge", created_at=now, session_id="sess-A", knowledge_id=99)
    )
    await rules_session.commit()

    result = await evaluate_create_without_search(rules_session, config)
    assert result is not None
    assert result.details["session_id"] == "sess-A"
    assert result.details["knowledge_id"] == 99


@pytest.mark.asyncio
async def test_create_without_search_silent_with_preceding_search(rules_session: AsyncSession) -> None:
    config = RuleConfig(mon_behavior_create_presearch_window_s=60)
    now = _now()
    rules_session.add(
        make_event(tool="search", created_at=now - timedelta(seconds=10), session_id="sess-A")
    )
    rules_session.add(
        make_event(tool="create_knowledge", created_at=now, session_id="sess-A", knowledge_id=99)
    )
    await rules_session.commit()

    assert await evaluate_create_without_search(rules_session, config) is None
