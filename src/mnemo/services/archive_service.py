"""Archive service — manual archive / unarchive state transitions.

TECH_PLAN §4.5 / §8.2: ``archived`` is the terminal manual status. An
``archive_knowledge`` call flips ``knowledge.status`` to ``'archived'`` and
records a ``knowledge_event(event_type='archived')`` row so the audit trail
carries the reason. ``unarchive_knowledge`` restores ``status='active'`` only
when the row is currently archived — it does not resurrect superseded or
deprecated rows, those have their own lifecycle.

Gated behind ``config.state_machine_enabled``: when the flag is off the
functions return ``{"success": False, "reason": "feature_disabled"}`` per
§16.2 constraint 4, matching the contract exercised by
``tests/test_feature_flags.py``.

The vector index is intentionally left in place (tech §6.3) — reindex cost
dominates archive latency and an archived row will simply never surface
through the default search filter.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from mnemo.config import MnemoConfig
from mnemo.models.knowledge import Knowledge, KnowledgeEvent
from mnemo.repository import knowledge_repository as kr


STATUS_ARCHIVED = "archived"
EVENT_ARCHIVED = "archived"


def _disabled_response() -> dict[str, Any]:
    return {"success": False, "reason": "feature_disabled"}


async def archive_knowledge(
    session: AsyncSession,
    knowledge_id: int,
    reason: str | None = None,
    *,
    config: MnemoConfig | None = None,
    task_id: str | None = None,
    trigger_source: str | None = None,
) -> dict[str, Any]:
    """Flip ``knowledge.status`` to ``archived`` and record the event.

    Returns ``{"success": True, "archived_at": datetime}`` on success, or a
    structured error dict when the row is missing / already archived / the
    feature flag is off. Caller owns no further session work — the function
    commits before returning.
    """
    cfg = config or MnemoConfig()
    if not cfg.state_machine_enabled:
        return _disabled_response()

    row = await kr.get_by_id(session, knowledge_id)
    if row is None:
        return {"success": False, "reason": "not_found"}
    if row.status == STATUS_ARCHIVED:
        return {"success": False, "reason": "already_archived"}

    archived_at = datetime.now(timezone.utc)
    row.status = STATUS_ARCHIVED
    row.updated_at = archived_at

    archived_payload: dict[str, Any] = {"reason": reason}
    if trigger_source is not None:
        archived_payload["trigger_source"] = trigger_source
    if task_id is not None:
        archived_payload["task_id"] = task_id
    session.add(
        KnowledgeEvent(
            knowledge_id=row.id,
            event_type=EVENT_ARCHIVED,
            payload_json=json.dumps(archived_payload),
        )
    )
    await session.commit()

    return {"success": True, "archived_at": archived_at}


async def unarchive_knowledge(
    session: AsyncSession,
    knowledge_id: int,
    *,
    config: MnemoConfig | None = None,
) -> dict[str, Any]:
    """Restore an archived row to ``status='active'``.

    Only transitions archived → active. Rows in any other state (missing,
    active, superseded, deprecated, stale) are rejected so callers cannot
    accidentally undo those lifecycles through this path.
    """
    cfg = config or MnemoConfig()
    if not cfg.state_machine_enabled:
        return _disabled_response()

    row = await kr.get_by_id(session, knowledge_id)
    if row is None:
        return {"success": False, "reason": "not_found"}
    if row.status != STATUS_ARCHIVED:
        return {"success": False, "reason": "not_archived"}

    row.status = kr.STATUS_ACTIVE
    row.updated_at = datetime.now(timezone.utc)
    await session.commit()

    return {"success": True}


__all__ = [
    "archive_knowledge",
    "unarchive_knowledge",
    "STATUS_ARCHIVED",
    "EVENT_ARCHIVED",
]
