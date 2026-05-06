"""Mnemo command-line interface — a thin wrapper over KnowledgeService.

Commands (see task #2):
    mnemo create --title ... --summary ... --body ... [--tags ...]
    mnemo search QUERY [--scope ...] [--limit N]
    mnemo get ID_OR_TITLE
    mnemo update ID [--title ...] [--summary ...] [--body ...] [--tags ...]
    mnemo delete ID
    mnemo related ID_OR_TITLE [--depth N]
    mnemo tags [--scope ...]
    mnemo tag-search "tag1,tag2" [--scope ...]

All async work goes through asyncio.run() so each invocation gets a fresh
event loop. First invocation auto-creates the SQLite schema via init_db().
"""

from __future__ import annotations

import asyncio
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from mnemo import __version__
from mnemo.config import MnemoConfig, Scope
from mnemo.db import get_session_factory, init_db
from mnemo.services import feedback_service
from mnemo.services.knowledge_service import KnowledgeService

app = typer.Typer(
    help="mnemo — agent-first knowledge base (SQLite backed).",
    no_args_is_help=True,
    add_completion=False,
)

console = Console()
err_console = Console(stderr=True)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"mnemo {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Print mnemo version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """mnemo — agent-first knowledge base (SQLite backed)."""
    return None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run an awaitable on a fresh event loop, auto-initializing the DB."""

    async def _boot():
        await init_db()
        return await coro

    return asyncio.run(_boot())


