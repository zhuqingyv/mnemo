"""Detect locally installed AI clients and their configuration paths."""

from pathlib import Path

# Client registry: each entry defines where to find config and prompt files.
_CLIENTS = [
    {
        "name": "claude-code",
        "config_path": "~/.claude/settings.json",
        "prompt_path": "~/.claude/CLAUDE.md",
        "format": "json",
        "mcp_field": "mcpServers",
    },
    {
        "name": "cursor",
        "config_path": "~/.cursor/mcp.json",
        "prompt_path": ".cursorrules",  # project-level
        "format": "json",
        "mcp_field": "mcpServers",
    },
    {
        "name": "codex-cli",
        "config_path": "~/.codex/config.toml",
        "prompt_path": "AGENTS.md",  # project-level
        "format": "toml",
        "mcp_field": "mcpServers",
    },
]


def detect_clients() -> list[dict]:
    """Detect locally installed AI clients.

    Returns a list of dicts, one per known client. Each dict contains:
        name        — client identifier
        config_path — absolute path to the config file
        prompt_path — absolute path (or project-relative) to the prompt file
        format      — config file format (json | toml)
        mcp_field   — key name for MCP server entries in config
        supported   — True if the config file exists on disk
    """
    results = []
    for client in _CLIENTS:
        config_path = Path(client["config_path"]).expanduser()
        prompt_path = Path(client["prompt_path"]).expanduser()
        results.append(
            {
                "name": client["name"],
                "config_path": str(config_path),
                "prompt_path": str(prompt_path),
                "format": client["format"],
                "mcp_field": client["mcp_field"],
                "supported": config_path.exists(),
            }
        )
    return results
