"""Idempotent MCP config injection into AI client configuration files."""

from __future__ import annotations

import json
import shutil
from pathlib import Path


def _mnemo_server_block(port: int) -> dict:
    """Return the mnemo MCP server config dict."""
    return {
        "type": "http",
        "url": f"http://127.0.0.1:{port}/mcp/http/mcp",
    }


def _inject_json(path: Path, port: int) -> bool:
    """Inject mnemo into a JSON config file. Returns True if modified."""
    if path.exists():
        text = path.read_text(encoding="utf-8")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {}
    else:
        data = {}

    mcp_servers = data.setdefault("mcpServers", {})
    if "mnemo" in mcp_servers:
        return False

    # Backup before first modification
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))

    mcp_servers["mnemo"] = _mnemo_server_block(port)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return True


def _inject_toml(path: Path, port: int) -> bool:
    """Inject mnemo into a TOML config file via string append. Returns True if modified."""
    section_header = "[mcp_servers.mnemo]"

    if path.exists():
        text = path.read_text(encoding="utf-8")
        if section_header in text:
            return False
    else:
        text = ""

    # Backup before first modification
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))

    block = (
        f"\n{section_header}\n"
        f'type = "http"\n'
        f'url = "http://127.0.0.1:{port}/mcp/http/mcp"\n'
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip("\n") + "\n" + block, encoding="utf-8")
    return True


def inject_mcp_config(config_path: str, format: str = "json", port: int = 8787) -> bool:
    """Idempotent injection of mnemo MCP server config into a client config file.

    - If the file does not exist, creates it.
    - If mnemo is already configured, skips (idempotent).
    - Merges without destroying existing configuration.

    Args:
        config_path: Absolute path to the configuration file.
        format: "json" or "toml".
        port: mnemo server port.

    Returns:
        True if the file was modified, False if already configured.

    Raises:
        ValueError: If format is not "json" or "toml".
    """
    path = Path(config_path)

    if format == "json":
        return _inject_json(path, port)
    elif format == "toml":
        return _inject_toml(path, port)
    else:
        raise ValueError(f"Unsupported format: {format!r}. Use 'json' or 'toml'.")