def _parse_tags(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


def _parse_id_or_title(value: str) -> int | str:
    stripped = value.strip()
    if stripped.isdigit():
        return int(stripped)
    return stripped


def _print_knowledge(item: dict) -> None:
    tags = ", ".join(item.get("tags") or []) or "—"
    related = ", ".join(item.get("related") or []) or "—"
    console.print(f"[bold cyan]#{item['id']}[/] [bold]{item['title']}[/]")
    console.print(f"[dim]scope:[/] {item['scope']}", end="")
    if item.get("project_name"):
        console.print(f" [dim]project:[/] {item['project_name']}", end="")
    console.print()
    console.print(
        f"[dim]status:[/] {item.get('status') or '—'} "
        f"[dim]claim_type:[/] {item.get('claim_type') or '—'} "
        f"[dim]version:[/] {item.get('version') or '—'}"
    )
    successor = item.get("superseded_by")
    if successor:
        console.print(
            f"[yellow]superseded by[/] #{successor['id']} "
            f"[bold]{successor['title']}[/]"
        )
    console.print(f"[dim]tags:[/] {tags}")
    console.print(f"[dim]related:[/] {related}")
    console.print(f"[dim]summary:[/] {item['summary']}")
    console.print()
    console.print(item["content"])


def _print_summary_table(items: list[dict], title: str) -> None:
    if not items:
        console.print(f"[yellow]No {title} found.[/]")
        return

    table = Table(title=title, show_lines=False, expand=False)
    table.add_column("id", justify="right", style="cyan", no_wrap=True)
    table.add_column("title", style="bold")
    table.add_column("scope", no_wrap=True)
    table.add_column("tags")
    table.add_column("summary")
    for it in items:
        table.add_row(
            str(it["id"]),
            it["title"],
            it.get("scope") or "",
            ", ".join(it.get("tags") or []),
            it.get("summary") or "",
        )
    console.print(table)


def _service() -> KnowledgeService:
    return KnowledgeService(config=MnemoConfig())


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

@app.command()
def create(
    title: str = typer.Option(..., "--title", "-t", help="Knowledge title (must be unique)."),
    summary: str = typer.Option(..., "--summary", "-s", help="Short description."),
    body: str = typer.Option(..., "--body", "-b", help="Full content (markdown allowed)."),
    tags: Optional[str] = typer.Option(
        None, "--tags", help="Comma-separated tags, e.g. 'python,db'."
    ),
    scope: Scope = typer.Option(Scope.GLOBAL, "--scope", help="global / project / session."),
    project_name: Optional[str] = typer.Option(
        None, "--project", help="Project name when scope=project."
    ),
    session_id: Optional[str] = typer.Option(
        None, "--session", help="Session id when scope=session."
    ),
    source: Optional[str] = typer.Option(None, "--source", help="Origin label."),
    claim_type: Optional[str] = typer.Option(
        None,
        "--claim-type",
        help="Claim type: fact / decision / procedure / hypothesis.",
    ),
    related: Optional[str] = typer.Option(
        None, "--related", help="Comma-separated related titles."
    ),
) -> None:
    """Create a new knowledge entry."""
    try:
        item = _run(
            _service().create_knowledge(
                title=title,
                summary=summary,
                content=body,
                tags=_parse_tags(tags),
                scope=scope.value,
                project_name=project_name,
                session_id=session_id,
                source=source,
                claim_type=claim_type,
                related_titles=_parse_tags(related),
            )
        )
    except Exception as exc:  # surface e.g. UNIQUE constraint violations cleanly
        err_console.print(f"[red]create failed:[/] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]Created[/] #{item['id']} [bold]{item['title']}[/]")
    if item.get("related"):
        console.print(f"[dim]auto-linked:[/] {', '.join(item['related'])}")


@app.command()
def search(
    query: str = typer.Argument(..., help="Free-form query (FTS5 matched)."),
    scope: Optional[Scope] = typer.Option(None, "--scope", help="Filter by scope."),
    project_name: Optional[str] = typer.Option(None, "--project", help="Filter by project."),
    limit: int = typer.Option(20, "--limit", "-n", min=1, max=200),
    include_archived: bool = typer.Option(
        False,
        "--include-archived/--no-include-archived",
        help="Include entries whose status is 'archived' (hidden by default).",
    ),
) -> None:
    """Full-text search across knowledge entries."""
    hits = _run(
        _service().search(
            query,
            scope=scope.value if scope else None,
            project_name=project_name,
            limit=limit,
            include_archived=include_archived,
        )
    )
    _print_summary_table(hits, title=f'search: "{query}"')


@app.command()
def get(
    id_or_title: str = typer.Argument(..., help="Numeric id or exact title."),
) -> None:
    """Show a single knowledge entry in full."""
    item = _run(_service().get_knowledge(_parse_id_or_title(id_or_title)))
    if item is None:
        err_console.print(f"[red]not found:[/] {id_or_title}")
        raise typer.Exit(code=1)
    _print_knowledge(item)


@app.command()
def update(
    knowledge_id: int = typer.Argument(..., help="Numeric knowledge id."),
    title: Optional[str] = typer.Option(None, "--title", "-t"),
    summary: Optional[str] = typer.Option(None, "--summary", "-s"),
    body: Optional[str] = typer.Option(None, "--body", "-b"),
    tags: Optional[str] = typer.Option(None, "--tags"),
    scope: Optional[Scope] = typer.Option(None, "--scope"),
    project_name: Optional[str] = typer.Option(None, "--project"),
    session_id: Optional[str] = typer.Option(None, "--session"),
    source: Optional[str] = typer.Option(None, "--source"),
) -> None:
    """Update fields on an existing entry. Only provided flags are applied."""
    fields: dict = {}
    if title is not None:
        fields["title"] = title
    if summary is not None:
        fields["summary"] = summary
    if body is not None:
        fields["content"] = body
    if tags is not None:
        fields["tags"] = _parse_tags(tags)
    if scope is not None:
        fields["scope"] = scope.value
    if project_name is not None:
        fields["project_name"] = project_name
    if session_id is not None:
        fields["session_id"] = session_id
    if source is not None:
        fields["source"] = source

    if not fields:
        err_console.print("[red]update failed:[/] no fields provided")
        raise typer.Exit(code=2)

    try:
        item = _run(_service().update_knowledge(knowledge_id, **fields))
    except ValueError as exc:
        err_console.print(f"[red]update failed:[/] {exc}")
        raise typer.Exit(code=1) from exc

    supersedes = item.get("supersedes_id")
    if supersedes is not None:
        console.print(
            f"[green]Created new version[/] #{item['id']} "
            f"[bold]{item['title']}[/] (supersedes #{supersedes})"
        )
    else:
        console.print(f"[green]Updated[/] #{item['id']} [bold]{item['title']}[/]")


@app.command()
def delete(
    knowledge_id: int = typer.Argument(..., help="Numeric knowledge id."),
) -> None:
    """Delete a knowledge entry (along with its relations and FTS row)."""
    ok = _run(_service().delete_knowledge(knowledge_id))
    if not ok:
        err_console.print(f"[red]not found:[/] {knowledge_id}")
        raise typer.Exit(code=1)
    console.print(f"[green]Deleted[/] #{knowledge_id}")


@app.command()
def archive(
    knowledge_id: int = typer.Argument(..., help="Numeric knowledge id."),
    reason: Optional[str] = typer.Option(
        None, "--reason", "-r", help="Free-form reason recorded in the audit event."
    ),
) -> None:
    """Archive a knowledge entry (hides from default search, keeps data)."""
    result = _run(_service().archive_knowledge(knowledge_id, reason=reason))
    if not result.get("success"):
        err_console.print(
            f"[red]archive failed:[/] {result.get('error') or 'unknown error'}"
        )
        raise typer.Exit(code=1)
    msg = f"[green]Archived[/] #{knowledge_id}"
    if reason:
        msg += f" [dim]reason:[/] {reason}"
    console.print(msg)


@app.command()
def unarchive(
    knowledge_id: int = typer.Argument(..., help="Numeric knowledge id."),
) -> None:
    """Restore an archived knowledge entry back to active."""
    result = _run(_service().unarchive_knowledge(knowledge_id))
    if not result.get("success"):
        err_console.print(
            f"[red]unarchive failed:[/] {result.get('error') or 'unknown error'}"
        )
        raise typer.Exit(code=1)
    console.print(f"[green]Unarchived[/] #{knowledge_id}")


@app.command()
def feedback(
    knowledge_id: int = typer.Argument(..., help="Numeric knowledge id."),
    signal: str = typer.Option(
        ...,
        "--signal",
        help="Feedback signal: helpful / misleading / outdated.",
    ),
    reason: Optional[str] = typer.Option(
        None, "--reason", "-r", help="Free-form justification (stored verbatim)."
    ),
    actor: str = typer.Option(
        "agent:unknown", "--actor", help="Caller identity for 24h dedup."
    ),
) -> None:
    """Record agent feedback on a knowledge entry (TECH_PLAN §5)."""
    config = MnemoConfig()
    if not getattr(config, "feedback_loop_enabled", True):
        err_console.print("[yellow]feedback skipped:[/] feature disabled")
        raise typer.Exit(code=0)

    async def _call():
        factory = get_session_factory(config)
        async with factory() as session:
            return await feedback_service.record_feedback(
                session,
                knowledge_id=knowledge_id,
                signal=signal,
                reason=reason,
                actor=actor,
                config=config,
            )

    try:
        result = _run(_call())
    except Exception as exc:
        err_console.print(f"[red]feedback failed:[/] {exc}")
        raise typer.Exit(code=1) from exc
    if not result.get("success"):
        err_console.print(
            f"[red]feedback failed:[/] {result.get('reason') or result.get('error') or 'unknown error'}"
        )
        raise typer.Exit(code=1)
    console.print(
        f"[green]Recorded[/] feedback on #{knowledge_id} "
        f"[dim]signal:[/] {signal} [dim]actor:[/] {actor}"
    )


@app.command()
def related(
    id_or_title: str = typer.Argument(..., help="Numeric id or exact title."),
    depth: int = typer.Option(1, "--depth", "-d", min=1, max=5),
) -> None:
    """List knowledge entries connected to the given one."""
    items = _run(
        _service().get_related(_parse_id_or_title(id_or_title), depth=depth)
    )
    _print_summary_table(items, title=f"related (depth={depth}): {id_or_title}")


@app.command()
def tags(
    scope: Optional[Scope] = typer.Option(None, "--scope"),
) -> None:
    """List all tags, optionally filtered by scope."""
    result = _run(_service().list_tags(scope=scope.value if scope else None))
    if not result:
        console.print("[yellow]No tags yet.[/]")
        return
    for tag in result:
        console.print(f"• {tag}")


# ---------------------------------------------------------------------------
# monitor subcommands
# ---------------------------------------------------------------------------

monitor_app = typer.Typer(
    help="Inspect monitoring data (events, stats).",
    no_args_is_help=True,
)
app.add_typer(monitor_app, name="monitor")


DOMAINS = ("search_quality", "knowledge_health", "behavior_compliance")


def _load_rules_for_domain(domain: str) -> list:
    """Import a domain's rule set lazily — rules/ is authored by a peer.

    Returns [] when the module or loader is missing so the runner boots even
    before the rules package lands.
    """
    try:
        from mnemo.monitor import rules as rules_pkg  # type: ignore
    except ImportError:
        return []
    loader = getattr(rules_pkg, "load_rules", None)
    if loader is None:
        return []
    try:
        return list(loader(domain=domain))
    except Exception:  # noqa: BLE001 — rules code is third-party to us here
        import logging

        logging.getLogger(__name__).exception(
            "rules.load_rules(domain=%s) failed", domain
        )
        return []


@monitor_app.command("run")
def monitor_run(
    domain: Optional[str] = typer.Option(
        None,
        "--domain",
        help=(
            "Run a single domain only (search_quality / knowledge_health / "
            "behavior_compliance). Default: embedded mode, all three."
        ),
    ),
    polling_s: float = typer.Option(
        10.0, "--polling-s", min=1.0, help="Poll interval seconds."
    ),
) -> None:
    """Start the monitor agent. Embedded mode runs all 3 domains in-process."""
    import asyncio
    import signal

    from mnemo.monitor.notifier import Notifier
    from mnemo.monitor.runner import MonitorRunner

    if domain is not None and domain not in DOMAINS:
        err_console.print(
            f"[red]invalid --domain:[/] {domain} "
            f"(expected one of {', '.join(DOMAINS)})"
        )
        raise typer.Exit(code=2)

    domains = [domain] if domain else list(DOMAINS)

    async def _main() -> None:
        await init_db()
        factory = get_session_factory()
        notifier = Notifier()
        shutdown = asyncio.Event()

        loop = asyncio.get_running_loop()

        def _handle_signal() -> None:
            if not shutdown.is_set():
                console.print("\n[yellow]monitor shutdown requested[/]")
                shutdown.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _handle_signal)
            except NotImplementedError:  # pragma: no cover — Windows fallback
                signal.signal(sig, lambda *_: _handle_signal())

        runners: list[MonitorRunner] = []
        for d in domains:
            rules = _load_rules_for_domain(d)
            runners.append(
                MonitorRunner(
                    domain=d,
                    rules=rules,
                    session_factory=factory,
                    notifier=notifier,
                    polling_s=polling_s,
                    shutdown=shutdown,
                )
            )
            console.print(
                f"[green]monitor domain=[/]{d} "
                f"[dim]rules={len(rules)} polling={polling_s:.1f}s[/]"
            )

        try:
            await asyncio.gather(*(r.run() for r in runners))
        except asyncio.CancelledError:  # pragma: no cover
            pass

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        console.print("[yellow]monitor interrupted[/]")


