"""CLI integration tests using typer.testing.CliRunner.

Each test gets an isolated SQLite DB via MNEMO_DATA_DIR pointing at tmp_path,
and resets the module-level engine cache so no state bleeds between tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mnemo import db as db_module
from mnemo.cli.main import app


@pytest.fixture
def runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[CliRunner]:
    monkeypatch.setenv("MNEMO_DATA_DIR", str(tmp_path))

    # Reset module-level engine cache so each test gets its own DB instance
    # keyed on the fresh MNEMO_DATA_DIR.
    asyncio.run(db_module.reset_engine())
    yield CliRunner()
    asyncio.run(db_module.reset_engine())


def _invoke(runner: CliRunner, *args: str):
    return runner.invoke(app, list(args), catch_exceptions=False)


def test_create_and_get(runner: CliRunner) -> None:
    result = _invoke(
        runner,
        "create",
        "--title",
        "SQLite Note",
        "--summary",
        "fts intro",
        "--body",
        "SQLite FTS5 basics.",
        "--tags",
        "sqlite,search",
    )
    assert result.exit_code == 0, result.output
    assert "Created" in result.output
    assert "SQLite Note" in result.output

    fetched = _invoke(runner, "get", "SQLite Note")
    assert fetched.exit_code == 0
    assert "SQLite Note" in fetched.output
    assert "sqlite" in fetched.output
    assert "SQLite FTS5 basics." in fetched.output


def test_get_missing_exits_nonzero(runner: CliRunner) -> None:
    result = _invoke(runner, "get", "no-such-title")
    assert result.exit_code == 1
    assert "not found" in result.output


def test_search_returns_table(runner: CliRunner) -> None:
    _invoke(
        runner,
        "create",
        "--title",
        "A",
        "--summary",
        "s",
        "--body",
        "mango fruit body",
    )
    _invoke(
        runner,
        "create",
        "--title",
        "B",
        "--summary",
        "s",
        "--body",
        "cherry fruit body",
    )

    hits = _invoke(runner, "search", "fruit")
    assert hits.exit_code == 0
    assert "A" in hits.output
    assert "B" in hits.output

    empty = _invoke(runner, "search", "nonexistentterm")
    assert empty.exit_code == 0
    assert "No" in empty.output


def test_update_changes_fields(runner: CliRunner) -> None:
    created = _invoke(
        runner, "create", "--title", "Orig", "--summary", "s", "--body", "c"
    )
    assert created.exit_code == 0

    updated = _invoke(
        runner, "update", "1", "--title", "Renamed", "--tags", "x,y"
    )
    assert updated.exit_code == 0
    assert "Renamed" in updated.output
    # Update is now a version bump — the fresh row gets a new id.
    assert "supersedes #1" in updated.output

    fetched = _invoke(runner, "get", "Renamed")
    assert fetched.exit_code == 0
    assert "Renamed" in fetched.output
    assert "x" in fetched.output and "y" in fetched.output

    old = _invoke(runner, "get", "1")
    assert old.exit_code == 0
    assert "superseded" in old.output


def test_update_without_fields_errors(runner: CliRunner) -> None:
    _invoke(runner, "create", "--title", "x", "--summary", "s", "--body", "c")
    result = _invoke(runner, "update", "1")
    assert result.exit_code == 2
    assert "no fields" in result.output


def test_delete_cycle(runner: CliRunner) -> None:
    _invoke(runner, "create", "--title", "tmp", "--summary", "s", "--body", "c")
    ok = _invoke(runner, "delete", "1")
    assert ok.exit_code == 0
    assert "Deleted" in ok.output

    again = _invoke(runner, "delete", "1")
    assert again.exit_code == 1


def test_related_traversal(runner: CliRunner) -> None:
    _invoke(runner, "create", "--title", "A", "--summary", "s", "--body", "c")
    _invoke(
        runner,
        "create",
        "--title",
        "B",
        "--summary",
        "s",
        "--body",
        "links [[A]]",
    )
    _invoke(
        runner,
        "create",
        "--title",
        "C",
        "--summary",
        "s",
        "--body",
        "links [[B]]",
    )

    depth1 = _invoke(runner, "related", "A", "--depth", "1")
    assert depth1.exit_code == 0
    assert "B" in depth1.output
    # depth=1 shouldn't reach C
    depth2 = _invoke(runner, "related", "A", "--depth", "2")
    assert depth2.exit_code == 0
    assert "B" in depth2.output and "C" in depth2.output


def test_related_missing(runner: CliRunner) -> None:
    result = _invoke(runner, "related", "does-not-exist")
    assert result.exit_code == 0  # returns empty table, not an error
    assert "No" in result.output


def test_tags_and_tag_search(runner: CliRunner) -> None:
    _invoke(
        runner,
        "create",
        "--title",
        "k1",
        "--summary",
        "s",
        "--body",
        "c",
        "--tags",
        "python,db",
    )
    _invoke(
        runner,
        "create",
        "--title",
        "k2",
        "--summary",
        "s",
        "--body",
        "c",
        "--tags",
        "python,web",
    )

    tags = _invoke(runner, "tags")
    assert tags.exit_code == 0
    assert "python" in tags.output
    assert "db" in tags.output
    assert "web" in tags.output

    hits = _invoke(runner, "tag-search", "python")
    assert hits.exit_code == 0
    assert "k1" in hits.output and "k2" in hits.output

    narrowed = _invoke(runner, "tag-search", "python,db")
    assert narrowed.exit_code == 0
    assert "k1" in narrowed.output
    assert "k2" not in narrowed.output


def test_tag_search_empty_arg_errors(runner: CliRunner) -> None:
    result = _invoke(runner, "tag-search", ",,")
    assert result.exit_code == 2
    assert "no tags" in result.output


def test_scope_filter_end_to_end(runner: CliRunner) -> None:
    _invoke(
        runner,
        "create",
        "--title",
        "G",
        "--summary",
        "s",
        "--body",
        "scope-unique-term",
        "--scope",
        "global",
    )
    _invoke(
        runner,
        "create",
        "--title",
        "P",
        "--summary",
        "s",
        "--body",
        "scope-unique-term",
        "--scope",
        "project",
        "--project",
        "mnemo",
    )

    proj = _invoke(runner, "search", "scope-unique-term", "--scope", "project")
    assert proj.exit_code == 0
    assert "P" in proj.output
    # The "G" row must not appear in project-scoped search
    # (case-sensitive title check via table rendering)
    assert "│ G " not in proj.output and "| G " not in proj.output


def test_help_runs(runner: CliRunner) -> None:
    result = _invoke(runner, "--help")
    assert result.exit_code == 0
    assert "mnemo" in result.output.lower()
