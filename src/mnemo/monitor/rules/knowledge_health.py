"""knowledge_health domain — 7 detection rules.

Operates on both ``monitor_event`` (ops) and ``knowledge`` (corpus state).
Rules are global (no per-session scoping) — they answer "is the knowledge
base as a whole degrading?".

Same ``evaluate(session, config) -> RuleResult | None`` contract as the other
domains.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mnemo.models.knowledge import Knowledge
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
    if meta.get("empty_result") is True:
        return True
    hits = meta.get("hits")
    return isinstance(hits, int) and hits == 0


# ---------------------------------------------------------------------------
# Rule 1 — health.stale_ratio
# ---------------------------------------------------------------------------

async def evaluate_stale_ratio(
    session: AsyncSession, config: RuleConfig
) -> RuleResult | None:
    """stale rows / (active+stale rows) exceeds threshold.

    Archived rows are excluded from the denominator — they're intentionally
    retired and shouldn't drag the ratio up or down. Returns None when there
    is no live corpus at all (avoids divide-by-zero noise on a fresh DB).
    """
    active_stmt = select(func.count(Knowledge.id)).where(Knowledge.status == "active")
    stale_stmt = select(func.count(Knowledge.id)).where(Knowledge.status == "stale")
    active = int((await session.execute(active_stmt)).scalar_one() or 0)
    stale = int((await session.execute(stale_stmt)).scalar_one() or 0)
    total = active + stale
    if total == 0:
        return None

    ratio = stale / total
    if ratio < config.mon_health_stale_ratio:
        return None

    return RuleResult(
        rule_id="health.stale_ratio",
        severity="info",
        message=(
            f"stale ratio {ratio:.1%} ≥ threshold "
            f"{config.mon_health_stale_ratio:.1%} "
            f"({stale}/{total} live rows are stale)"
        ),
        details={"stale": stale, "active": active, "ratio": ratio},
    )


# ---------------------------------------------------------------------------
# Rule 2 — health.no_writes
# ---------------------------------------------------------------------------

async def evaluate_no_writes(
    session: AsyncSession, config: RuleConfig
) -> RuleResult | None:
    """No successful ``create_knowledge`` / ``update_knowledge`` in N days.

    Uses ``monitor_event`` rather than the knowledge.updated_at column because
    the monitor tool already filters status='ok' and is the operational truth.
    Empty-DB case: returns None — there's nothing to age.
    """
    threshold = _now() - timedelta(days=config.mon_health_no_writes_days)

    # Any knowledge row at all? If DB is empty, suppress.
    has_any = int(
        (await session.execute(select(func.count(Knowledge.id)))).scalar_one() or 0
    )
    if has_any == 0:
        return None

    stmt = (
        select(func.max(MonitorEvent.created_at))
        .where(MonitorEvent.tool_name.in_(("create_knowledge", "update_knowledge")))
        .where(MonitorEvent.status == "ok")
    )
    last_write: datetime | None = (await session.execute(stmt)).scalar_one()
    if last_write is not None and last_write.tzinfo is None:
        last_write = last_write.replace(tzinfo=timezone.utc)

    if last_write is not None and last_write >= threshold:
        return None

    days_since = (
        (_now() - last_write).total_seconds() / 86400 if last_write else None
    )
    return RuleResult(
        rule_id="health.no_writes",
        severity="info",
        message=(
            "no successful writes in last "
            f"{config.mon_health_no_writes_days} days"
        ),
        details={
            "last_write_at": last_write.isoformat() if last_write else None,
            "days_since": days_since,
            "threshold_days": config.mon_health_no_writes_days,
        },
    )


# ---------------------------------------------------------------------------
# Rule 3 — health.high_empty_ratio
# ---------------------------------------------------------------------------

async def evaluate_high_empty_ratio(
    session: AsyncSession, config: RuleConfig
) -> RuleResult | None:
    """Recent window: share of empty searches exceeds threshold."""
    since = _now() - timedelta(hours=24)
    stmt = (
        select(MonitorEvent)
        .where(MonitorEvent.tool_name == "search")
        .where(MonitorEvent.created_at >= since)
    )
    events = list((await session.execute(stmt)).scalars().all())
    if len(events) < config.mon_health_empty_ratio_min_samples:
        return None

    empty = sum(1 for ev in events if _is_empty_search(_parse_meta(ev.result_meta)))
    ratio = empty / len(events)
    if ratio < config.mon_health_empty_ratio_threshold:
        return None

    return RuleResult(
        rule_id="health.high_empty_ratio",
        severity="warning",
        message=(
            f"{ratio:.1%} of last {len(events)} searches returned empty "
            f"(threshold {config.mon_health_empty_ratio_threshold:.1%})"
        ),
        details={
            "samples": len(events),
            "empty": empty,
            "ratio": ratio,
            "threshold": config.mon_health_empty_ratio_threshold,
        },
    )


# ---------------------------------------------------------------------------
# Rule 4 — health.monitor_backpressure
# ---------------------------------------------------------------------------

async def evaluate_monitor_backpressure(
    session: AsyncSession, config: RuleConfig
) -> RuleResult | None:
    """Collector health — too many error-status events recently.

    The collector itself writes a row per tool call including failures. When
    the writer can't keep up OR tool failure rate spikes, this rule fires as
    a proxy. No dedicated ``dropped_count`` column exists in this milestone
    (see models.py MonitorHealth) — so we read status='error' counts in the
    last hour. Threshold: any error count ≥ ``dropped_min`` (default 1).
    """
    since = _now() - timedelta(hours=1)
    stmt = select(func.count(MonitorEvent.id)).where(
        MonitorEvent.status == "error",
        MonitorEvent.created_at >= since,
    )
    error_count = int((await session.execute(stmt)).scalar_one() or 0)
    if error_count < config.mon_health_backpressure_dropped_min:
        return None

    return RuleResult(
        rule_id="health.monitor_backpressure",
        severity="warning",
        message=(
            f"{error_count} tool error events in last hour "
            "— collector or tool layer is unstable"
        ),
        details={"error_count": error_count, "window_s": 3600},
    )


# ---------------------------------------------------------------------------
# Rule 5 — feedback.misleading_threshold
# ---------------------------------------------------------------------------

async def evaluate_misleading_threshold(
    session: AsyncSession, config: RuleConfig
) -> RuleResult | None:
    """Any single knowledge row has ≥N misleading feedback in window.

    ``signal='misleading'`` lives in ``result_meta`` (Phase-B key). Group by
    ``knowledge_id`` column. Events without knowledge_id are skipped.
    """
    since = _now() - timedelta(days=config.mon_feedback_misleading_window_days)
    stmt = (
        select(MonitorEvent)
        .where(MonitorEvent.tool_name == "feedback_knowledge")
        .where(MonitorEvent.created_at >= since)
        .where(MonitorEvent.status == "ok")
    )
    events = list((await session.execute(stmt)).scalars().all())
    if not events:
        return None

    counts: dict[int, int] = {}
    for ev in events:
        if ev.knowledge_id is None:
            continue
        meta = _parse_meta(ev.result_meta)
        if meta.get("signal") != "misleading":
            continue
        counts[ev.knowledge_id] = counts.get(ev.knowledge_id, 0) + 1

    offenders = [
        (kid, n)
        for kid, n in counts.items()
        if n >= config.mon_feedback_misleading_threshold
    ]
    if not offenders:
        return None

    offenders.sort(key=lambda kv: kv[1], reverse=True)
    top_kid, top_count = offenders[0]
    return RuleResult(
        rule_id="feedback.misleading_threshold",
        severity="warning",
        message=(
            f"knowledge {top_kid} accumulated {top_count} misleading flags "
            f"in last {config.mon_feedback_misleading_window_days}d"
        ),
        details={
            "knowledge_id": top_kid,
            "count": top_count,
            "offender_count": len(offenders),
            "threshold": config.mon_feedback_misleading_threshold,
        },
    )


# ---------------------------------------------------------------------------
# Rule 6 — write.gate_supersede_spike
# ---------------------------------------------------------------------------

async def evaluate_gate_supersede_spike(
    session: AsyncSession, config: RuleConfig
) -> RuleResult | None:
    """Recent create_knowledge events lean heavily toward ``supersede``.

    Write-gate decisions are recorded in ``result_meta.recommended_action``.
    A high supersede ratio means the agent keeps trying to re-create rows
    that should have been updates — signals either stale write-gate
    thresholds or the agent not reading search first.
    """
    since = _now() - timedelta(hours=1)
    stmt = (
        select(MonitorEvent)
        .where(MonitorEvent.tool_name == "create_knowledge")
        .where(MonitorEvent.created_at >= since)
    )
    events = list((await session.execute(stmt)).scalars().all())
    if len(events) < config.mon_write_gate_supersede_min_samples:
        return None

    supersede = 0
    for ev in events:
        meta = _parse_meta(ev.result_meta)
        if meta.get("recommended_action") == "supersede":
            supersede += 1
    ratio = supersede / len(events)
    if ratio < config.mon_write_gate_supersede_ratio:
        return None

    return RuleResult(
        rule_id="write.gate_supersede_spike",
        severity="warning",
        message=(
            f"{ratio:.1%} of last {len(events)} create attempts recommended "
            f"supersede (threshold {config.mon_write_gate_supersede_ratio:.1%})"
        ),
        details={
            "samples": len(events),
            "supersede_count": supersede,
            "ratio": ratio,
        },
    )


# ---------------------------------------------------------------------------
# Rule 7 — write.evidence_weak_spike
# ---------------------------------------------------------------------------

async def evaluate_evidence_weak_spike(
    session: AsyncSession, config: RuleConfig
) -> RuleResult | None:
    """Recent create events with weak evidence markers are a large share."""
    since = _now() - timedelta(hours=24)
    stmt = (
        select(MonitorEvent)
        .where(MonitorEvent.tool_name == "create_knowledge")
        .where(MonitorEvent.created_at >= since)
    )
    events = list((await session.execute(stmt)).scalars().all())
    if len(events) < config.mon_write_evidence_weak_min_samples:
        return None

    weak = 0
    for ev in events:
        meta = _parse_meta(ev.result_meta)
        reason = meta.get("evidence_weak_reason")
        if reason:
            weak += 1
    ratio = weak / len(events)
    if ratio < config.mon_write_evidence_weak_ratio:
        return None

    return RuleResult(
        rule_id="write.evidence_weak_spike",
        severity="info",
        message=(
            f"{ratio:.1%} of last {len(events)} creates flagged evidence-weak "
            f"(threshold {config.mon_write_evidence_weak_ratio:.1%})"
        ),
        details={
            "samples": len(events),
            "weak_count": weak,
            "ratio": ratio,
        },
    )


RULES: tuple = (
    evaluate_stale_ratio,
    evaluate_no_writes,
    evaluate_high_empty_ratio,
    evaluate_monitor_backpressure,
    evaluate_misleading_threshold,
    evaluate_gate_supersede_spike,
    evaluate_evidence_weak_spike,
)

__all__ = [
    "RULES",
    "evaluate_evidence_weak_spike",
    "evaluate_gate_supersede_spike",
    "evaluate_high_empty_ratio",
    "evaluate_misleading_threshold",
    "evaluate_monitor_backpressure",
    "evaluate_no_writes",
    "evaluate_stale_ratio",
]