@monitor_app.command("stats")
def monitor_stats(
    hours: int = typer.Option(
        24, "--hours", "-h", min=1, max=720, help="Rolling window in hours."
    ),
) -> None:
    """Show tool-call counts, average latency, and empty-search ratio."""
    from mnemo.monitor import queries as monitor_queries

    async def _call():
        factory = get_session_factory()
        async with factory() as session:
            counts = await monitor_queries.event_counts_by_tool(session, hours=hours)
            latency = await monitor_queries.avg_latency_by_tool(session, hours=hours)
            empty = await monitor_queries.search_empty_ratio(session, hours=hours)
            return counts, latency, empty

    counts, latency, empty = _run(_call())

    console.print(f"[bold]monitor stats[/] — last {hours}h")
    if not counts:
        console.print("[yellow]No tool calls recorded in this window.[/]")
    else:
        table = Table(show_lines=False, expand=False)
        table.add_column("tool", style="cyan", no_wrap=True)
        table.add_column("calls", justify="right")
        table.add_column("avg latency (ms)", justify="right")
        for tool in sorted(counts):
            avg = latency.get(tool, 0.0)
            table.add_row(tool, str(counts[tool]), f"{avg:.1f}")
        console.print(table)

    console.print(f"[dim]search empty ratio:[/] {empty:.1%}")


