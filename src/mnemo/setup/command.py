"""CLI implementation for `mnemo setup` — configures detected AI clients.

Default behavior is "stdio + auto + write prompts": the client spawns
`mnemo mcp` directly (zero background process), MCP entries land in every
detected client, and the agent-facing prompt is injected into the per-client
prompt file (CLAUDE.md / .cursorrules / AGENTS.md).

Pass `--mode http` to instead point clients at a running `mnemo serve` (good
for shared multi-client / visualization scenarios).

Pass `--uninstall` to idempotently remove every mnemo entry the previous
runs of setup wrote.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from mnemo.setup.client_detector import detect_clients
from mnemo.setup.config_writer import inject_mcp_config, remove_mcp_config
from mnemo.setup.prompt_template import inject_prompt, remove_prompt

console = Console()
err_console = Console(stderr=True)

# Display names for nicer output.
_DISPLAY_NAMES = {
    "claude-code": "Claude Code",
    "claude-desktop": "Claude Desktop",
    "cursor": "Cursor",
    "codex-cli": "Codex CLI",
    "qwen-code": "Qwen Code",
    "gemini-cli": "Gemini CLI",
    "codebuddy": "CodeBuddy",
    "windsurf": "Windsurf",
    "github-copilot-cli": "GitHub Copilot CLI",
}

# Clients whose prompt file is project-level — only injected when running
# setup inside a project directory and the user hasn't passed --no-project-prompts.
_PROJECT_LEVEL_PROMPT = {"cursor", "codex-cli"}


def _prompt_entries(client: dict) -> list[dict[str, str]]:
    entries = client.get("prompt_paths") or []
    if entries:
        return entries
    prompt_path = client.get("prompt_path")
    prompt_target = client.get("prompt_target")
    if prompt_path is None or prompt_target is None:
        return []
    return [{"path": prompt_path, "target": prompt_target}]


def _resolve_mnemo_command() -> str:
    """Pick the executable string used in stdio MCP entries.

    Prefers the absolute path of the running `mnemo` binary so the client
    config keeps working even if the user's PATH changes later. Falls back
    to the bare name "mnemo" if we can't resolve it (e.g. dev `python -m`).
    """
    # 1. PyInstaller-frozen binary: sys.argv[0] is the bundled exe path
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve())

    # 2. `mnemo` resolvable on PATH
    found = shutil.which("mnemo")
    if found:
        return str(Path(found).resolve())

    # 3. Last resort: relative name. Works if user later puts it on PATH.
    return "mnemo"


def setup_command(
    port: int = typer.Option(8787, "--port", "-p", help="mnemo server port (HTTP mode only)."),
    mode: str = typer.Option(
        "stdio",
        "--mode",
        help="MCP transport: 'stdio' (default, zero background process) or 'http'.",
    ),
    client: Optional[str] = typer.Option(
        None,
        "--client", "-c",
        help="Only target a specific client (e.g. 'claude-code', 'cursor').",
    ),
    skip_prompt: bool = typer.Option(
        False, "--skip-prompt", help="Skip system prompt injection."
    ),
    no_project_prompts: bool = typer.Option(
        False,
        "--no-project-prompts",
        help="Skip injecting .cursorrules / AGENTS.md into the current directory.",
    ),
    auto: bool = typer.Option(
        True,
        "--auto/--no-auto",
        help="Auto-write to every detected client (default). "
             "Pass --no-auto to abort if any client write fails.",
    ),
    uninstall: bool = typer.Option(
        False,
        "--uninstall",
        help="Remove mnemo entries (MCP config + prompt block) from every detected client.",
    ),
    mcp_only: bool = typer.Option(
        False,
        "--mcp-only",
        help="Only change MCP config; leave prompt files untouched.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would change without writing files."
    ),
) -> None:
    """Detect AI clients and configure mnemo MCP server + system prompts."""

    if mode not in ("stdio", "http"):
        err_console.print(f"[red]Invalid --mode:[/] {mode!r} (use 'stdio' or 'http')")
        raise typer.Exit(code=2)

    if uninstall:
        return _run_uninstall(
            client=client,
            no_project_prompts=no_project_prompts,
            mcp_only=mcp_only,
            dry_run=dry_run,
        )

    return _run_install(
        mode=mode,
        port=port,
        client=client,
        skip_prompt=skip_prompt or mcp_only,
        no_project_prompts=no_project_prompts,
        auto=auto,
        dry_run=dry_run,
    )


def _run_install(
    *,
    mode: str,
    port: int,
    client: Optional[str] = None,
    skip_prompt: bool,
    no_project_prompts: bool,
    auto: bool,
    dry_run: bool,
) -> None:
    console.print("\n[bold]Detecting AI clients...[/]\n")

    clients = detect_clients()
    for c in clients:
        name = _DISPLAY_NAMES.get(c["name"], c["name"])
        if c["supported"]:
            console.print(f"  [green]+[/] {name:<16} {c['config_path']}")
        else:
            console.print(f"  [dim]-[/] {name:<16} not installed")

    supported = [c for c in clients if c["supported"]]

    if client:
        supported = [c for c in supported if c["name"] == client]
        if not supported:
            console.print(f"\n[yellow]Client '{client}' not found or not installed.[/]")
            raise typer.Exit(code=1)
    if not supported:
        console.print("\n[yellow]No supported AI clients detected. Nothing to configure.[/]")
        raise typer.Exit(code=0)

    command = _resolve_mnemo_command()
    label = f"stdio command={command}" if mode == "stdio" else f"http port={port}"
    prefix = "[bold]Would configure[/]" if dry_run else "[bold]Configuring[/]"
    console.print(f"\n{prefix} mnemo ({label})...\n")

    failures: list[str] = []

    for client in supported:
        name = _DISPLAY_NAMES.get(client["name"], client["name"])
        console.print(f"  {name}:")

        try:
            if dry_run:
                console.print(
                    f"    [dim]would write MCP -> {client['config_path']}[/]"
                )
            else:
                modified = inject_mcp_config(
                    config_path=client["config_path"],
                    format=client["format"],
                    port=port,
                    mode=mode,
                    command=command,
                    mcp_field=client["mcp_field"],
                    client_name=client["name"],
                )
                if modified:
                    console.print(f"    [green]ok[/]  MCP server -> {client['config_path']}")
                else:
                    console.print(
                        f"    [green]ok[/]  MCP server -> {client['config_path']} "
                        f"[dim](already configured)[/]"
                    )
        except Exception as exc:
            console.print(f"    [red]err[/] MCP server failed: {exc}")
            failures.append(client["name"])
            if not auto:
                raise typer.Exit(code=1) from exc
            continue

        prompt_entries = _prompt_entries(client)

        if skip_prompt or not prompt_entries:
            if not prompt_entries:
                console.print(f"    [dim]skip prompt (client has no prompt file)[/]")
            else:
                console.print(f"    [dim]skip prompt (--skip-prompt)[/]")
            continue

        if client["name"] in _PROJECT_LEVEL_PROMPT and no_project_prompts:
            console.print(
                f"    [dim]skip prompt (project-level, --no-project-prompts)[/]"
            )
            continue

        for prompt_entry in prompt_entries:
            prompt_path = prompt_entry["path"]
            prompt_target = prompt_entry["target"]
            try:
                if dry_run:
                    console.print(f"    [dim]would write prompt -> {prompt_path}[/]")
                else:
                    changed = inject_prompt(prompt_path, target=prompt_target)
                    if changed:
                        console.print(f"    [green]ok[/]  Prompt -> {prompt_path}")
                    else:
                        console.print(
                            f"    [green]ok[/]  Prompt -> {prompt_path} [dim](already up-to-date)[/]"
                        )
            except Exception as exc:
                console.print(f"    [yellow]warn[/] Prompt injection failed: {exc}")

    if failures:
        err_console.print(
            f"\n[yellow]Setup finished with errors on:[/] {', '.join(failures)}"
        )
        raise typer.Exit(code=1)

    if dry_run:
        console.print(
            f"\n[bold cyan]Dry-run complete.[/] "
            f"Re-run without --dry-run to apply.\n"
        )
        return

    console.print("\n[bold green]Done.[/] Restart your AI clients to activate mnemo.")
    if mode == "http":
        console.print("\n  [dim]Reminder: HTTP mode requires the server to be running:[/]")
        console.print(f"  [dim]    mnemo serve --port {port}[/]\n")
    else:
        console.print("  [dim]stdio mode is on — clients will spawn `mnemo mcp` automatically.[/]\n")


def _run_uninstall(
    *,
    client: Optional[str] = None,
    no_project_prompts: bool,
    mcp_only: bool,
    dry_run: bool,
) -> None:
    console.print("\n[bold]Uninstalling mnemo from detected clients...[/]\n")

    clients = detect_clients()
    targets = [c for c in clients if c["supported"]]
    if client:
        targets = [c for c in targets if c["name"] == client]

    any_change = False

    for entry in targets:
        name = _DISPLAY_NAMES.get(entry["name"], entry["name"])
        console.print(f"  {name}:")

        try:
            if dry_run:
                console.print(f"    [dim]would remove MCP entry from {entry['config_path']}[/]")
            else:
                removed = remove_mcp_config(
                    config_path=entry["config_path"],
                    format=entry["format"],
                    mcp_field=entry["mcp_field"],
                    client_name=entry["name"],
                )
                if removed:
                    console.print(f"    [green]ok[/]  MCP entry removed from {entry['config_path']}")
                    any_change = True
                else:
                    console.print(f"    [dim]MCP entry not present in {entry['config_path']}[/]")
        except Exception as exc:
            console.print(f"    [red]err[/] {exc}")
            continue

        if mcp_only:
            continue

        prompt_entries = _prompt_entries(entry)
        if not prompt_entries:
            continue
        if entry["name"] in _PROJECT_LEVEL_PROMPT and no_project_prompts:
            continue

        for prompt_entry in prompt_entries:
            prompt_path = prompt_entry["path"]
            try:
                if dry_run:
                    console.print(f"    [dim]would remove prompt block from {prompt_path}[/]")
                else:
                    removed = remove_prompt(prompt_path)
                    if removed:
                        console.print(f"    [green]ok[/]  Prompt block removed from {prompt_path}")
                        any_change = True
                    else:
                        console.print(f"    [dim]Prompt block not present in {prompt_path}[/]")
            except Exception as exc:
                console.print(f"    [yellow]warn[/] {exc}")

    if not any_change and not dry_run:
        console.print("\n[yellow]Nothing to remove — mnemo wasn't configured anywhere.[/]")
        return

    console.print("\n[bold green]Done.[/]\n")
