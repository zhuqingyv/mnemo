"""Lifecycle service — stale transition judgement + last_accessed touch.

TECH_PLAN §4.2 / §4.4: the state-machine极简版.  This module provides the
two primitives the read-lazy flow needs:

  * ``check_stale_transition`` — pure threshold check, no DB, no I/O. The
    caller decides what to do with a ``True`` return (write the status
    flip + knowledge_event in its own transaction).
  * ``touch_last_accessed`` — batch ``UPDATE`` on ``last_accessed_at`` with
    a 60-second dedupe window so repeated reads don't hammer IOPS.

Both are gated behind ``config.state_machine_enabled``. When the flag is
off, ``check_stale_transition`` returns ``False`` so no transition fires.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from mnemo.config import MnemoConfig
from mnemo.models.knowledge import Knowledge


STATUS_ACTIVE = "active"


def _as_utc(dt: datetime) -> datetime:
    """Coerce a naive datetime to UTC. SQLite stores naive datetimes, so a row
    read back through the ORM may be tz-less — treat such values as UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def check_stale_transition(
    knowledge_row: Any,
    config: MnemoConfig,
    *,
    now: datetime | None = None,
) -> bool:
    """Return True iff *knowledge_row* should flip from active to stale.

    Conditions (all must hold):
      * flag ``state_machine_enabled`` is on;
      * current status is ``active``;
      * ``updated_at`` is ≥ ``no_update_days`` ago for the row's claim_type;
      * ``last_accessed_at`` is non-null *and* ≥ ``no_access_days`` ago.

    Null ``last_accessed_at`` is treated as "has not had its first read yet"
    — the read-lazy flow fills it on this very call, so the stale clock
    starts from the first real read rather than from creation time. This
    matches the tech_research §6.2 contract "被读就会刷新活跃度".

    ``knowledge_row`` may be an ORM ``Knowledge`` instance or any object with
    the same attributes (``status`` / ``updated_at`` / ``last_accessed_at`` /
    ``claim_type``) — the function does not touch the session.

    Thresholds are looked up via ``config.stale_thresholds_by_claim_type``
    keyed by ``claim_type``. An unknown or ``None`` claim_type means no
    threshold is defined, so the row stays active.
    """
    if not config.state_machine_enabled:
        return False

    status = getattr(knowledge_row, "status", None)
    if status != STATUS_ACTIVE:
        return False

    claim_type = getattr(knowledge_row, "claim_type", None)
    if not claim_type:
        return False

    thresholds = config.stale_thresholds_by_claim_type.get(claim_type)
    if not thresholds:
        return False

    no_update_days = thresholds.get("no_update_days")
    no_access_days = thresholds.get("no_access_days")
    if no_update_days is None or no_access_days is None:
        return False

    updated_at = getattr(knowledge_row, "updated_at", None)
    if updated_at is None:
        return False

    reference = now or datetime.now(timezone.utc)
    reference = _as_utc(reference)

    update_age_days = (reference - _as_utc(updated_at)).total_seconds() / 86400.0
    if update_age_days < no_update_days:
        return False

    last_accessed_at = getattr(knowledge_row, "last_accessed_at", None)
    if last_accessed_at is None:
        # Never accessed yet — the read-lazy caller will fill this on the
        # current call, so the stale clock starts from the first real read.
        # §6.2 "被读就会刷新活跃度".
        return False

    access_age_days = (
        reference - _as_utc(last_accessed_at)
    ).total_seconds() / 86400.0
    return access_age_days >= no_access_days


async def touch_last_accessed(
    session: AsyncSession,
    ids: Iterable[int],
    *,
    now: datetime | None = None,
    dedupe_window_s: float = 60.0,
) -> list[int]:
    """Batch-update ``last_accessed_at`` to *now* for the given ids.

    Rows whose current ``last_accessed_at`` is within ``dedupe_window_s`` of
    ``now`` are skipped — this is the §4.5 "60s 去重写" rule that keeps
    read-heavy traffic from hammering IOPS. Returns the ids that were
    actually written so the caller can log / observe the churn.

    The function does not commit — the caller owns transaction boundaries
    so the write can be coalesced with any ``stale_transition`` updates made
    in the same read-lazy step.
    """
    id_list = [int(i) for i in ids]
    if not id_list:
        return []

    reference = now or datetime.now(timezone.utc)
    reference = _as_utc(reference)
    cutoff = reference.timestamp() - dedupe_window_s

    # Load current last_accessed_at for the ids so we can filter out rows that
    # were just touched. A single SELECT + single UPDATE is cheaper than N
    # round-trips and keeps the dedupe logic out of SQL dialect territory.
    from sqlalchemy import select

    existing = await session.execute(
        select(Knowledge.id, Knowledge.last_accessed_at).where(
            Knowledge.id.in_(id_list)
        )
    )
    to_write: list[int] = []
    for kid, last in existing.all():
        if last is None:
            to_write.append(kid)
            continue
        if _as_utc(last).timestamp() < cutoff:
            to_write.append(kid)

    if not to_write:
        return []

    # Store as tz-aware UTC so callers that read back can do direct arithmetic
    # against their own tz-aware reference clocks. SQLite's DateTime column
    # strips tz on read anyway — use a TypeDecorator on the model to re-attach
    # UTC (see ``Knowledge.last_accessed_at``).
    # Core UPDATE still triggers ``Column.onupdate`` — explicitly pin
    # ``updated_at`` to its own column value so the ``onupdate=_utcnow`` hook
    # cannot bump it. Touching ``last_accessed_at`` must not bump
    # ``updated_at`` or freshness scoring / search ordering (§4.5) would flip
    # on every read.
    table = Knowledge.__table__
    await session.execute(
        update(table)
        .where(table.c.id.in_(to_write))
        .values(last_accessed_at=reference, updated_at=table.c.updated_at)
    )
    return to_write
