"""Phase 3 P3a-M4 feedback-loop reader side (TECH_PLAN §5.2 + §5.4).

Pure read path that the rerank hot-loop calls. Three primitives:

* ``get_feedback_counts`` — single-id bucket counts (helpful / misleading /
  outdated) with the 24h per-actor dedup + 30d window already applied.
* ``batch_feedback_counts`` — same as above, but batched for the rerank path.
  Returns ``{kid: (helpful_count, misleading_count)}`` — ``outdated`` is not
  returned because the mult formula only uses helpful/misleading.
* ``get_verification_mult`` — the derived multiplier for a single knowledge id.
  Applies the sample-floor + sigmoid formula and respects the feature flag.

Dedup semantics (spec item 5): when the same ``actor`` has multiple feedback
events on the same knowledge within the last ``feedback_dedup_hours`` (24h by
default), only the most recent event counts.  Events further apart than the
window are treated independently.

Window semantics (spec item 6): only events whose ``created_at`` is within
``feedback_window_days`` (30d) of "now" contribute.

Formula (spec item 3/4): ``verification_mult = 0.7 + 0.6 * sigmoid(h - 2m)``,
floored to 1.0 when ``h + m < 3``.  The misleading weight is 2× — pushing the
pivot to ``h == 2m`` where ``sigmoid(0) = 0.5`` yields exactly 1.0.

Zero new tables — everything reads from ``knowledge_event``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mnemo.config import MnemoConfig
from mnemo.models.knowledge import KnowledgeEvent


FEEDBACK_EVENT_TYPE = "feedback"
FEEDBACK_SIGNALS = ("helpful", "misleading", "outdated")

# Defaults mirror TECH_PLAN §9.5. We do not read them from MnemoConfig yet
# because the fields have not been declared on the model; when they land the
# ``config`` kwargs will supersede these.
DEFAULT_SAMPLE_FLOOR = 3
DEFAULT_MISLEADING_WEIGHT = 2.0
DEFAULT_WINDOW_DAYS = 30
DEFAULT_DEDUP_HOURS = 24
DEFAULT_MULT_LOW = 0.7
DEFAULT_MULT_HIGH = 1.3


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _cfg_int(config: MnemoConfig | None, name: str, default: int) -> int:
    if config is None:
        return default
    return int(getattr(config, name, default))


def _cfg_float(config: MnemoConfig | None, name: str, default: float) -> float:
    if config is None:
        return default
    return float(getattr(config, name, default))


def _parse_signal(payload_json: str | None) -> str | None:
    if not payload_json:
        return None
    try:
        data = json.loads(payload_json)
    except (TypeError, ValueError):
        return None
    signal = data.get("signal") if isinstance(data, dict) else None
    if signal in FEEDBACK_SIGNALS:
        return signal
    return None


def _sigmoid(x: float) -> float:
    # Guard against overflow for large negative inputs — math.exp(1000) raises.
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    ex = math.exp(x)
    return ex / (1.0 + ex)


def _dedup_to_latest(
    events: Iterable[tuple[int | None, str | None, str | None, datetime]],
    dedup_window_s: float,
) -> list[tuple[int | None, str | None, str]]:
    """Collapse (knowledge_id, actor) events within the dedup window to the
    latest one.

    Events outside the window are treated independently.  Expects events to
    be iterable in any order — this function sorts internally.

    Returns ``[(knowledge_id, actor, signal)]`` — the chronological relation
    between events is dropped because callers only need counts.
    """
    # Sort chronologically so the "latest in each 24h bucket" is the last one
    # we see in a given group.  We then walk the events and, for each
    # (kid, actor), collapse any event that sits within ``dedup_window_s`` of
    # the previous kept event *for that pair* into the new one.
    sorted_events = sorted(
        events, key=lambda e: _as_utc(e[3]).timestamp()
    )
    last_kept_ts: dict[tuple[int | None, str | None], float] = {}
    kept_for_pair: dict[tuple[int | None, str | None], int] = {}
    kept: list[tuple[int | None, str | None, str]] = []

    for kid, actor, payload, at in sorted_events:
        signal = _parse_signal(payload)
        if signal is None:
            continue
        ts = _as_utc(at).timestamp()
        key = (kid, actor)
        prev_ts = last_kept_ts.get(key)
        if prev_ts is not None and (ts - prev_ts) < dedup_window_s:
            # Overwrite the previously kept entry with this newer one.
            idx = kept_for_pair[key]
            kept[idx] = (kid, actor, signal)
            last_kept_ts[key] = ts  # latest timestamp in this rolling bucket
            continue
        kept.append((kid, actor, signal))
        kept_for_pair[key] = len(kept) - 1
        last_kept_ts[key] = ts

    return kept


async def _load_window_events(
    session: AsyncSession,
    knowledge_ids: list[int],
    *,
    window_days: int,
    now: datetime | None = None,
) -> list[tuple[int | None, str | None, str | None, datetime]]:
    if not knowledge_ids:
        return []
    reference = _as_utc(now or datetime.now(timezone.utc))
    cutoff = reference - timedelta(days=window_days)
    stmt = select(
        KnowledgeEvent.knowledge_id,
        KnowledgeEvent.actor,
        KnowledgeEvent.payload_json,
        KnowledgeEvent.created_at,
    ).where(
        and_(
            KnowledgeEvent.event_type == FEEDBACK_EVENT_TYPE,
            KnowledgeEvent.knowledge_id.in_(knowledge_ids),
            KnowledgeEvent.created_at >= cutoff,
        )
    )
    rows = (await session.execute(stmt)).all()
    return [(kid, actor, payload, at) for kid, actor, payload, at in rows]


async def get_feedback_counts(
    session: AsyncSession,
    knowledge_id: int,
    *,
    config: MnemoConfig | None = None,
    window_days: int | None = None,
    dedup_hours: int | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Return ``{"helpful": h, "misleading": m, "outdated": o}`` for one id.

    Applies the 24h per-actor dedup + 30d time window.  Keys always present
    even when zero so callers can do ``counts["misleading"]`` safely.
    """
    w_days = window_days if window_days is not None else _cfg_int(
        config, "feedback_window_days", DEFAULT_WINDOW_DAYS
    )
    d_hours = dedup_hours if dedup_hours is not None else _cfg_int(
        config, "feedback_dedup_hours", DEFAULT_DEDUP_HOURS
    )

    events = await _load_window_events(
        session, [knowledge_id], window_days=w_days, now=now
    )
    deduped = _dedup_to_latest(events, dedup_window_s=d_hours * 3600.0)

    counts = {"helpful": 0, "misleading": 0, "outdated": 0}
    for _, _, signal in deduped:
        counts[signal] = counts.get(signal, 0) + 1
    return counts