@app.command("mcp")
def mcp_command() -> None:
    """Start the mnemo MCP server over stdio (for uvx / claude mcp add)."""
    from mnemo.mcp.server import main as mcp_main

    mcp_main()


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host (default localhost only)."),
    port: int = typer.Option(8787, "--port", "-p", help="Bind port."),
    log_level: str = typer.Option("info", "--log-level"),
    reload: bool = typer.Option(
        False, "--reload", help="Enable uvicorn auto-reload (development)."
    ),
    viz: bool = typer.Option(
        True, "--viz/--no-viz", help="Serve the knowledge graph visualization at /viz."
    ),
) -> None:
    """Start the mnemo HTTP server (MCP-over-SSE + REST)."""
    import os

    import uvicorn

    os.environ["MNEMO_VIZ_ENABLED"] = "1" if viz else "0"
    uvicorn.run(
        "mnemo.server.app:create_app",
        host=host,
        port=port,
        log_level=log_level,
        reload=reload,
        factory=True,
    )


@app.command("setup")
def setup(
    port: int = typer.Option(8787, "--port", "-p", help="mnemo server port (HTTP mode only)."),
    mode: str = typer.Option(
        "stdio",
        "--mode",
        help="MCP transport: 'stdio' (default, zero background process) or 'http'.",
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
        help="Auto-write to every detected client (default).",
    ),
    uninstall: bool = typer.Option(
        False,
        "--uninstall",
        help="Remove mnemo entries (MCP config + prompt block) from every detected client.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would change without writing files."
    ),
) -> None:
    """Detect AI clients and configure mnemo MCP server + system prompts."""
    from mnemo.setup.command import setup_command

    setup_command(
        port=port,
        mode=mode,
        skip_prompt=skip_prompt,
        no_project_prompts=no_project_prompts,
        auto=auto,
        uninstall=uninstall,
        dry_run=dry_run,
    )


@app.command("tag-search")
def tag_search(
    tags_csv: str = typer.Argument(..., help="Comma-separated tags, intersection match."),
    scope: Optional[Scope] = typer.Option(None, "--scope"),
    limit: int = typer.Option(20, "--limit", "-n", min=1, max=200),
) -> None:
    """Find knowledge entries that contain ALL of the given tags."""
    tag_list = _parse_tags(tags_csv)
    if not tag_list:
        err_console.print("[red]tag-search failed:[/] no tags provided")
        raise typer.Exit(code=2)

    items = _run(
        _service().search_by_tag(
            tag_list,
            scope=scope.value if scope else None,
            limit=limit,
        )
    )
    _print_summary_table(items, title=f"tags: {', '.join(tag_list)}")


if __name__ == "__main__":
    app()
