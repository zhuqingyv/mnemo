"""Idempotent MCP config injection / removal for AI client config files.

Strategy: CLI-first, fallback to file write.
  - For agents with native CLI (claude, qwen, cbc, codex, gemini),
    prefer `<agent> mcp add/remove` commands — they handle internal
    structures correctly and take effect immediately.
  - Fall back to direct file edit only when CLI is unavailable or fails.

Two transport modes are supported:
  - "stdio": the client spawns `mnemo mcp` directly.
  - "http":  the client connects to an already-running `mnemo serve` instance
    over streamable-HTTP (recommended — instant connect, no cold start).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Literal

Mode = Literal["stdio", "http"]

# ---------------------------------------------------------------------------
# CLI command builders per client
# ---------------------------------------------------------------------------

def _find_cli(names: list[str]) -> str | None:
    """Find the first available CLI binary from a list of candidates."""
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


_CLIENT_CLI_MAP: dict[str, list[str]] = {
    "claude-code": ["claude"],
    "qwen-code": ["qwen"],
    "codebuddy": ["cbc", "codebuddy"],
    "codex-cli": ["codex"],
    "gemini-cli": ["gemini"],
    "cursor": ["cursor"],
    "github-copilot-cli": ["copilot"],
}


def _cli_install_args(
    client_name: str,
    cli_path: str,
    mode: Mode,
    port: int,
    command: str,
) -> list[str] | None:
    """Build CLI args for installing mnemo MCP. Returns None if not supported."""
    url = f"http://127.0.0.1:{port}/mcp/http/mcp"

    if client_name == "claude-code":
        if mode == "http":
            return [cli_path, "mcp", "add", "-s", "user", "-t", "http", "mnemo", url]
        else:
            return [cli_path, "mcp", "add", "-s", "user", "mnemo", "--", command, "mcp"]

    if client_name == "qwen-code":
        if mode == "http":
            return [cli_path, "mcp", "add", "-s", "user", "-t", "http", "mnemo", url]
        else:
            return [cli_path, "mcp", "add", "-s", "user", "mnemo", "--", command, "mcp"]

    if client_name == "codebuddy":
        if mode == "http":
            return [cli_path, "mcp", "add", "mnemo", "-s", "user", "-t", "http", url]
        else:
            return [cli_path, "mcp", "add", "mnemo", "-s", "user", "--", command, "mcp"]

    if client_name == "codex-cli":
        if mode == "http":
            return [cli_path, "mcp", "add", "mnemo", "--url", url]
        else:
            return [cli_path, "mcp", "add", "mnemo", "--", command, "mcp"]

    if client_name == "gemini-cli":
        if mode == "http":
            return [cli_path, "mcp", "add", "-s", "user", "--transport", "http", "mnemo", url]
        else:
            return [cli_path, "mcp", "add", "-s", "user", "--trust", "mnemo", "--", command, "mcp"]

    if client_name == "cursor":
        if mode == "http":
            payload = json.dumps({"name": "mnemo", "type": "http", "url": url})
            return [cli_path, "--add-mcp", payload]
        else:
            payload = json.dumps({"name": "mnemo", "command": command, "args": ["mcp"]})
            return [cli_path, "--add-mcp", payload]

    if client_name == "github-copilot-cli":
        # GitHub Copilot CLI uses /mcp add (interactive subcommand).
        # CLI approach is unreliable for non-interactive use, so we treat
        # it as file-only and let the file-based fallback handle it.
        return None

    return None


def _cli_uninstall_args(client_name: str, cli_path: str) -> list[str] | None:
    """Build CLI args for removing mnemo MCP. Returns None if not supported."""
    if client_name == "claude-code":
        return [cli_path, "mcp", "remove", "mnemo", "-s", "user"]

    if client_name == "qwen-code":
        return [cli_path, "mcp", "remove", "mnemo", "-s", "user"]

    if client_name == "codebuddy":
        return [cli_path, "mcp", "remove", "mnemo", "-s", "user"]

    if client_name == "codex-cli":
        return [cli_path, "mcp", "remove", "mnemo"]

    if client_name == "gemini-cli":
        return [cli_path, "mcp", "remove", "-s", "user", "mnemo"]

    # Cursor has no remove command — fallback to file edit
    if client_name == "cursor":
        return None

    # GitHub Copilot CLI has no non-interactive remove command
    if client_name == "github-copilot-cli":
        return None

    return None


def _run_cli(args: list[str], timeout: int = 15) -> bool:
    """Run a CLI command, return True on success."""
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


# ---------------------------------------------------------------------------
# File-based fallback: JSON
# ---------------------------------------------------------------------------

def _stdio_block(command: str = "mnemo", client_name: str = "") -> dict:
    block: dict = {"command": command, "args": ["mcp"]}
    if client_name == "github-copilot-cli":
        block["type"] = "local"
        block["tools"] = ["*"]
    return block


def _http_block(port: int, client_name: str = "") -> dict:
    url = f"http://127.0.0.1:{port}/mcp/http/mcp"
    if client_name in ("qwen-code", "gemini-cli"):
        return {"httpUrl": url}
    block: dict = {
        "type": "http",
        "url": url,
        "disabled": False,
    }
    if client_name == "github-copilot-cli":
        block["tools"] = ["*"]
    return block


def _build_block(mode: Mode, port: int, command: str, client_name: str = "") -> dict:
    if mode == "stdio":
        return _stdio_block(command=command, client_name=client_name)
    if mode == "http":
        return _http_block(port=port, client_name=client_name)
    raise ValueError(f"Unsupported mode: {mode!r}")


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
    client_name: str = "",
) -> bool:
    data = _read_json(path)
    servers = data.setdefault(field, {})
    desired = _build_block(mode=mode, port=port, command=command, client_name=client_name)

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
    modified = False

    servers = data.get(field) or {}
    if "mnemo" in servers:
        del data[field]["mnemo"]
        if not data[field]:
            del data[field]
        modified = True

    projects = data.get("projects")
    if isinstance(projects, dict):
        for _proj_path, proj_val in projects.items():
            if isinstance(proj_val, dict):
                proj_servers = proj_val.get(field)
                if isinstance(proj_servers, dict) and "mnemo" in proj_servers:
                    del proj_servers["mnemo"]
                    if not proj_servers:
                        del proj_val[field]
                    modified = True

    if not modified:
        return False
    _backup(path)
    _write_json(path, data)
    return True


# ---------------------------------------------------------------------------
# File-based fallback: TOML
# ---------------------------------------------------------------------------

def _toml_section_header(field: str) -> str:
    return f"[{field}.mnemo]"


def _strip_toml_section(text: str, header: str) -> str:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped == header:
            skipping = True
            continue
        if skipping:
            if stripped.startswith("[") and stripped.endswith("]"):
                skipping = False
                out.append(line)
                continue
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

    if mode == "stdio":
        body = (
            f"{header}\n"
            f'command = "{command}"\n'
            f'args = ["mcp"]\n'
        )
    else:
        body = (
            f"{header}\n"
            f'url = "http://127.0.0.1:{port}/mcp/http/mcp"\n'
        )

    if header in text:
        existing = _extract_toml_section(text, header)
        if existing.strip() == body.strip():
            return False
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
# Public API
# ---------------------------------------------------------------------------

_FILE_ONLY_CLIENTS = {"cursor", "windsurf", "github-copilot-cli"}


def inject_mcp_config(
    config_path: str,
    format: str = "json",
    port: int = 8787,
    mode: Mode = "stdio",
    command: str = "mnemo",
    mcp_field: str = "mcpServers",
    client_name: str = "",
) -> bool:
    """Idempotent injection of mnemo MCP server config into a client.

    Strategy: try CLI first, fallback to file write.
    Some clients (Cursor) have unreliable CLIs — always use file write.

    Returns True if config was modified, False if already up-to-date.
    """
    # For reliable CLI clients, try CLI first
    if client_name not in _FILE_ONLY_CLIENTS:
        cli_path = _find_cli(_CLIENT_CLI_MAP.get(client_name, []))
        if cli_path:
            args = _cli_install_args(client_name, cli_path, mode, port, command)
            if args and _run_cli(args):
                return True

    # File write (primary for _FILE_ONLY_CLIENTS, fallback for others)
    path = Path(config_path).expanduser()
    if format == "json":
        return _inject_json(path, mcp_field, mode, port, command, client_name=client_name)
    if format == "toml":
        return _inject_toml(path, mcp_field, mode, port, command)
    raise ValueError(f"Unsupported format: {format!r}. Use 'json' or 'toml'.")


def remove_mcp_config(
    config_path: str,
    format: str = "json",
    mcp_field: str = "mcpServers",
    client_name: str = "",
) -> bool:
    """Idempotently remove the mnemo entry from a client config.

    Strategy: try CLI first, then always ensure file is clean.
    Some agent CLIs (e.g. qwen) report success but don't actually
    remove the entry from the config file, so we always verify and
    clean up via direct file edit.

    Returns True if config was modified, False if mnemo wasn't present.
    """
    # Try CLI first (handles internal state the file might not reflect)
    cli_path = _find_cli(_CLIENT_CLI_MAP.get(client_name, []))
    if cli_path:
        args = _cli_uninstall_args(client_name, cli_path)
        if args:
            _run_cli(args)

    # Always ensure the config file is clean regardless of CLI result
    path = Path(config_path).expanduser()
    if format == "json":
        return _remove_json(path, mcp_field)
    if format == "toml":
        return _remove_toml(path, mcp_field)
    raise ValueError(f"Unsupported format: {format!r}. Use 'json' or 'toml'.")