async def batch_feedback_counts(
    session: AsyncSession,
    ids: Iterable[int],
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    dedup_hours: int = DEFAULT_DEDUP_HOURS,
    config: MnemoConfig | None = None,
    now: datetime | None = None,
) -> dict[int, tuple[int, int]]:
    """Batch variant for the rerank path.

    Returns ``{knowledge_id: (helpful_count, misleading_count)}`` — ids with
    no in-window feedback are omitted (callers treat missing as (0, 0)).
    ``outdated`` is not in the tuple because the verification_mult formula
    only uses helpful/misleading.
    """
    id_list = [int(i) for i in ids]
    if not id_list:
        return {}

    if config is not None:
        window_days = _cfg_int(config, "feedback_window_days", window_days)
        dedup_hours = _cfg_int(config, "feedback_dedup_hours", dedup_hours)

    events = await _load_window_events(
        session, id_list, window_days=window_days, now=now
    )
    deduped = _dedup_to_latest(events, dedup_window_s=dedup_hours * 3600.0)

    out: dict[int, tuple[int, int]] = {}
    tally: dict[int, list[int]] = {}
    for kid, _, signal in deduped:
        if kid is None:
            continue
        bucket = tally.setdefault(kid, [0, 0])
        if signal == "helpful":
            bucket[0] += 1
        elif signal == "misleading":
            bucket[1] += 1
        # outdated is tracked elsewhere — not in the rerank tuple.
    for kid, pair in tally.items():
        out[kid] = (pair[0], pair[1])
    return out


def compute_verification_mult(
    helpful: int,
    misleading: int,
    *,
    sample_floor: int = DEFAULT_SAMPLE_FLOOR,
    misleading_weight: float = DEFAULT_MISLEADING_WEIGHT,
    low: float = DEFAULT_MULT_LOW,
    high: float = DEFAULT_MULT_HIGH,
) -> float:
    """Pure function — no DB, no config.  Exposed so the ranking module and
    ablation tests can exercise the formula in isolation.

    Floor: ``h + m < sample_floor`` → 1.0 (neutral prior).
    Otherwise: ``low + (high - low) * sigmoid(h - misleading_weight * m)``.
    """
    if helpful + misleading < sample_floor:
        return 1.0
    signal = helpful - misleading_weight * misleading
    return low + (high - low) * _sigmoid(signal)


async def get_verification_mult(
    session: AsyncSession,
    knowledge_id: int,
    *,
    config: MnemoConfig | None = None,
    now: datetime | None = None,
) -> float:
    """Verification multiplier for one knowledge id.

    Feature-flag OFF → 1.0 regardless of any feedback events already in the
    DB (spec item 10 — read path).  This lets ops flip the flag and get the
    neutral prior immediately without purging the event log.
    """
    if config is not None and not getattr(config, "feedback_loop_enabled", True):
        return 1.0

    counts = await get_feedback_counts(
        session, knowledge_id, config=config, now=now
    )
    sample_floor = _cfg_int(config, "feedback_sample_floor", DEFAULT_SAMPLE_FLOOR)
    misleading_weight = _cfg_float(
        config, "feedback_misleading_weight", DEFAULT_MISLEADING_WEIGHT
    )
    low = _cfg_float(config, "verification_mult_low", DEFAULT_MULT_LOW)
    high = _cfg_float(config, "verification_mult_high", DEFAULT_MULT_HIGH)

    return compute_verification_mult(
        counts["helpful"],
        counts["misleading"],
        sample_floor=sample_floor,
        misleading_weight=misleading_weight,
        low=low,
        high=high,
    )


async def last_feedback_events(
    session: AsyncSession,
    knowledge_id: int,
    *,
    limit: int = 3,
) -> list[tuple[str, str | None, datetime]]:
    """Read the last ``limit`` feedback events (most-recent first) for the
    given knowledge id.

    Used by the service-side "3 consecutive misleadings" streak detector.
    Returns ``[(signal, actor, created_at), ...]``. Malformed payloads are
    skipped.
    """
    stmt = (
        select(
            KnowledgeEvent.payload_json,
            KnowledgeEvent.actor,
            KnowledgeEvent.created_at,
        )
        .where(
            and_(
                KnowledgeEvent.event_type == FEEDBACK_EVENT_TYPE,
                KnowledgeEvent.knowledge_id == knowledge_id,
            )
        )
        .order_by(KnowledgeEvent.created_at.desc(), KnowledgeEvent.id.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    out: list[tuple[str, str | None, datetime]] = []
    for payload, actor, at in rows:
        signal = _parse_signal(payload)
        if signal is None:
            continue
        out.append((signal, actor, at))
    return out
