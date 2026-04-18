"""In-memory task queue for health-check work items.

No schema migration — tasks live in process memory. Restart re-discovers
outstanding problems via the next trigger (create/feedback/search). Access
is single-threaded: mnemo runs one asyncio event loop per process.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class HealthTask:
    problem_type: str
    priority: float
    target_ids: list[int]
    action: str
    description: str
    project_name: str | None = None
    tags: list[str] = field(default_factory=list)
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: datetime = field(default_factory=_utcnow)


_queue: list[HealthTask] = []
_done: set[str] = set()


def add_task(task: HealthTask) -> None:
    """Push a task; dedupe on (problem_type, sorted target_ids)."""
    sig = (task.problem_type, tuple(sorted(task.target_ids)))
    for ex in _queue:
        if ex.problem_type == sig[0] and tuple(sorted(ex.target_ids)) == sig[1]:
            return
    _queue.append(task)


def pop_task_for(
    project_name: str | None, query_keywords: list[str]
) -> HealthTask | None:
    """Pop highest-priority task matching caller context.

    Match: same project_name, OR tag overlap with query_keywords, OR the
    task is global (no project + no tags). Ties break on older-first.
    """
    kw = {k.lower() for k in query_keywords if k}
    cand: list[tuple[float, datetime, int, HealthTask]] = []
    for idx, t in enumerate(_queue):
        match = (
            (t.project_name is not None and t.project_name == project_name)
            or (t.tags and any(tg.lower() in kw for tg in t.tags))
            or (t.project_name is None and not t.tags)
        )
        if match:
            cand.append((t.priority, t.created_at, idx, t))
    if not cand:
        return None
    cand.sort(key=lambda x: (-x[0], x[1], x[2]))
    _, _, idx, picked = cand[0]
    _queue.pop(idx)
    return picked


def mark_done(task_id: str) -> None:
    _done.add(task_id)


def pending_tasks() -> list[HealthTask]:
    return list(_queue)


def clear() -> None:
    _queue.clear()
    _done.clear()
