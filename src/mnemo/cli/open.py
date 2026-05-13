"""Open an installed AI agent (Cursor, Windsurf, etc.) in a working directory.

Uses each agent's native CLI command (cursor <dir>, windsurf <dir>) to launch
the application. If the command is not installed on PATH, that agent is skipped.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

# ---------------------------------------------------------------------------
# Agent definitions — extensible list of supported agents
#
# Each entry maps a stable id to:
#   name        – human-readable display name
#   binaries    – candidate executable names on PATH (first match wins)
#   description – one-line description shown in the picker
#
# To add a new agent, append a new entry here and ensure the binary accepts
# a directory as its first positional argument (e.g. `cursor /tmp/proj`).
# ---------------------------------------------------------------------------

_AGENT_REGISTRY: dict[str, dict] = {
    "cursor": {
        "name": "Cursor",
        "binaries": ["cursor"],
        "description": "AI-powered code editor (VS Code fork)",
    },
    "windsurf": {
        "name": "Windsurf",
        "binaries": ["windsurf"],
        "description": "AI coding assistant by Codeium",
    },
}


def _detect_available(registry: dict[str, dict] | None = None) -> list[dict]:
    """Return installed agents (binary found on PATH), ordered by registry key."""
    if registry is None:
        registry = _AGENT_REGISTRY

    available: list[dict] = []
    for agent_id, spec in registry.items():
        exe = _resolve_binary(spec.get("binaries", []))
        if exe is None:
            continue
        available.append(
            {
                "id": agent_id,
                "name": spec["name"],
                "binary": exe,
                "description": spec.get("description", ""),
            }
        )
    return available


def _resolve_binary(binaries: list[str]) -> str | None:
    """Return the first binary from the list that is found on PATH."""
    for bin_name in binaries:
        path = shutil.which(bin_name)
        if path is not None:
            return bin_name
    return None


def _pick_agent(available: list[dict]) -> dict | None:
    """Interactive agent picker using Rich formatting."""
    from rich.console import Console
    from rich.table import Table

    console = Console()

    if not available:
        console.print("[yellow]没有检测到已安装的 agent 客户端[/]")
        return None

    if len(available) == 1:
        agent = available[0]
        console.print(
            f"唯一可用 agent: [bold cyan]{agent['name']}[/] "
            f"(命令: {agent['binary']})"
        )
        return agent

    table = Table(title="已安装的 Agent", show_lines=False, expand=False)
    table.add_column("#", justify="right", style="cyan", no_wrap=True)
    table.add_column("Agent", style="bold")
    table.add_column("命令", style="dim")
    table.add_column("说明")
    for i, agent in enumerate(available, 1):
        table.add_row(
            str(i),
            agent["name"],
            agent["binary"],
            agent.get("description", ""),
        )
    console.print(table)

    # Prompt for selection
    selection = -1
    while selection < 1 or selection > len(available):
        try:
            raw = input(f"请选择 Agent (1-{len(available)}): ").strip()
            selection = int(raw)
        except (ValueError, EOFError, KeyboardInterrupt):
            return None

    return available[selection - 1]


def _launch(binary: str, directory: Path) -> bool:
    """Execute `<binary> <directory>` in the background.

    On macOS, the GUI-based agents (Cursor, Windsurf) detach immediately
    after signalling the main process, so we don't wait for them.
    """
    try:
        subprocess.Popen(
            [binary, str(directory)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # detach from parent process
        )
        return True
    except OSError:
        return False


def open_agent(
    agent_id: str | None = None,
    directory: str | None = None,
) -> None:
    """Entry point for the `mnemo open` command.

    Args:
        agent_id: Optional agent id to skip the picker.
        directory: Optional working directory; defaults to current directory.
    """
    from rich.console import Console
    from rich.prompt import Prompt

    console = Console()

    # Detect available agents
    available = _detect_available()

    if not available:
        console.print("[yellow]没有检测到已安装的 agent 客户端[/]")
        console.print(
            "[dim]提示: 请先在 Cursor 中运行 Cmd+Shift+P → "
            "\"Install 'cursor' command in PATH\"[/]"
        )
        raise SystemExit(0)

    # Select agent
    if agent_id:
        agent = next((a for a in available if a["id"] == agent_id), None)
        if agent is None:
            console.print(f"[red]未找到 agent: {agent_id}[/]")
            available_ids = ", ".join(a["id"] for a in available)
            console.print(f"[dim]可用: {available_ids}[/]")
            raise SystemExit(1)
    else:
        agent = _pick_agent(available)
        if agent is None:
            console.print("[yellow]已取消[/]")
            raise SystemExit(0)

    # Select directory
    if directory:
        work_dir = Path(directory).expanduser().resolve()
    else:
        default_dir = str(Path.cwd())
        work_dir_str = Prompt.ask(
            "工作目录",
            default=default_dir,
        )
        work_dir = Path(work_dir_str).expanduser().resolve()

    if not work_dir.exists():
        console.print(f"[red]目录不存在: {work_dir}[/]")
        raise SystemExit(1)
    if not work_dir.is_dir():
        console.print(f"[red]路径不是目录: {work_dir}[/]")
        raise SystemExit(1)

    console.print(
        f"正在打开 [bold cyan]{agent['name']}[/] "
        f"→ [dim]{work_dir}[/]"
    )

    ok = _launch(agent["binary"], work_dir)
    if not ok:
        console.print(
            f"[red]启动失败:[/] 无法执行 {agent['binary']}"
        )
        raise SystemExit(1)

    console.print(f"[green]已启动 {agent['name']}[/]")
