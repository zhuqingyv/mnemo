"""Task dispatch/completion event writers.

See docs/phase5/TASK_TRACKING_DESIGN.md. Closes the loop between
``pick_task_for_search`` (派发) and the MCP reply tools (回收) without
adding a new table — everything lands on ``knowledge_event`` with two
new ``event_type`` values (``task_dispatched`` / ``task_completed``).

task_id format: 32-char lowercase hex (uuid4.hex). Invalid values are
silently dropped so older clients and malformed pass-through strings
don't break persistence. Callers should therefore treat
``validate_task_id`` as a filter: ``None`` means "do not include
task_id in the event payload".
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from mnemo.models.knowledge import KnowledgeEvent


logger = logging.getLogger(__name__)


EVENT_TASK_DISPATCHED = "task_dispatched"
EVENT_TASK_COMPLETED = "task_completed"

_TASK_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def validate_task_id(task_id: str | None) -> str | None:
    """Return the task_id if it matches uuid4.hex shape, else ``None``.

    Non-strings, wrong length, non-hex chars → ``None``. Never raises —
    the design asks for silent downgrade to ``agent_initiative``.
    """
    if not isinstance(task_id, str):
        return None
    if _TASK_ID_RE.match(task_id):
        return task_id
    return None


def _normalize_target_ids(target_ids: Iterable[Any] | None) -> list[int]:
    if not target_ids:
        return []
    out: list[int] = []
    for v in target_ids:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return out


async def record_task_dispatched(
    session: AsyncSession,
    *,
    task_id: str,
    task_type: str,
    target_ids: Iterable[Any] | None,
    actor: str | None = None,
    source: str | None = None,
    problem_type: str | None = None,
    priority: float | None = None,
    project_name: str | None = None,
    tags: list[str] | None = None,
) -> bool:
    """Write a ``task_dispatched`` event. Returns True on success.

    - ``task_type``: the recommended MCP action (``feedback_knowledge`` /
      ``archive_knowledge`` / ``update_knowledge`` …).
    - ``source``: where the dispatch came from (``"search"`` / ``"create"``
      / ``"feedback"``) — used by /stats to break down dispatch origin.
    - When ``target_ids`` has exactly one entry, it is also mirrored into
      the event row's ``knowledge_id`` column so FK joins light up.
    - Session is flushed but NOT committed; the caller owns the txn
      boundary (usually the same commit that serves the search reply).
    """
    tid = validate_task_id(task_id)
    if tid is None:
        return False

    ids = _normalize_target_ids(target_ids)
    payload: dict[str, Any] = {
        "task_id": tid,
        "task_type": task_type,
        "action": task_type,
        "target_ids": ids,
    }
    if source:
        payload["source"] = source
    if problem_type:
        payload["problem_type"] = problem_type
    if priority is not None:
        payload["priority"] = priority
    if project_name:
        payload["project_name"] = project_name
    if tags:
        payload["tags"] = list(tags)

    kid = ids[0] if len(ids) == 1 else None
    try:
        session.add(
            KnowledgeEvent(
                knowledge_id=kid,
                event_type=EVENT_TASK_DISPATCHED,
                actor=actor,
                payload_json=json.dumps(payload, ensure_ascii=False),
            )
        )
        await session.flush()
        return True
    except Exception:
        logger.exception("record_task_dispatched: failed to insert event")
        return False


async def record_task_completed(
    session: AsyncSession,
    *,
    task_id: str,
    knowledge_id: int | None,
    action: str,
    actor: str | None = None,
    trigger_source: str = "search_dispatch",
) -> bool:
    """Write a ``task_completed`` event. Returns True on success.

    Used by the回收 paths that don't otherwise write a
    ``knowledge_event`` row (``create_knowledge`` / ``update_knowledge``).
    For ``feedback`` / ``archived`` the caller should instead add
    ``task_id`` + ``trigger_source`` onto the existing event's
    ``payload_json`` — no extra ``task_completed`` row needed.
    """
    tid = validate_task_id(task_id)
    if tid is None:
        return False

    payload: dict[str, Any] = {
        "task_id": tid,
        "action": action,
        "trigger_source": trigger_source,
    }
    if knowledge_id is not None:
        payload["knowledge_id"] = int(knowledge_id)

    try:
        session.add(
            KnowledgeEvent(
                knowledge_id=knowledge_id,
                event_type=EVENT_TASK_COMPLETED,
                actor=actor,
                payload_json=json.dumps(payload, ensure_ascii=False),
            )
        )
        await session.flush()
        return True
    except Exception:
        logger.exception("record_task_completed: failed to insert event")
        return False
