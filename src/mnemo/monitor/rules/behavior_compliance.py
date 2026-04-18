"""behavior_compliance domain — 3 detection rules.

Inferred from the agent's tool-call timing patterns (no agent self-report
needed). Signals violations of the "agent using mnemo correctly" workflow.

Same ``evaluate(session, config) -> RuleResult | None`` contract. These rules
are intentionally per-session / per-knowledge scoped — they say "this agent
behaviour is off" rather than "the system is degraded".
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mnemo.monitor.models import MonitorEvent
from mnemo.monitor.rules import RuleConfig, RuleResult

_WRITE_TOOLS = ("create_knowledge", "feedback_knowledge", "update_knowledge")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_meta(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Rule 1 — behavior.long_idle_no_search
# ---------------------------------------------------------------------------

async def evaluate_long_idle_no_search(
    session: AsyncSession, config: RuleConfig
) -> RuleResult | None:
    """A session ran ≥N writes in the window but made zero searches.

    Writes-without-reads is the canonical "agent forgot to consult mnemo"
    anti-pattern. Scoped per session — global scope would be meaningless
    because different agents can legitimately write while others search.
    """
    window_s = config.mon_behavior_idle_no_search_window_s
    since = _now() - timedelta(seconds=window_s)

    stmt = (
        select(MonitorEvent)
        .where(MonitorEvent.created_at >= since)
        .where(MonitorEvent.session_id.is_not(None))
    )
    events = list((await session.execute(stmt)).scalars().all())
    if not events:
        return None

    per_session: dict[str, dict[str, int]] = defaultdict(lambda: {"search": 0, "write": 0})
    for ev in events:
        bucket = per_session[ev.session_id]  # type: ignore[index]
        if ev.tool_name == "search":
            bucket["search"] += 1
        elif ev.tool_name in _WRITE_TOOLS:
            bucket["write"] += 1

    offenders = [
        (sid, stats["write"])
        for sid, stats in per_session.items()
        if stats["write"] >= config.mon_behavior_idle_min_non_search_calls
        and stats["search"] == 0
    ]
    if not offenders:
        return None

    offenders.sort(key=lambda kv: kv[1], reverse=True)
    sid, writes = offenders[0]
    return RuleResult(
        rule_id="behavior.long_idle_no_search",
        severity="warning",
        message=(
            f"session {sid} performed {writes} writes in "
            f"{window_s}s with zero searches"
        ),
        details={
            "session_id": sid,
            "write_count": writes,
            "window_s": window_s,
            "offender_count": len(offenders),
        },
    )


# ---------------------------------------------------------------------------
# Rule 2 — behavior.misleading_no_update
# ---------------------------------------------------------------------------

async def evaluate_misleading_no_update(
    session: AsyncSession, config: RuleConfig
) -> RuleResult | None:
    """Knowledge flagged misleading ≥N times but has no follow-up update.

    Window: ``mon_behavior_misleading_no_update_days``. "Update" = a
    successful ``update_knowledge`` / ``archive_knowledge`` event on the same
    knowledge_id after the first misleading flag in the window.
    """
    days = config.mon_behavior_misleading_no_update_days
    threshold = config.mon_behavior_misleading_no_update_threshold
    since = _now() - timedelta(days=days)

    stmt = (
        select(MonitorEvent)
        .where(MonitorEvent.created_at >= since)
        .where(MonitorEvent.status == "ok")
        .where(MonitorEvent.knowledge_id.is_not(None))
    )
    events = list((await session.execute(stmt)).scalars().all())
    if not events:
        return None

    mislead_events: dict[int, list[datetime]] = defaultdict(list)
    update_events: dict[int, list[datetime]] = defaultdict(list)
    for ev in events:
        kid = ev.knowledge_id
        if kid is None:
            continue
        created = ev.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if ev.tool_name == "feedback_knowledge":
            if _parse_meta(ev.result_meta).get("signal") == "misleading":
                mislead_events[kid].append(created)
        elif ev.tool_name in ("update_knowledge", "archive_knowledge"):
            update_events[kid].append(created)

    offenders = []
    for kid, ts in mislead_events.items():
        if len(ts) < threshold:
            continue
        first_flag = min(ts)
        later_update = any(u > first_flag for u in update_events.get(kid, ()))
        if not later_update:
            offenders.append((kid, len(ts)))
    if not offenders:
        return None

    offenders.sort(key=lambda kv: kv[1], reverse=True)
    kid, count = offenders[0]
    return RuleResult(
        rule_id="behavior.misleading_no_update",
        severity="warning",
        message=(
            f"knowledge {kid} flagged misleading {count} times "
            f"in last {days}d without any update/archive"
        ),
        details={
            "knowledge_id": kid,
            "misleading_count": count,
            "window_days": days,
            "offender_count": len(offenders),
        },
    )


# ---------------------------------------------------------------------------
# Rule 3 — behavior.create_without_search
# ---------------------------------------------------------------------------

async def evaluate_create_without_search(
    session: AsyncSession, config: RuleConfig
) -> RuleResult | None:
    """A create_knowledge event fires without a preceding same-session search.

    "Preceding" = within ``presearch_window_s`` seconds in the same session.
    Scans the most recent create events and rejects them if no search exists
    in that session's pre-window. Fires on the freshest offender only — the
    runner tracks cooldown, not the rule.
    """
    window_s = config.mon_behavior_create_presearch_window_s

    stmt = (
        select(MonitorEvent)
        .where(MonitorEvent.tool_name == "create_knowledge")
        .where(MonitorEvent.status == "ok")
        .where(MonitorEvent.session_id.is_not(None))
        .order_by(MonitorEvent.created_at.desc())
        .limit(25)
    )
    creates = list((await session.execute(stmt)).scalars().all())
    if not creates:
        return None

    for create in creates:
        create_ts = create.created_at
        if create_ts.tzinfo is None:
            create_ts = create_ts.replace(tzinfo=timezone.utc)
        pre_start = create_ts - timedelta(seconds=window_s)

        search_stmt = select(func.count(MonitorEvent.id)).where(
            MonitorEvent.tool_name == "search",
            MonitorEvent.session_id == create.session_id,
            MonitorEvent.created_at >= pre_start,
            MonitorEvent.created_at < create_ts,
        )
        search_count = int((await session.execute(search_stmt)).scalar_one() or 0)
        if search_count == 0:
            return RuleResult(
                rule_id="behavior.create_without_search",
                severity="info",
                message=(
                    f"session {create.session_id} created knowledge "
                    f"{create.knowledge_id} without a search in the preceding "
                    f"{window_s}s"
                ),
                details={
                    "session_id": create.session_id,
                    "knowledge_id": create.knowledge_id,
                    "event_id": create.id,
                    "window_s": window_s,
                },
            )

    return None


RULES: tuple = (
    evaluate_long_idle_no_search,
    evaluate_misleading_no_update,
    evaluate_create_without_search,
)

__all__ = [
    "RULES",
    "evaluate_create_without_search",
    "evaluate_long_idle_no_search",
    "evaluate_misleading_no_update",
]
