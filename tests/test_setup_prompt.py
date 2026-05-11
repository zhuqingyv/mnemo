from pathlib import Path

from mnemo.setup.command import _prompt_entries
from mnemo.setup.prompt_template import get_prompt_template, inject_prompt, remove_prompt


def test_new_prompt_targets_are_available() -> None:
    for target in (
        "cursor_project_rule",
        "windsurf_global_rules",
        "copilot_instructions",
    ):
        template = get_prompt_template(target)
        assert "<!-- mnemo-start -->" in template
        assert "<!-- mnemo-end -->" in template


def test_cursor_project_rule_preserves_frontmatter(tmp_path: Path) -> None:
    target = tmp_path / "mnemo.mdc"

    changed = inject_prompt(str(target), target="cursor_project_rule")

    assert changed is True
    content = target.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert "alwaysApply: true" in content
    assert "<!-- mnemo-start -->" in content
    assert "<!-- mnemo-end -->" in content


def test_prompt_entries_supports_multiple_paths() -> None:
    client = {
        "prompt_path": ".cursorrules",
        "prompt_target": "cursor_rules",
        "prompt_paths": [
            {"path": ".cursorrules", "target": "cursor_rules"},
            {"path": ".cursor/rules/mnemo.mdc", "target": "cursor_project_rule"},
        ],
    }

    entries = _prompt_entries(client)

    assert entries == client["prompt_paths"]


def test_remove_prompt_removes_marker_block(tmp_path: Path) -> None:
    target = tmp_path / "copilot-instructions.md"
    inject_prompt(str(target), target="copilot_instructions")

    removed = remove_prompt(str(target))

    assert removed is True
    assert target.read_text(encoding="utf-8") == ""
