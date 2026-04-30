"""CLI implementation for `mnemo setup` — detects AI clients and injects MCP config + prompts."""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console

from mnemo.setup.client_detector import detect_clients
from mnemo.setup.config_writer import inject_mcp_config
from mnemo.setup.prompt_template import inject_prompt

console = Console()

# Clients whose prompt file is project-level (not global)
_PROJECT_LEVEL_PROMPT = {"cursor", "codex-cli"}

# Display names for nicer output
_DISPLAY_NAMES = {
    "claude-code": "Claude Code",
    "cursor": "Cursor",
    "codex-cli": "Codex CLI",
}


def setup_command(
    port: int = typer.Option(8787, "--port", "-p", help="mnemo server port."),
    skip_prompt: bool = typer.Option(
        False, "--skip-prompt", help="Skip system prompt injection."
    ),
) -> None:
    """Detect AI clients and configure mnemo MCP server + system prompts."""

    # Step 1: detect clients
    console.print("\n[bold]🔍 Detecting AI clients...[/]\n")

    clients = detect_clients()

    for client in clients:
        name = _DISPLAY_NAMES.get(client["name"], client["name"])
        if client["supported"]:
            console.print(f"  [green]✓[/] {name:<15} {client['config_path']}")
        else:
            console.print(f"  [red]✗[/] {name:<15} not installed")

    supported = [c for c in clients if c["supported"]]
    if not supported:
        console.print("\n[yellow]No supported AI clients detected. Nothing to configure.[/]")
        raise typer.Exit(code=0)

    # Step 2: configure each supported client
    console.print(f"\n[bold]⚡ Configuring mnemo (port {port})...[/]\n")

    configured = []
    skipped = []

    for client in supported:
        name = _DISPLAY_NAMES.get(client["name"], client["name"])
        console.print(f"  {name}:")

        # Inject MCP config
        try:
            modified = inject_mcp_config(
                config_path=client["config_path"],
                format=client["format"],
                port=port,
            )
            if modified:
                console.print(f"    [green]✓[/] MCP server → {client['config_path']}")
            else:
                console.print(f"    [green]✓[/] MCP server → {client['config_path']} [dim](already configured)[/]")
            configured.append(client["name"])
        except Exception as exc:
            console.print(f"    [red]✗[/] MCP server failed: {exc}")
            skipped.append(client["name"])
            continue

        # Inject system prompt
        if skip_prompt:
            console.print(f"    [dim]⊘[/] System prompt skipped (--skip-prompt)")
        elif client["name"] in _PROJECT_LEVEL_PROMPT:
            console.print(
                f"    [dim]⊘[/] System prompt skipped "
                f"(project-level only: {client['prompt_path']})"
            )
        else:
            try:
                prompt_modified = inject_prompt(client["prompt_path"])
                if prompt_modified:
                    console.print(f"    [green]✓[/] System prompt → {client['prompt_path']}")
                else:
                    console.print(
                        f"    [green]✓[/] System prompt → {client['prompt_path']} [dim](already present)[/]"
                    )
            except Exception as exc:
                console.print(f"    [yellow]⚠[/] System prompt failed: {exc}")

    # Step 3: summary
    console.print(f"\n[bold green]✅ Done![/] Restart your AI clients to activate mnemo.\n")
    console.print("  [dim]mnemo serve          # Start the server[/]")
    console.print(f"  [dim]open http://127.0.0.1:{port}/viz/[/]\n")
