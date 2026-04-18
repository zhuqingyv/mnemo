# mnemo MCP Server

Local knowledge base for AI agents. Zero cloud, one SQLite file.

## Quick Install

### 1. Install from source
```bash
git clone https://github.com/zhuqingyv/mnemo.git
cd mnemo
pip install -e .
```

### 2. Add to your MCP client

Config file locations:
- Claude Code: `~/.claude.json`
- Cursor: `~/.cursor/mcp.json`
- Claude Desktop: `~/Library/Application Support/Claude/claude_desktop_config.json`

Using the installed entry point (simplest):
```json
{
  "mcpServers": {
    "mnemo": {
      "command": "mnemo-mcp"
    }
  }
}
```

Or run as a module against a specific checkout (recommended for development):
```json
{
  "mcpServers": {
    "mnemo": {
      "command": "/ABS/PATH/mnemo/.venv/bin/python",
      "args": ["-m", "mnemo.mcp.server"],
      "cwd": "/ABS/PATH/mnemo"
    }
  }
}
```

More templates: see `mcp/config.examples.json`.

## Verify Installation
```bash
mnemo-mcp --version
```
Full smoke test (binary + MCP handshake): see `mcp/smoke_test.md`.

After verifying, **restart your IDE / open a new session** — MCP config is not hot-reloaded.

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
- Python 3.11+ required.
- If `mnemo-mcp` not found on PATH: reinstall with `pip install -e .` from the repo root, or use the module form (`python -m mnemo.mcp.server` with `cwd` pointing at the checkout).
- After any config change: restart the IDE / open a new session.
- Connection silently fails: check `~/.claude/logs/` or run `claude mcp list`.
