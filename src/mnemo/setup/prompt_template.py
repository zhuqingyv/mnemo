"""System prompt template management for mnemo injection into CLAUDE.md / AGENTS.md / .cursorrules."""

from __future__ import annotations

import os
import re

_START_MARKER = "<!-- mnemo-start -->"
_END_MARKER = "<!-- mnemo-end -->"

_TEMPLATE = f"""{_START_MARKER}
## mnemo — shared knowledge base

Search before starting any task. Store new facts after finishing.

### Usage
- `search(query)` — find relevant knowledge
- `create_knowledge(title, summary, content, tags)` — store new facts
- `feedback_knowledge(id, signal)` — rate search results (helpful/misleading/outdated)

### Rules
1. First step: always search mnemo
2. After using results: feedback_knowledge
3. Before finishing: create_knowledge if you learned something new
4. Only store facts, not opinions
5. Never store secrets or credentials
{_END_MARKER}
"""

_MARKER_RE = re.compile(
    rf"{re.escape(_START_MARKER)}.*?{re.escape(_END_MARKER)}\n?",
    re.DOTALL,
)


def get_prompt_template() -> str:
    """Return the mnemo system prompt template string."""
    return _TEMPLATE


def inject_prompt(prompt_path: str, create_if_missing: bool = True) -> bool:
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

    template = get_prompt_template()

    # Already contains current template exactly — no-op
    if template in content:
        return False

    # Markers present but outdated — replace
    if _MARKER_RE.search(content):
        new_content = _MARKER_RE.sub(template, content)
    else:
        # Append (ensure preceding newline)
        sep = "\n" if content and not content.endswith("\n") else ""
        new_content = content + sep + template

    os.makedirs(os.path.dirname(prompt_path) or ".", exist_ok=True)
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    return True
