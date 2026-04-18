"""search_quality domain — 5 detection rules.

Tool name convention (MONITOR_DESIGN.md §3.1):
- ``search`` — hybrid FTS+vector search
- ``feedback_knowledge`` — user feedback on a search result

Each rule is a pure async function with the unified signature

    async def evaluate(session, config) -> RuleResult | None

Returning ``None`` means "below threshold". The runner (task #52 B4) is
responsible for cooldown / suppression / notification — rules do not touch
``alert_history`` or ``mon_rule_state``.

Empty / top1 extraction: ``MonitorEvent.result_meta`` is a JSON blob emitted by
the collector's ``record_payload``. When the key is missing (e.g. tools not
yet wired for Phase-B keys) the sample is treated as neutral, not as a zero.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mnemo.monitor.models import MonitorEvent
from mnemo.monitor.rules import RuleConfig, RuleResult


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


def _is_empty_search(meta: dict[str, Any]) -> bool:
    """An event is "empty" when result_meta explicitly reports zero hits.

    ``empty_result=true`` (Phase-B key) wins if present; otherwise ``hits==0``
    (collector convention, see queries.py). A missing key is NOT empty — we
    don't want rules to false-fire on legacy events that never set it.
    """
    if meta.get("empty_result") is True:
        return True
    hits = meta.get("hits")
    return isinstance(hits, int) and hits == 0


async def _search_events_in_window(
    session: AsyncSession, window_s: int
) -> list[MonitorEvent]:
    since = _now() - timedelta(seconds=window_s)
    stmt = (
        select(MonitorEvent)
        .where(MonitorEvent.tool_name == "search")
        .where(MonitorEvent.created_at >= since)
        .order_by(MonitorEvent.created_at.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


# ---------------------------------------------------------------------------
# Rule 1 — empty_streak
# ---------------------------------------------------------------------------

async def evaluate_empty_streak(
    session: AsyncSession, config: RuleConfig
) -> RuleResult | None:
    """Latest N search events are all empty → warning.

    Global scope (not per-session): the design doc's per-session variant is an
    enrichment; here we focus on the straightforward "the last N searches
    returned nothing" signal. Ties up with the suppression chain downstream —
    if ``health.no_writes`` or ``health.stale_ratio`` is already firing, the
    runner will suppress this one.
    """
    n = max(1, config.mon_search_empty_streak_n)
    stmt = (
        select(MonitorEvent)
        .where(MonitorEvent.tool_name == "search")
        .order_by(MonitorEvent.created_at.desc())
        .limit(n)
    )
    events = list((await session.execute(stmt)).scalars().all())
    if len(events) < n:
        return None

    for ev in events:
        if not _is_empty_search(_parse_meta(ev.result_meta)):
            return None

    return RuleResult(
        rule_id="search.empty_streak",
        severity="warning",
        message=f"last {n} searches returned zero results",
        details={"streak": n, "latest_event_id": events[0].id},
    )


# ---------------------------------------------------------------------------
# Rule 2 — low_top1
# ---------------------------------------------------------------------------

async def evaluate_low_top1(
    session: AsyncSession, config: RuleConfig
) -> RuleResult | None:
    """Recent search events' top-1 cosine is persistently below threshold.

    Looks at the last ``min_samples`` events with a top1_score present; if
    *all* of them are below ``threshold``, fire. Events without the key are
    skipped, not counted — avoids penalising legacy data.
    """
    threshold = config.mon_search_low_top1
    min_samples = max(1, config.mon_search_low_top1_min_samples)

    stmt = (
        select(MonitorEvent)
        .where(MonitorEvent.tool_name == "search")
        .order_by(MonitorEvent.created_at.desc())
        .limit(min_samples * 4)  # over-fetch; skip null top1
    )
    events = list((await session.execute(stmt)).scalars().all())

    scores: list[float] = []
    for ev in events:
        meta = _parse_meta(ev.result_meta)
        val = meta.get("top1_score")
        if isinstance(val, (int, float)):
            scores.append(float(val))
            if len(scores) >= min_samples:
                break
    if len(scores) < min_samples:
        return None
    if max(scores) >= threshold:
        return None

    return RuleResult(
        rule_id="search.low_top1",
        severity="info",
        message=(
            f"top-1 cosine below {threshold} across last "
            f"{len(scores)} search samples (max={max(scores):.3f})"
        ),
        details={
            "threshold": threshold,
            "samples": len(scores),
            "max_score": max(scores),
        },
    )


# ---------------------------------------------------------------------------
# Rule 3 — loop_suspect
# ---------------------------------------------------------------------------

async def evaluate_loop_suspect(
    session: AsyncSession, config: RuleConfig
) -> RuleResult | None:
    """Same query digest repeated ≥N times within window seconds.

    Uses ``args_digest`` as the query identity (collector fills this for
    search tool in task #55). Falls back to the raw params JSON when digest
    is NULL so the rule still works with legacy events.
    """
    events = await _search_events_in_window(session, config.mon_search_loop_window_s)
    if not events:
        return None

    counts: dict[tuple[str | None, str], int] = defaultdict(int)
    for ev in events:
        identity = ev.args_digest or ev.params_json
        counts[(ev.session_id, identity)] += 1

    offenders = [
        (key, count)
        for key, count in counts.items()
        if count >= config.mon_search_loop_repeat_n
    ]
    if not offenders:
        return None

    offenders.sort(key=lambda kv: kv[1], reverse=True)
    (session_id, identity), peak = offenders[0]
    return RuleResult(
        rule_id="search.loop_suspect",
        severity="warning",
        message=(
            f"same query repeated {peak} times in "
            f"{config.mon_search_loop_window_s}s (session={session_id})"
        ),
        details={
            "session_id": session_id,
            "repeat_count": peak,
            "window_s": config.mon_search_loop_window_s,
            "offender_count": len(offenders),
            "identity_preview": (identity or "")[:80],
        },
    )


# ---------------------------------------------------------------------------
# Rule 4 — latency_sustained
# ---------------------------------------------------------------------------

async def evaluate_latency_sustained(
    session: AsyncSession, config: RuleConfig
) -> RuleResult | None:
    """Last N consecutive search events all exceeded latency threshold.

    "Sustained" is a streak in the task spec, not a p95 — that keeps the rule
    sensitive to pure degradation while remaining robust to single spikes.
    """
    streak_n = max(1, config.mon_search_latency_streak_n)
    threshold = config.mon_search_latency_ms

    stmt = (
        select(MonitorEvent)
        .where(MonitorEvent.tool_name == "search")
        .order_by(MonitorEvent.created_at.desc())
        .limit(streak_n)
    )
    events = list((await session.execute(stmt)).scalars().all())
    if len(events) < streak_n:
        return None
    if any(ev.latency_ms <= threshold for ev in events):
        return None

    latencies = [ev.latency_ms for ev in events]
    return RuleResult(
        rule_id="search.latency_sustained",
        severity="critical",
        message=(
            f"last {streak_n} search latencies all > {threshold}ms "
            f"(min={min(latencies):.0f}, max={max(latencies):.0f})"
        ),
        details={
            "threshold_ms": threshold,
            "streak": streak_n,
            "min_latency_ms": min(latencies),
            "max_latency_ms": max(latencies),
        },
    )


# ---------------------------------------------------------------------------
# Rule 5 — no_follow_up_feedback
# ---------------------------------------------------------------------------

async def evaluate_no_follow_up_feedback(
    session: AsyncSession, config: RuleConfig
) -> RuleResult | None:
    """Search produced non-empty results but window has zero feedback events.

    "Has results" = at least ``min_hits`` non-empty search events in window.
    Triggers when feedback_knowledge count is zero in the same window —
    signals the agent is consuming search output without marking anything
    correct/misleading, which breaks the feedback loop.
    """
    since = _now() - timedelta(seconds=config.mon_search_no_feedback_window_s)

    stmt = (
        select(MonitorEvent)
        .where(MonitorEvent.tool_name.in_(("search", "feedback_knowledge")))
        .where(MonitorEvent.created_at >= since)
    )
    events = list((await session.execute(stmt)).scalars().all())

    non_empty_searches = 0
    feedback_count = 0
    for ev in events:
        if ev.tool_name == "feedback_knowledge":
            feedback_count += 1
            continue
        if ev.tool_name == "search" and not _is_empty_search(_parse_meta(ev.result_meta)):
            non_empty_searches += 1

    if non_empty_searches < config.mon_search_no_feedback_min_hits:
        return None
    if feedback_count > 0:
        return None

    return RuleResult(
        rule_id="search.no_follow_up_feedback",
        severity="info",
        message=(
            f"{non_empty_searches} productive searches in "
            f"{config.mon_search_no_feedback_window_s}s but zero feedback events"
        ),
        details={
            "window_s": config.mon_search_no_feedback_window_s,
            "non_empty_searches": non_empty_searches,
            "feedback_events": feedback_count,
        },
    )


RULES: tuple = (
    evaluate_empty_streak,
    evaluate_low_top1,
    evaluate_loop_suspect,
    evaluate_latency_sustained,
    evaluate_no_follow_up_feedback,
)

__all__ = [
    "RULES",
    "evaluate_empty_streak",
    "evaluate_latency_sustained",
    "evaluate_loop_suspect",
    "evaluate_low_top1",
    "evaluate_no_follow_up_feedback",
]
