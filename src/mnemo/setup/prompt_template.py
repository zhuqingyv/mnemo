"""System prompt template loader — reads from src/mnemo/setup/prompts/*.md.

The prompts directory is the single source of truth for both:
  - the text that `mnemo setup` injects into client prompt files
    (~/.claude/CLAUDE.md, .cursorrules, project AGENTS.md, etc.)
  - the FastMCP `instructions=` field surfaced to every connected agent
    (loaded from `mcp_instructions.md` by mnemo.mcp.server)

Bundling the prompts inside the package makes them survive PyInstaller's
single-file build (collect_data_files picks up the .md files) and lets
contributors version-control the agent contract alongside the code.
"""

from __future__ import annotations

import os
import re
from importlib import resources

_START_MARKER = "<!-- mnemo-start -->"
_END_MARKER = "<!-- mnemo-end -->"

_MARKER_RE = re.compile(
    rf"{re.escape(_START_MARKER)}.*?{re.escape(_END_MARKER)}\n?",
    re.DOTALL,
)

# Map "target" identifiers (used by setup.command) to bundled .md filenames.
# Keep the keys stable — they are part of the public contract for the prompt
# resolver in setup.command and setup.client_detector.
_PROMPT_TARGETS: dict[str, str] = {
    "claude_global": "claude_global.md",
    "cursor_rules": "cursor_rules.md",
    "agents_md": "agents_md.md",
    "mcp_instructions": "mcp_instructions.md",
    "qwen_md": "qwen_md.md",
    "gemini_md": "gemini_md.md",
    "codebuddy_md": "codebuddy_md.md",
}


def _read_prompt_file(filename: str) -> str:
    """Read a bundled .md file from src/mnemo/setup/prompts/.

    Uses importlib.resources so the lookup works identically in:
      - editable install / source checkout (`pip install -e .`)
      - wheel install
      - PyInstaller onefile bundle (resources end up in _MEIPASS)
    """
    with resources.files("mnemo.setup.prompts").joinpath(filename).open(
        "r", encoding="utf-8"
    ) as fh:
        return fh.read()


def get_prompt_body(target: str) -> str:
    """Return the raw markdown body for a given target (no markers)."""
    if target not in _PROMPT_TARGETS:
        raise ValueError(
            f"Unknown prompt target: {target!r}. "
            f"Valid targets: {sorted(_PROMPT_TARGETS)}"
        )
    return _read_prompt_file(_PROMPT_TARGETS[target]).rstrip() + "\n"


def get_prompt_template(target: str = "claude_global") -> str:
    """Return the full marker-wrapped block for `inject_prompt`.

    The markers let `inject_prompt` find and replace the mnemo block
    idempotently across upgrades without touching the rest of the file.
    """
    body = get_prompt_body(target)
    return f"{_START_MARKER}\n{body}{_END_MARKER}\n"


def get_mcp_instructions() -> str:
    """Return the MCP `instructions=` text broadcast to connected agents."""
    return get_prompt_body("mcp_instructions").rstrip()


def inject_prompt(
    prompt_path: str,
    target: str = "claude_global",
    create_if_missing: bool = True,
) -> bool:
    """Idempotently inject the mnemo prompt into a file.

    - If file missing and create_if_missing=True, create it.
    - If markers found, replace content between them (update).
    - If no markers, append to end.

    Returns True if file was modified, False if already up-to-date.
    """
    content = ""
    exists = os.path.isfile(prompt_path)

    if exists:
        with open(prompt_path, "r", encoding="utf-8") as f:
            content = f.read()
    elif not create_if_missing:
        return False

    template = get_prompt_template(target)

    if template in content:
        return False

    if _MARKER_RE.search(content):
        new_content = _MARKER_RE.sub(template, content)
    else:
        sep = "\n" if content and not content.endswith("\n\n") else ""
        new_content = template + sep + content

    os.makedirs(os.path.dirname(prompt_path) or ".", exist_ok=True)
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    return True


def remove_prompt(prompt_path: str) -> bool:
    """Remove the mnemo block (between markers) from a file.

    Returns True if file was modified, False if file missing or no block found.
    """
    if not os.path.isfile(prompt_path):
        return False
    with open(prompt_path, "r", encoding="utf-8") as f:
        content = f.read()
    if not _MARKER_RE.search(content):
        return False
    new_content = _MARKER_RE.sub("", content)
    new_content = new_content.rstrip() + "\n" if new_content.strip() else ""
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    return True
