"""Search-time task dispatch.

See HEALTH_CHECK_DESIGN.md §4-§5. Given an incoming ``search`` context,
pick at most one pending maintenance task from the health task queue so
the MCP tool can append it to the result.

The layer is thin: the task store owns queue + match semantics
(``pop_task_for``); this module only feeds it ``project_name`` plus
jieba-tokenized query keywords. Create/feedback hints live in
``health.hints`` to keep this file focused.

If the task store module is not yet wired in (parallel track), imports
and calls degrade silently so search keeps working.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, is_dataclass
from typing import Any

from mnemo.utils.tokenizer import extract_keywords_for_edge


logger = logging.getLogger(__name__)


def pick_task_for_search(
    project_name: str | None,
    query: str,
    scope: str | None = None,  # reserved for future fine-grained matching
) -> dict[str, Any] | None:
    """Return at most one pending task matching this search context.

    Matching is delegated to ``task_store.pop_task_for`` — currently keyed
    on ``project_name`` and jieba-keyword / task-tag overlap. Returns
    ``None`` on no-match or when task_store isn't available yet.
    """
    try:
        from mnemo.health.task_store import pop_task_for
    except ImportError:
        return None

    keywords = extract_keywords_for_edge(query, top_n=8) if query else []
    try:
        task = pop_task_for(project_name, keywords)
    except Exception:
        logger.exception("pick_task_for_search: pop_task_for raised")
        return None
    if not task:
        return None
    return asdict(task) if is_dataclass(task) else dict(task)


def format_task_hint(task: dict[str, Any]) -> str:
    """Render a dispatched task as a markdown tail.

    When the task carries a ``task_id`` (uuid4 hex), it is embedded into
    the call hint so agents can echo it back through the MCP tool's
    ``task_id`` parameter and close the派发→回收 loop. See
    docs/phase5/TASK_TRACKING_DESIGN.md §2.
    """
    priority = float(task.get("priority") or 0.0)
    icon = "[P1]" if priority >= 0.8 else "[P2]"
    description = task.get("description") or ""
    action = task.get("action") or task.get("suggested_tool")
    target_ids = task.get("target_ids") or (
        [task["target_id"]] if task.get("target_id") is not None else []
    )
    task_id = task.get("task_id")
    tid_arg = f', task_id="{task_id}"' if task_id else ""
    tid_tail = f" [task_id={task_id}]" if task_id else ""

    lines = ["", "---", f"{icon} **维护任务**：{description}"]
    if action:
        if len(target_ids) == 1:
            lines.append(
                f"→ 调用 `{action}(id={target_ids[0]}{tid_arg})` 处理{tid_tail}"
            )
        elif target_ids:
            ids_str = ", ".join(str(i) for i in target_ids)
            lines.append(
                f"→ 调用 `{action}` 处理（ids: {ids_str}{tid_arg}）{tid_tail}"
            )
        else:
            lines.append(f"→ 调用 `{action}` 处理{tid_tail}")
    elif task_id:
        lines.append(f"[task_id={task_id}]")
    return "\n".join(lines)
