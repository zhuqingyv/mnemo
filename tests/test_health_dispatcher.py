"""Unit tests for health.dispatcher and health.hints.

Covers:
- pick_task_for_search — project match / keyword match / empty queue / store unavailable
- format_task_hint — icon, action rendering with single/multi/no target_ids
- hint_from_create_result — supersede / review / evidence_weak / embedding_failed / clean
- hint_from_feedback_result — transitioned_to_stale / normal
"""

from __future__ import annotations

import pytest

from mnemo.health import task_store as ts
from mnemo.health.dispatcher import format_task_hint, pick_task_for_search
from mnemo.health.hints import hint_from_create_result, hint_from_feedback_result
from mnemo.health.task_store import HealthTask


@pytest.fixture(autouse=True)
def _clear_queue():
    ts.clear()
    yield
    ts.clear()


# ---------------------------------------------------------------------------
# pick_task_for_search
# ---------------------------------------------------------------------------

def test_pick_task_returns_none_when_queue_empty():
    assert pick_task_for_search("mnemo", "anything") is None


def test_pick_task_matches_on_project_name():
    ts.add_task(
        HealthTask(
            problem_type="P1-1",
            priority=0.9,
            target_ids=[42],
            action="archive_knowledge",
            description="#42 misleading",
            project_name="mnemo",
        )
    )
    result = pick_task_for_search("mnemo", "anything")
    assert result is not None
    assert result["target_ids"] == [42]
    assert result["priority"] == 0.9

    # second call after pop → empty
    assert pick_task_for_search("mnemo", "anything") is None


def test_pick_task_matches_on_keyword_overlap():
    ts.add_task(
        HealthTask(
            problem_type="P2-3",
            priority=0.4,
            target_ids=[11],
            action="update_knowledge",
            description="#11 evidence weak",
            project_name=None,
            tags=["sqlite"],
        )
    )
    result = pick_task_for_search(None, "sqlite fts5 tokenizer")
    assert result is not None
    assert result["target_ids"] == [11]


def test_pick_task_skips_unrelated_project():
    ts.add_task(
        HealthTask(
            problem_type="P1-1",
            priority=0.9,
            target_ids=[7],
            action="archive_knowledge",
            description="other project task",
            project_name="other",
        )
    )
    # different project, no keyword tags → no match
    assert pick_task_for_search("mnemo", "random words") is None


def test_pick_task_prefers_higher_priority(monkeypatch):
    ts.add_task(
        HealthTask(
            problem_type="P2",
            priority=0.3,
            target_ids=[1],
            action="a",
            description="low",
            project_name="mnemo",
        )
    )
    ts.add_task(
        HealthTask(
            problem_type="P1",
            priority=0.95,
            target_ids=[2],
            action="b",
            description="high",
            project_name="mnemo",
        )
    )
    result = pick_task_for_search("mnemo", "anything")
    assert result is not None
    assert result["target_ids"] == [2]


def test_pick_task_degrades_when_store_missing(monkeypatch):
    """If task_store import fails we return None instead of raising."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "mnemo.health.task_store":
            raise ImportError("simulated missing module")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert pick_task_for_search("mnemo", "anything") is None


def test_pick_task_swallows_pop_exceptions(monkeypatch):
    """Dispatcher must not propagate task_store bugs to the search tool."""
    import mnemo.health.task_store as store

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(store, "pop_task_for", boom)
    assert pick_task_for_search("mnemo", "anything") is None


# ---------------------------------------------------------------------------
# format_task_hint
# ---------------------------------------------------------------------------

def test_format_task_hint_p1_single_target():
    task = {
        "priority": 0.9,
        "description": "知识 #42 misleading",
        "action": "archive_knowledge",
        "target_ids": [42],
    }
    out = format_task_hint(task)
    assert "[P1]" in out
    assert "archive_knowledge(id=42)" in out


def test_format_task_hint_p2_no_target():
    task = {
        "priority": 0.3,
        "description": "generic maintenance",
        "action": "review",
        "target_ids": [],
    }
    out = format_task_hint(task)
    assert "[P2]" in out
    assert "`review`" in out
    assert "id=" not in out


def test_format_task_hint_multiple_ids():
    task = {
        "priority": 0.9,
        "description": "merge dups",
        "action": "merge",
        "target_ids": [1, 2, 3],
    }
    out = format_task_hint(task)
    assert "ids: 1, 2, 3" in out


# ---------------------------------------------------------------------------
# hint_from_create_result
# ---------------------------------------------------------------------------

def test_hint_create_supersede_uses_exact_duplicate():
    hint = hint_from_create_result(
        {"id": 10, "write_gate": {"recommended_action": "supersede",
                                   "exact_duplicate": {"id": 5}}}
    )
    assert hint is not None
    assert "[P1]" in hint
    assert "#10" in hint and "#5" in hint
    assert "update_knowledge(knowledge_id=5" in hint


def test_hint_create_supersede_falls_back_to_semantic():
    hint = hint_from_create_result(
        {"id": 10, "write_gate": {"recommended_action": "supersede",
                                   "semantic_similar": [{"id": 7, "cosine": 0.99}]}}
    )
    assert hint is not None
    assert "#7" in hint


def test_hint_create_review():
    hint = hint_from_create_result(
        {"id": 20, "write_gate": {"recommended_action": "review"}}
    )
    assert hint is not None
    assert "#20" in hint
    assert "复核" in hint


def test_hint_create_evidence_weak():
    hint = hint_from_create_result(
        {"id": 30,
         "write_gate": {"recommended_action": "create",
                        "evidence_weak": {"reason": "content<50"}}}
    )
    assert hint is not None
    assert "[P2]" in hint
    assert "content<50" in hint


def test_hint_create_embedding_failed():
    hint = hint_from_create_result(
        {"id": 40, "embedding_failed": True,
         "write_gate": {"recommended_action": "create"}}
    )
    assert hint is not None
    assert "[P1]" in hint
    assert "#40" in hint
    assert "向量" in hint


def test_hint_create_clean_returns_none():
    assert hint_from_create_result(
        {"id": 50, "write_gate": {"recommended_action": "create"}}
    ) is None


def test_hint_create_no_gate_returns_none():
    assert hint_from_create_result({"id": 60}) is None


# ---------------------------------------------------------------------------
# hint_from_feedback_result
# ---------------------------------------------------------------------------

def test_hint_feedback_transitioned():
    hint = hint_from_feedback_result(
        {"success": True, "transitioned_to_stale": True}, 42
    )
    assert hint is not None
    assert "[P1]" in hint
    assert "#42" in hint
    assert "archive_knowledge(id=42" in hint


def test_hint_feedback_clean_returns_none():
    assert hint_from_feedback_result({"success": True}, 42) is None
