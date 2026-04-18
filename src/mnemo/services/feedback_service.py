"""Phase 3 P3a-M4 feedback-loop writer side (TECH_PLAN §5.2).

Exposes ``record_feedback`` — the single entry point used by both the MCP
tool and the CLI command. Responsibilities:

1. Feature-flag gate — off → ``{"success": False, "reason": "feature_disabled"}``,
   no event written (spec item 10).
2. Signal enum validation — ``helpful | misleading | outdated``.
3. ``reason`` length cap (default 500 chars).
4. 24h per-actor dedup — same actor + same knowledge within the window does
   not write a new event (spec item 5 on the write path).
5. Write a ``KnowledgeEvent(event_type="feedback", actor=..., payload_json=
   {"signal", "reason"?})`` row.
6. After every ``misleading``, check the last 3 feedback rows for this
   knowledge: three in a row (no ``helpful`` in between) flips status to
   ``stale`` + writes a ``stale_transition`` event in the same transaction.
7. Phase 5b M2: after a successful helpful/misleading event, propagate the
   signal into every ``auto_related`` edge touching the knowledge, lifting
   or lowering the edge weight via the saturation formula below (see
   ``propagate_edge_feedback``). Gated by ``edge_feedback_propagation``.

Returns ``dict`` so MCP / CLI can surface the outcome without unwrapping an
ORM row.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from mnemo.config import MnemoConfig
from mnemo.models.knowledge import Knowledge, KnowledgeEvent, Relation
from mnemo.repository import feedback_repository as fr
from mnemo.repository import relation_repository as rr


FEEDBACK_SIGNALS = ("helpful", "misleading", "outdated")

# Phase 5b M2 — auto_related edge weight saturation (FINE_EDGE_PLAN §2.3 +
# team-lead 2026-04-24 arbitration). Exposed as module-level constants so the
# tests can import them instead of hard-coding magic numbers.
EDGE_WEIGHT_BASE: float = 0.3
EDGE_WEIGHT_CAP: float = 0.85
EDGE_WEIGHT_FLOOR: float = 0.05
EDGE_WEIGHT_K: float = 5.0
EDGE_WEIGHT_MISLEADING_DECAY: float = 0.15
EDGE_WEIGHT_MIN_DECAY: float = 0.1
STALE_TRANSITION_EVENT = "stale_transition"
STATUS_STALE = "stale"

# Mirrors TECH_PLAN §9.5. Surfaced as module-level so MCP/CLI error messages
# can cite the concrete limit.
DEFAULT_REASON_MAX_CHARS = 500
DEFAULT_DEDUP_HOURS = 24
DEFAULT_CONSECUTIVE_MISLEADINGS_TO_STALE = 3


def _cfg_int(config: MnemoConfig | None, name: str, default: int) -> int:
    if config is None:
        return default
    return int(getattr(config, name, default))


def _actor_key(actor: str | None) -> str:
    return actor or "agent:unknown"


async def _has_recent_duplicate(
    session: AsyncSession,
    knowledge_id: int,
    actor: str,
    *,
    dedup_hours: int,
    now: datetime,
) -> bool:
    """Return True iff ``actor`` already wrote a feedback event on this
    knowledge within ``dedup_hours`` of ``now``.

    The dedup semantics here match the reader side: the *latest* event wins,
    so an older event within the window is replaced — we implement that by
    *skipping the new write* (since we can't mutate historical event rows).
    Tests assert the reader collapses within-24h events to the latest, which
    is equivalent to the writer refusing to add a new event within the
    window.
    """
    cutoff = now - timedelta(hours=dedup_hours)
    stmt = (
        select(KnowledgeEvent.id)
        .where(
            and_(
                KnowledgeEvent.event_type == fr.FEEDBACK_EVENT_TYPE,
                KnowledgeEvent.knowledge_id == knowledge_id,
                KnowledgeEvent.actor == actor,
                KnowledgeEvent.created_at >= cutoff,
            )
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def _maybe_transition_stale(
    session: AsyncSession,
    knowledge_id: int,
    *,
    streak_required: int,
) -> bool:
    """Check the last ``streak_required`` feedback events; flip to stale if
    all of them are ``misleading``.

    Returns True iff the row was just flipped. No-op when the row is already
    stale/archived/superseded or when the streak is broken by a helpful.
    The caller owns the transaction — we do not commit here.
    """
    recent = await fr.last_feedback_events(
        session, knowledge_id, limit=streak_required
    )
    if len(recent) < streak_required:
        return False
    if not all(signal == "misleading" for signal, _, _ in recent):
        return False

    row = await session.get(Knowledge, knowledge_id)
    if row is None or row.status != "active":
        return False

    prior_status = row.status
    stmt = sa_update(Knowledge.__table__).where(
        Knowledge.__table__.c.id == knowledge_id
    ).values(status=STATUS_STALE)
    await session.execute(stmt)
    session.add(
        KnowledgeEvent(
            knowledge_id=knowledge_id,
            event_type=STALE_TRANSITION_EVENT,
            payload_json=json.dumps(
                {
                    "from_status": prior_status,
                    "to_status": STATUS_STALE,
                    "reason": "consecutive_misleading_feedback",
                    "streak": streak_required,
                }
            ),
        )
    )
    return True


def compute_edge_weight(helpful_count: int, misleading_count: int) -> float:
    """Return the post-feedback weight for an ``auto_related`` edge.

    Formula (FINE_EDGE_PLAN §2.3 + team-lead 2026-04-24 arbitration)::

        decay  = max(MIN_DECAY, 1 - misleading * 0.15)
        weight = BASE + helpful/(helpful+K) * (CAP - BASE) * decay
        weight = max(FLOOR, weight)

    Why saturation instead of linear: early helpful feedback has high marginal
    impact (first few ``helpful`` clearly separate real-signal edges from
    noise) while the upside is capped so a single hot knowledge cannot drive
    every edge to ~1.0. Misleading applies a multiplicative decay and then the
    FLOOR floor prevents the edge from hitting 0 — a tiny residual weight is
    kept for auditability (see §2.3 "floor=0.05 留一点痕迹方便调查").
    """
    h = max(0, int(helpful_count))
    m = max(0, int(misleading_count))
    decay = max(EDGE_WEIGHT_MIN_DECAY, 1.0 - m * EDGE_WEIGHT_MISLEADING_DECAY)
    helpful_term = (h / (h + EDGE_WEIGHT_K)) * (EDGE_WEIGHT_CAP - EDGE_WEIGHT_BASE)
    raw = EDGE_WEIGHT_BASE + helpful_term * decay
    return max(EDGE_WEIGHT_FLOOR, raw)


def _parse_edge_extra(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


async def propagate_edge_feedback(
    service: Any,
    *,
    knowledge_id: int,
    signal: str,
    config: MnemoConfig | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Lift / lower every ``auto_related`` edge touching *knowledge_id*.

    Flow:

    1. No-op when ``signal`` is neither ``helpful`` nor ``misleading``
       (``outdated`` signals do not move edge weights — see §2.3: outdated
       targets archival, not weight decay).
    2. No-op when ``edge_feedback_propagation`` is False on the config.
    3. For every ``auto_related`` edge touching the node: bump the relevant
       counter in ``extra_json`` and recompute ``weight`` via
       ``compute_edge_weight``.

    Returns a diagnostic dict with ``updated`` (count of affected edges) so
    the MCP tool can surface the propagation result without crashing on a
    None return — and the test suite can assert behavior independent of the
    wire format.
    """
    if signal not in ("helpful", "misleading"):
        return {"propagated": False, "reason": "signal_not_weighted", "updated": 0}

    cfg = config if config is not None else getattr(service, "_config", None)
    if cfg is not None and not getattr(cfg, "edge_feedback_propagation", True):
        return {"propagated": False, "reason": "feature_disabled", "updated": 0}

    now_ts = now or datetime.now(timezone.utc)

    session_factory = getattr(service, "_session_factory")
    updated = 0
    async with session_factory() as session:
        edges = await rr.get_auto_edges_touching(session, knowledge_id)
        for edge in edges:
            extra = _parse_edge_extra(edge.extra_json)
            helpful = int(extra.get("helpful_count", 0) or 0)
            misleading = int(extra.get("misleading_count", 0) or 0)
            if signal == "helpful":
                helpful += 1
            else:
                misleading += 1
            extra["helpful_count"] = helpful
            extra["misleading_count"] = misleading
            extra["last_feedback_at"] = now_ts.isoformat()

            new_weight = compute_edge_weight(helpful, misleading)
            await rr.update_weight(
                session,
                edge.id,
                new_weight=new_weight,
                extra_json=json.dumps(extra, ensure_ascii=False),
            )
            updated += 1

    return {"propagated": True, "updated": updated, "signal": signal}


async def record_feedback(
    service: Any,
    *,
    knowledge_id: int,
    signal: str,
    reason: str | None = None,
    actor: str = "agent:unknown",
    config: MnemoConfig | None = None,
    task_id: str | None = None,
    trigger_source: str | None = None,
) -> dict[str, Any]:
    """Record an agent feedback event.

    ``service`` is the ``KnowledgeService`` instance — we reuse its private
    ``_session_factory`` and ``_config`` to open a fresh session, matching the
    conventions used by ``archive_service`` and the rest of the service layer.

    Returns a structured dict with ``success`` / ``reason`` / ``event_id`` and
    auxiliary metadata (e.g. ``transitioned_to_stale``).  Never raises on
    business-level issues — the caller pattern-matches on ``success``.

    Raises ``ValueError`` for programmer errors (unknown signal, reason too
    long) so the MCP / CLI layer can surface a stack-trace-free 400-style
    response.
    """
    if signal not in FEEDBACK_SIGNALS:
        raise ValueError(
            f"invalid feedback signal {signal!r}; "
            f"expected one of {FEEDBACK_SIGNALS}"
        )

    cfg = config
    if cfg is None:
        cfg = getattr(service, "_config", None)

    reason_max = _cfg_int(cfg, "feedback_reason_max_chars", DEFAULT_REASON_MAX_CHARS)
    if reason is not None and len(reason) > reason_max:
        raise ValueError(
            f"reason length {len(reason)} exceeds max {reason_max}"
        )

    if cfg is not None and not getattr(cfg, "feedback_loop_enabled", True):
        return {
            "success": False,
            "reason": "feature_disabled",
            "knowledge_id": knowledge_id,
        }

    dedup_hours = _cfg_int(cfg, "feedback_dedup_hours", DEFAULT_DEDUP_HOURS)
    streak_required = _cfg_int(
        cfg,
        "feedback_consecutive_misleadings_to_stale",
        DEFAULT_CONSECUTIVE_MISLEADINGS_TO_STALE,
    )

    session_factory = getattr(service, "_session_factory")
    async with session_factory() as session:
        # Ensure the target row exists so we fail fast on bad ids.
        target = await session.get(Knowledge, knowledge_id)
        if target is None:
            return {
                "success": False,
                "reason": "knowledge_not_found",
                "knowledge_id": knowledge_id,
            }

        now = datetime.now(timezone.utc)
        actor_key = _actor_key(actor)

        if await _has_recent_duplicate(
            session,
            knowledge_id,
            actor_key,
            dedup_hours=dedup_hours,
            now=now,
        ):
            return {
                "success": False,
                "reason": "deduplicated_within_window",
                "knowledge_id": knowledge_id,
                "actor": actor_key,
                "dedup_hours": dedup_hours,
            }

        payload: dict[str, Any] = {"signal": signal}
        if reason is not None:
            payload["reason"] = reason
        # Phase 5 task tracking: record origin so /stats can split
        # search-dispatch completion vs. agent-initiative reporting.
        if trigger_source is not None:
            payload["trigger_source"] = trigger_source
        if task_id is not None:
            payload["task_id"] = task_id

        event = KnowledgeEvent(
            knowledge_id=knowledge_id,
            event_type=fr.FEEDBACK_EVENT_TYPE,
            actor=actor_key,
            payload_json=json.dumps(payload, ensure_ascii=False),
        )
        session.add(event)
        # Flush so the new event is visible to the streak-detector query below
        # (it reads the same session).
        await session.flush()

        transitioned = False
        if signal == "misleading":
            transitioned = await _maybe_transition_stale(
                session,
                knowledge_id,
                streak_required=streak_required,
            )

        await session.commit()
        await session.refresh(event)

        result: dict[str, Any] = {
            "success": True,
            "event_id": event.id,
            "knowledge_id": knowledge_id,
            "actor": actor_key,
            "signal": signal,
        }
        if transitioned:
            result["transitioned_to_stale"] = True

    # Edge propagation runs on its own session so a failure here never rolls
    # back the feedback event itself. ``helpful`` / ``misleading`` are the
    # only signals that move weights — ``outdated`` is handled by the
    # archive_service flow.
    if signal in ("helpful", "misleading"):
        propagation = await propagate_edge_feedback(
            service,
            knowledge_id=knowledge_id,
            signal=signal,
            config=cfg,
        )
        if propagation.get("propagated"):
            result["edges_updated"] = int(propagation.get("updated", 0))
    return result
