"""Detect locally installed AI clients and their configuration paths.

Each client entry declares:
  - name        : stable identifier (used by setup.command)
  - config_path : where to write/read MCP server entries
  - prompt_path : where to inject the agent-facing system prompt
  - prompt_target : key into setup.prompt_template's _PROMPT_TARGETS
  - format      : "json" or "toml"
  - mcp_field   : top-level key for MCP entries inside the config file
"""

from __future__ import annotations

import sys
from pathlib import Path

# Resolve platform-specific Claude Desktop config path lazily.
def _claude_desktop_path() -> str:
    if sys.platform == "darwin":
        return "~/Library/Application Support/Claude/claude_desktop_config.json"
    if sys.platform.startswith("win"):
        # %APPDATA% is the canonical roaming profile on Windows
        return "~/AppData/Roaming/Claude/claude_desktop_config.json"
    # Linux build of Claude Desktop is unofficial; fall back to XDG-ish path
    return "~/.config/Claude/claude_desktop_config.json"


# Order matters: clients earlier in the list win when there's overlap (e.g.
# both Claude Code and Claude Desktop want the global CLAUDE.md prompt — only
# Claude Code is responsible for it).
_CLIENTS = [
    {
        "name": "claude-code",
        # Claude Code reads MCP servers from ~/.claude.json (project & user
        # scoped servers all live in this single file). Keep ~/.claude/settings.json
        # as a fallback for older installs.
        "config_path": "~/.claude.json",
        "config_path_fallback": "~/.claude/settings.json",
        "prompt_path": "~/.claude/CLAUDE.md",
        "prompt_target": "claude_global",
        "format": "json",
        "mcp_field": "mcpServers",
    },
    {
        "name": "claude-desktop",
        "config_path": _claude_desktop_path(),
        # Claude Desktop has no per-user system prompt file we can write to;
        # the global ~/.claude/CLAUDE.md is owned by Claude Code instead.
        "prompt_path": None,
        "prompt_target": None,
        "format": "json",
        "mcp_field": "mcpServers",
    },
    {
        "name": "cursor",
        "config_path": "~/.cursor/mcp.json",
        # Cursor uses project-level rules; setup.command will inject it into
        # the cwd's .cursorrules when explicitly asked.
        "prompt_path": ".cursorrules",
        "prompt_target": "cursor_rules",
        "format": "json",
        "mcp_field": "mcpServers",
    },
    {
        "name": "codex-cli",
        "config_path": "~/.codex/config.toml",
        "prompt_path": "AGENTS.md",
        "prompt_target": "agents_md",
        "format": "toml",
        "mcp_field": "mcp_servers",
    },
]


def _resolve(path_str: str | None) -> str | None:
    if path_str is None:
        return None
    return str(Path(path_str).expanduser())


def detect_clients() -> list[dict]:
    """Detect locally installed AI clients.

    A client is considered "supported" if its primary or fallback config path
    already exists on disk. The returned dict always uses the resolved primary
    path (so injection writes a fresh file when the user has the client
    installed but no config yet).
    """
    results = []
    for client in _CLIENTS:
        primary = Path(client["config_path"]).expanduser()
        fallback_str = client.get("config_path_fallback")
        fallback = Path(fallback_str).expanduser() if fallback_str else None

        # Pick primary path for writes; report `supported` if either exists.
        # If only fallback exists, prefer that one for writes so we don't
        # split state across two files.
        if primary.exists():
            chosen = primary
        elif fallback is not None and fallback.exists():
            chosen = fallback
        else:
            chosen = primary  # write to primary even if neither exists

        supported = primary.exists() or (fallback is not None and fallback.exists())

        results.append(
            {
                "name": client["name"],
                "config_path": str(chosen),
                "prompt_path": _resolve(client["prompt_path"]),
                "prompt_target": client.get("prompt_target"),
                "format": client["format"],
                "mcp_field": client["mcp_field"],
                "supported": supported,
            }
        )
    return results
