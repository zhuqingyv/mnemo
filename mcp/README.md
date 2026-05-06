# mnemo MCP Server

Local memory for AI agents today. Trainable local model memory tomorrow.

mnemo is distributed to end users **only as prebuilt binaries from GitHub Releases**. There is no PyPI package, no `pipx` package, and no npm package.

For normal installation, use the root [README.md](../README.md) or the agent-facing [AGENTS.md](../AGENTS.md).

## Quick Install

### macOS / Linux

```bash
curl -fsSL https://github.com/zhuqingyv/mnemo/releases/latest/download/install.sh | sh
```

### Windows PowerShell

```powershell
irm https://github.com/zhuqingyv/mnemo/releases/latest/download/install.ps1 | iex
```

The installer downloads the matching binary, verifies SHA256, installs `mnemo`, and runs:

```bash
mnemo setup --auto
```

That command writes the MCP config and agent prompt into every detected supported client.

Supported clients:

- Claude Code
- Claude Desktop
- Cursor
- Codex CLI

After installation, **restart your IDE / AI client**. MCP config is not hot-reloaded.

## Default MCP mode

By default, `mnemo setup --auto` writes a stdio MCP entry. Clients spawn `mnemo mcp` directly, so no background server is required.

The generated config looks like:

```json
{
  "mcpServers": {
    "mnemo": {
      "command": "/absolute/path/to/mnemo",
      "args": ["mcp"]
    }
  }
}
```

Do not hand-edit client config files unless you are intentionally debugging a client-specific issue. Prefer `mnemo setup --auto`, `mnemo setup --dry-run`, and `mnemo setup --uninstall`.

## HTTP mode

Use HTTP mode when multiple clients need to share one mnemo backend, or when you want the live visualization page:

```bash
mnemo setup --mode http --port 8787
mnemo serve --port 8787
open http://127.0.0.1:8787/viz/
```

## Verify Installation

```bash
mnemo --version
mnemo --help
```

Full smoke test: see [mcp/smoke_test.md](smoke_test.md).

## Available Tools (11)
- `search` — hybrid full-text + vector search across stored knowledge.
- `search_by_tag` — find entries carrying ALL given tags.
- `get_knowledge` — fetch full content by numeric id or exact title.
- `get_related` — traverse the relation graph from an entry.
- `list_tags` — list every distinct tag, optionally scoped.
- `create_knowledge` — create a new entry (returns write_gate dedup report).
- `update_knowledge` — update fields on an existing entry.
- `delete_knowledge` — hard-delete an entry by id.
- `archive_knowledge` — soft-hide an entry (status → archived) with an audit reason.
- `unarchive_knowledge` — restore an archived entry.
- `feedback_knowledge` — record `helpful` / `misleading` / `outdated` on a used entry.

## Troubleshooting
- If `mnemo` is not found after install, open a new shell or restart the AI client so PATH changes apply.
- If the MCP server is missing, run `mnemo setup --dry-run`, then `mnemo setup --auto`.
- After any config change, restart the IDE / open a new agent session.
- Connection silently fails: check the client MCP logs, for example `~/.claude/logs/` for Claude Code.
- Do not try to fix end-user installs with `pip install`, `pipx`, `uvx`, `brew`, or `npm`. They are not supported install paths.
