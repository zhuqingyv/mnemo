"""Lightweight markdown parsing helpers.

Pure-DB mode: we do not generate or persist markdown files. The only thing we
need from the markdown layer is to extract [[wikilinks]] so the service layer
can auto-build Relation rows between knowledge entries.
"""

from __future__ import annotations

import re

# [[target]]           → target
# [[target|alias]]     → target (the part before the pipe is the link target;
#                         the alias is display-only and ignored here)
#
# We reject wikilinks that span newlines or contain nested brackets to keep
# the match conservative. Empty/whitespace-only targets are skipped.
_WIKILINK_RE = re.compile(r"\[\[([^\[\]\n|]+?)(?:\|[^\[\]\n]*)?\]\]")


def extract_wikilinks(text: str | None) -> list[str]:
    """Return de-duplicated wikilink targets from *text*, preserving order.

    Whitespace around the target is stripped. If *text* is falsy, returns [].
    """
    if not text:
        return []

    seen: set[str] = set()
    ordered: list[str] = []
    for match in _WIKILINK_RE.finditer(text):
        target = match.group(1).strip()
        if not target:
            continue
        if target in seen:
            continue
        seen.add(target)
        ordered.append(target)
    return ordered
