"""Return-value hints appended by create_knowledge / feedback_knowledge.

These turn write-time signals (duplicate, weak evidence, embedding miss,
misleading streak) into the same markdown-tail task banners that
dispatcher emits for search. See HEALTH_CHECK_DESIGN.md §4 row "直接触发".

Each hint also allocates a fresh ``task_id`` (uuid4 hex) and embeds it
into the rendered call suggestion so the downstream MCP回收 tool can
echo it back. Callers that want to persist the dispatch should read the
``task_id`` from the returned tuple (see ``hint_from_*_with_id``).
"""

from __future__ import annotations

import uuid
from typing import Any


def _new_task_id() -> str:
    return uuid.uuid4().hex


def _tid_args(task_id: str) -> str:
    return f', task_id="{task_id}"'


def _tid_tail(task_id: str) -> str:
    return f" [task_id={task_id}]"


def hint_from_create_result(result: dict[str, Any]) -> str | None:
    """Surface write-time problems as a task banner alongside write_gate."""
    pair = hint_from_create_result_with_id(result)
    return pair[0] if pair else None


def hint_from_create_result_with_id(
    result: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    """Same as ``hint_from_create_result`` but also returns dispatch meta.

    Returns ``(hint_text, meta)`` where ``meta`` has ``task_id`` /
    ``task_type`` / ``target_ids`` / ``source`` suitable for
    ``record_task_dispatched``. ``None`` when no hint applies.
    """
    gate = result.get("write_gate") or {}
    action = gate.get("recommended_action") or "create"
    kid = result.get("id")

    if action == "supersede":
        exact = gate.get("exact_duplicate") or {}
        target = exact.get("id") or (gate.get("semantic_similar") or [{}])[0].get("id")
        if target:
            tid = _new_task_id()
            text = (
                f"\n\n---\n[P1] **维护任务**：知识 #{kid} 与 #{target} 内容高度重复。\n"
                f"→ 调用 `update_knowledge(knowledge_id={target}"
                f"{_tid_args(tid)})` 合并并 supersede{_tid_tail(tid)}"
            )
            return text, {
                "task_id": tid,
                "task_type": "update_knowledge",
                "target_ids": [target],
                "source": "create",
                "problem_type": "duplicate",
                "priority": 0.8,
            }
    if action == "review":
        tid = _new_task_id()
        text = (
            f"\n\n---\n[P1] **维护任务**：知识 #{kid} 命中疑似重复或矛盾，请复核。\n"
            f"→ 参考上方 write-gate report，必要时 `update_knowledge` 或 "
            f"`archive_knowledge`{_tid_tail(tid)}"
        )
        return text, {
            "task_id": tid,
            "task_type": "update_knowledge",
            "target_ids": [kid] if kid is not None else [],
            "source": "create",
            "problem_type": "review",
            "priority": 0.8,
        }
    evidence = gate.get("evidence_weak")
    if evidence:
        tid = _new_task_id()
        text = (
            f"\n\n---\n[P2] **维护任务**：知识 #{kid} 证据偏弱"
            f"（{evidence.get('reason', 'unknown')}）。\n"
            f"→ 调用 `update_knowledge(knowledge_id={kid}{_tid_args(tid)})`"
            f" 补强 content 或 source{_tid_tail(tid)}"
        )
        return text, {
            "task_id": tid,
            "task_type": "update_knowledge",
            "target_ids": [kid] if kid is not None else [],
            "source": "create",
            "problem_type": "evidence_weak",
            "priority": 0.5,
        }
    if result.get("embedding_failed"):
        tid = _new_task_id()
        text = (
            f"\n\n---\n[P1] **维护任务**：知识 #{kid} 向量生成失败，hybrid 搜索将瞎一路。\n"
            f"→ 调用 `update_knowledge(knowledge_id={kid}{_tid_args(tid)})`"
            f" 触发重建{_tid_tail(tid)}"
        )
        return text, {
            "task_id": tid,
            "task_type": "update_knowledge",
            "target_ids": [kid] if kid is not None else [],
            "source": "create",
            "problem_type": "embedding_failed",
            "priority": 0.9,
        }
    return None


def hint_from_feedback_result(
    result: dict[str, Any],
    knowledge_id: int,
) -> str | None:
    """Surface P1-1 (misleading streak → stale) as a tool-return tail."""
    pair = hint_from_feedback_result_with_id(result, knowledge_id)
    return pair[0] if pair else None


def hint_from_feedback_result_with_id(
    result: dict[str, Any],
    knowledge_id: int,
) -> tuple[str, dict[str, Any]] | None:
    """Same as ``hint_from_feedback_result`` but also returns dispatch meta."""
    if result.get("transitioned_to_stale"):
        tid = _new_task_id()
        text = (
            f"\n\n---\n[P1] **维护任务**：知识 #{knowledge_id} 连续 misleading "
            f"已被转为 stale，可能正在毒化搜索结果。\n"
            f"→ 调用 `archive_knowledge(id={knowledge_id}{_tid_args(tid)})` 或 "
            f"`update_knowledge(knowledge_id={knowledge_id}{_tid_args(tid)})` "
            f"处理{_tid_tail(tid)}"
        )
        return text, {
            "task_id": tid,
            "task_type": "archive_knowledge",
            "target_ids": [knowledge_id],
            "source": "feedback",
            "problem_type": "stale_transition",
            "priority": 0.9,
        }
    return None
