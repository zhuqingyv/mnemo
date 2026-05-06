"""Idempotent MCP config injection / removal for AI client config files.

Two transport modes are supported:
  - "stdio": the client spawns `mnemo mcp` directly (zero background process,
    default for prebuilt-binary distributions).
  - "http":  the client connects to an already-running `mnemo serve` instance
    over streamable-HTTP (multi-client / visualization scenarios).

Both modes write under the same key ("mnemo"), so toggling between them is a
matter of re-running setup with a different `--mode`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Literal

Mode = Literal["stdio", "http"]


def _stdio_block(command: str = "mnemo") -> dict:
    return {"command": command, "args": ["mcp"]}


def _http_block(port: int) -> dict:
    return {
        "type": "http",
        "url": f"http://127.0.0.1:{port}/mcp/http/mcp",
    }


def _build_block(mode: Mode, port: int, command: str) -> dict:
    if mode == "stdio":
        return _stdio_block(command=command)
    if mode == "http":
        return _http_block(port=port)
    raise ValueError(f"Unsupported mode: {mode!r}")


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def _backup(path: Path) -> None:
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _inject_json(
    path: Path,
    field: str,
    mode: Mode,
    port: int,
    command: str,
) -> bool:
    data = _read_json(path)
    servers = data.setdefault(field, {})
    desired = _build_block(mode=mode, port=port, command=command)

    if servers.get("mnemo") == desired:
        return False

    _backup(path)
    servers["mnemo"] = desired
    _write_json(path, data)
    return True


def _remove_json(path: Path, field: str) -> bool:
    if not path.exists():
        return False
    data = _read_json(path)
    servers = data.get(field) or {}
    if "mnemo" not in servers:
        return False
    _backup(path)
    del data[field]["mnemo"]
    if not data[field]:
        del data[field]
    _write_json(path, data)
    return True


# ---------------------------------------------------------------------------
# TOML (string-level, no parser dependency)
# ---------------------------------------------------------------------------

def _toml_section_header(field: str) -> str:
    return f"[{field}.mnemo]"


def _strip_toml_section(text: str, header: str) -> str:
    """Remove a `[section]` block (header + body until next section or EOF)."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped == header:
            skipping = True
            continue
        if skipping:
            # Stop skipping when we hit the next section header
            if stripped.startswith("[") and stripped.endswith("]"):
                skipping = False
                out.append(line)
                continue
            # else: still inside the mnemo block, drop the line
            continue
        out.append(line)
    return "".join(out)


def _inject_toml(
    path: Path,
    field: str,
    mode: Mode,
    port: int,
    command: str,
) -> bool:
    header = _toml_section_header(field)

    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = ""

    # Build the block we want present.
    if mode == "stdio":
        body = (
            f"{header}\n"
            f'command = "{command}"\n'
            f'args = ["mcp"]\n'
        )
    else:
        body = (
            f"{header}\n"
            f'type = "http"\n'
            f'url = "http://127.0.0.1:{port}/mcp/http/mcp"\n'
        )

    if header in text:
        # Already present — check if the body matches exactly to stay idempotent.
        existing = _extract_toml_section(text, header)
        if existing.strip() == body.strip():
            return False
        # Different content → strip and rewrite.
        text = _strip_toml_section(text, header)

    _backup(path)
    new_text = text.rstrip("\n")
    if new_text:
        new_text += "\n\n"
    new_text += body
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")
    return True


def _extract_toml_section(text: str, header: str) -> str:
    out: list[str] = []
    capturing = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped == header:
            capturing = True
            out.append(line)
            continue
        if capturing:
            if stripped.startswith("[") and stripped.endswith("]"):
                break
            out.append(line)
    return "".join(out)


def _remove_toml(path: Path, field: str) -> bool:
    if not path.exists():
        return False
    header = _toml_section_header(field)
    text = path.read_text(encoding="utf-8")
    if header not in text:
        return False
    _backup(path)
    new_text = _strip_toml_section(text, header).rstrip("\n") + "\n"
    path.write_text(new_text, encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def inject_mcp_config(
    config_path: str,
    format: str = "json",
    port: int = 8787,
    mode: Mode = "stdio",
    command: str = "mnemo",
    mcp_field: str = "mcpServers",
) -> bool:
    """Idempotent injection of mnemo MCP server config into a client config.

    Args:
        config_path: Absolute path to the configuration file.
        format:      "json" or "toml".
        port:        mnemo HTTP server port (only used when mode="http").
        mode:        "stdio" (default) or "http".
        command:     Executable to invoke for stdio mode (default "mnemo").
        mcp_field:   Top-level key holding MCP entries. JSON convention is
                     "mcpServers"; Codex CLI's TOML uses "mcp_servers".

    Returns:
        True if the file was modified, False if already up-to-date.
    """
    path = Path(config_path)

    if format == "json":
        return _inject_json(path, mcp_field, mode, port, command)
    if format == "toml":
        return _inject_toml(path, mcp_field, mode, port, command)
    raise ValueError(f"Unsupported format: {format!r}. Use 'json' or 'toml'.")


def remove_mcp_config(
    config_path: str,
    format: str = "json",
    mcp_field: str = "mcpServers",
) -> bool:
    """Idempotently remove the mnemo entry from a client config file.

    Returns True if the file was modified, False if mnemo wasn't present
    or the file doesn't exist.
    """
    path = Path(config_path)
    if format == "json":
        return _remove_json(path, mcp_field)
    if format == "toml":
        return _remove_toml(path, mcp_field)
    raise ValueError(f"Unsupported format: {format!r}. Use 'json' or 'toml'.")
