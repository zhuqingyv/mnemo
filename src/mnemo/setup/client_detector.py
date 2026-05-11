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

import shutil
import subprocess
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
        "detect_binaries": ["claude"],
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
        # Cursor supports both legacy .cursorrules and modern project rules.
        "prompt_path": ".cursorrules",
        "prompt_target": "cursor_rules",
        "prompt_paths": [
            {"path": ".cursorrules", "target": "cursor_rules"},
            {"path": ".cursor/rules/mnemo.mdc", "target": "cursor_project_rule"},
        ],
        "format": "json",
        "mcp_field": "mcpServers",
        "detect_binaries": ["cursor"],
        "detect_paths": ["/Applications/Cursor.app"],
    },
    {
        "name": "codex-cli",
        "config_path": "~/.codex/config.toml",
        "prompt_path": "AGENTS.md",
        "prompt_target": "agents_md",
        "format": "toml",
        "mcp_field": "mcp_servers",
        "detect_binaries": ["codex"],
    },
    {
        "name": "qwen-code",
        "config_path": "~/.qwen/settings.json",
        "prompt_path": "~/.qwen/QWEN.md",
        "prompt_target": "qwen_md",
        "format": "json",
        "mcp_field": "mcpServers",
        "detect_binaries": ["qwen"],
    },
    {
        "name": "gemini-cli",
        "config_path": "~/.gemini/settings.json",
        "prompt_path": "~/.gemini/GEMINI.md",
        "prompt_target": "gemini_md",
        "format": "json",
        "mcp_field": "mcpServers",
        "detect_binaries": ["gemini"],
    },
    {
        "name": "codebuddy",
        "config_path": "~/.codebuddy/.mcp.json",
        "prompt_path": "~/.codebuddy/CODEBUDDY.md",
        "prompt_target": "codebuddy_md",
        "format": "json",
        "mcp_field": "mcpServers",
        "detect_binaries": ["cbc", "codebuddy"],
    },
    {
        "name": "windsurf",
        # Windsurf stores MCP config at ~/.codeium/windsurf/mcp_config.json.
        # Global Cascade rules live in memories/global_rules.md.
        "config_path": "~/.codeium/windsurf/mcp_config.json",
        "prompt_path": "~/.codeium/windsurf/memories/global_rules.md",
        "prompt_target": "windsurf_global_rules",
        "format": "json",
        "mcp_field": "mcpServers",
        "detect_binaries": ["windsurf"],
        "detect_paths": ["/Applications/Windsurf.app"],
    },
    {
        "name": "github-copilot-cli",
        # GitHub Copilot CLI stores MCP config at ~/.copilot/mcp-config.json.
        # Requires explicit "type" field ("local"/"stdio"/"http").
        # Copilot CLI supports local custom instructions in ~/.copilot.
        "config_path": "~/.copilot/mcp-config.json",
        "prompt_path": "~/.copilot/copilot-instructions.md",
        "prompt_target": "copilot_instructions",
        "format": "json",
        "mcp_field": "mcpServers",
        # Detect via binary name or the path where `gh` installs the extension.
        # Do NOT use detect_commands with ["gh", "copilot", ...]: when the
        # extension is not installed, gh downloads it over the network, which
        # hangs indefinitely in CI / air-gapped environments.
        "detect_binaries": ["copilot"],
        "detect_paths": ["~/.local/share/gh/copilot/copilot"],
    },
]


def _resolve(path_str: str | None) -> str | None:
    if path_str is None:
        return None
    return str(Path(path_str).expanduser())


def _binary_candidate_paths(binary: str) -> list[Path]:
    home = Path.home()
    paths = [
        home / ".local" / "bin" / binary,
        home / ".mnemo" / "bin" / binary,
        Path("/opt/homebrew/bin") / binary,
        Path("/usr/local/bin") / binary,
        Path("/usr/bin") / binary,
        Path("/bin") / binary,
    ]
    nvm_versions = home / ".nvm" / "versions" / "node"
    if nvm_versions.exists():
        paths.extend(
            path / "bin" / binary for path in nvm_versions.iterdir() if path.is_dir()
        )
    return paths


def _binary_exists(binary: str) -> bool:
    return shutil.which(binary) is not None or any(
        path.exists() for path in _binary_candidate_paths(binary)
    )


def _command_succeeds(command: list[str]) -> bool:
    if not command or not _binary_exists(command[0]):
        return False
    executable = shutil.which(command[0]) or next(
        (str(path) for path in _binary_candidate_paths(command[0]) if path.exists()),
        None,
    )
    if not executable:
        return False
    try:
        result = subprocess.run(
            [executable, *command[1:]],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


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

        binary_supported = any(
            _binary_exists(binary) for binary in client.get("detect_binaries", [])
        )
        path_supported = any(
            Path(path).expanduser().exists() for path in client.get("detect_paths", [])
        )
        command_supported = any(
            _command_succeeds(cmd) for cmd in client.get("detect_commands", [])
        )
        supported = (
            primary.exists()
            or (fallback is not None and fallback.exists())
            or binary_supported
            or path_supported
            or command_supported
        )

        results.append(
            {
                "name": client["name"],
                "config_path": str(chosen),
                "prompt_path": _resolve(client["prompt_path"]),
                "prompt_target": client.get("prompt_target"),
                "prompt_paths": [
                    {
                        "path": _resolve(prompt["path"]),
                        "target": prompt["target"],
                    }
                    for prompt in client.get("prompt_paths", [])
                ],
                "format": client["format"],
                "mcp_field": client["mcp_field"],
                "supported": supported,
            }
        )
    return results
