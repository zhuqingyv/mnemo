<p align="center">
  <img src="assets/logo-256.png" alt="mnemo" width="128">
</p>

<h1 align="center">mnemo</h1>
<p align="center">Agent-first local memory for MCP agents.</p>

<p align="center">
  <a href="#features">Features</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#usage">Usage</a> •
  <a href="#visualization">Visualization</a> •
  <a href="#contributing">Contributing</a> •
  <a href="README.zh.md">中文</a>
</p>

<p align="center">
  <a href="https://www.python.org"><img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11+"></a>
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/protocol-MCP-green" alt="Protocol: MCP"></a>
</p>

---

## What is mnemo?

Every new agent session starts with a blank operational memory. It does not know the decisions from the last session, the local conventions, the broken approach that already cost you an afternoon, or the user's correction from yesterday.

mnemo is a local memory layer built for agents as first-class users. Agents search it before work, write back what they learn, rate the knowledge they used, and receive small maintenance tasks during search. The MCP instructions and tool contracts are part of the product: they tell agents how to keep the knowledge base useful instead of treating memory as a passive storage API.

It runs on one SQLite file, with no cloud service and no LLM token cost for storage. Knowledge can age, be corrected, be superseded, be archived, and surface contradictions instead of hiding them behind an opaque embedding result. It works with any MCP-compatible client, including Claude Code, Cursor, and custom tooling.

## Why mnemo is different

- **Agent-first by design**: the primary user is the agent, not a human clicking through a note app.
- **MCP behavior is explicit**: the server ships instructions and tool descriptions that define when agents should search, write, update, archive, and give feedback.
- **Search is also a maintenance surface**: search results can include a small optional task, such as archiving stale knowledge or cleaning up duplicates, so the knowledge base improves while agents do real work.
- **Memory stays inspectable**: entries, relations, feedback, events, and lifecycle state live in local SQLite tables instead of a remote black box.
- **Corrections are part of the loop**: feedback, write-gate checks, superseding, contradiction links, and archival are normal operations, not afterthoughts.

## Features

- **MCP-native agent contract**: works with Claude Code, Cursor, and any MCP client, with agent-facing instructions built into the server.
- **Hybrid search**: FTS5 full-text search + sqlite-vec semantic search + typed knowledge graph, fused via Reciprocal Rank Fusion.
- **Search-time maintenance tasks**: P1/P2 health checks can dispatch one relevant cleanup task at the end of a search result.
- **Knowledge lifecycle**: entries move through `active`, `stale`, `superseded`, and `archived`; unused knowledge decays instead of staying equally trusted forever.
- **Feedback-aware ranking**: agents record `helpful`, `misleading`, or `outdated` after using knowledge, and that signal feeds future ranking.
- **Write gate**: near-duplicate, weak-evidence, and potential contradiction checks run before writes so agents update existing knowledge instead of creating noise.
- **Auto-linking**: vector similarity, keyword edges, wikilinks, manual links, and feedback-driven edge weights build a local knowledge graph over time.
- **Contradiction surfacing**: conflicting entries are returned together with `contradicts_with` rather than silently choosing one answer.
- **Local visualization**: list, 2D Canvas, and 3D WebGL views show entries, relations, and recent agent activity.
- **Timeline API**: replay knowledge and agent activity over time.
- **Zero infrastructure**: one local SQLite database, optional Ollama embeddings, no hosted service required.

## Quick Start

mnemo ships as a single prebuilt binary — no Python, no pip, no npm.

### macOS / Linux

```bash
curl -fsSL https://github.com/zhuqingyv/mnemo/releases/latest/download/install.sh | sh
```

### Windows (PowerShell)

```powershell
irm https://github.com/zhuqingyv/mnemo/releases/latest/download/install.ps1 | iex
```

The installer drops the binary into `~/.mnemo/bin` (POSIX) or
`%LOCALAPPDATA%\mnemo\bin` (Windows), adds it to your PATH, and runs
`mnemo setup --auto` so every detected AI client gets the mnemo MCP server
and the agent system prompt configured automatically.

Supported clients: **Claude Code**, **Claude Desktop**, **Cursor**, **Codex CLI**.

After installation, restart your AI client. Verify:

```bash
mnemo --version
```

### Hand the repo to your local agent

If you want a coding agent to install mnemo for you, just clone this repo
and tell it to follow [AGENTS.md](AGENTS.md) — it has the one-liner and the
"do not pip install" rules baked in.

### Re-run / uninstall

```bash
mnemo setup --auto       # idempotent, safe to run any time
mnemo setup --dry-run    # preview what would change
mnemo setup --uninstall  # remove every mnemo entry from every client
```

### Optional: HTTP transport for multi-client / visualization

By default `mnemo setup` writes stdio MCP entries (clients spawn
`mnemo mcp` directly, zero background process). To share one mnemo across
multiple clients or to use the live visualization, switch to HTTP:

```bash
mnemo setup --mode http --port 8787
mnemo serve --port 8787
open http://127.0.0.1:8787/viz/
```

## Usage

### MCP Tools (11)

| Tool | Description |
|------|-------------|
| `search` | Hybrid full-text + semantic + graph search across all knowledge |
| `create_knowledge` | Write a new structured entry (with write-gate dedup check) |
| `get_knowledge` | Fetch full content by id or title |
| `update_knowledge` | Amend an existing entry (old version becomes superseded) |
| `delete_knowledge` | Hard-delete an entry by id |
| `feedback_knowledge` | Record `helpful` / `misleading` / `outdated` signal |
| `archive_knowledge` | Soft-hide from search without deleting |
| `unarchive_knowledge` | Restore an archived entry |
| `get_related` | Traverse the knowledge graph from an entry |
| `list_tags` | List all tags, optionally filtered by scope |
| `search_by_tag` | Find entries matching all given tags |

### CLI

```bash
mnemo search "websocket heartbeat"
mnemo create --title "Deploy needs --chain flag" \
  --tags "deploy,gotcha" --summary "Without --chain, tx silently drops" \
  --body "Deploy script ignores pending tx unless --chain is passed." \
  --claim-type fact
mnemo get 42
mnemo tags
```

### Core Concepts

- **Claim types** — `fact` | `decision` | `procedure` | `hypothesis`
- **Scopes** — `global` | `project` | `session`
- **Agent workflow** — search first, use or inspect results, do the work, write back non-obvious knowledge, then give feedback on entries that affected the result
- **Search dispatch** — search may append one optional maintenance task for the agent to handle when it matches the current context
- **Feedback loop** — agents call `feedback_knowledge` with `helpful`, `misleading`, or `outdated` after using a result

## Visualization

```bash
# Start server then open in browser
mnemo serve --port 8787
open http://127.0.0.1:8787/viz/
```

The visualization provides list, 2D graph, and 3D graph views of your knowledge base, showing entries, typed relations, feedback activity, and recent agent events.

The 2D graph makes the agent-maintained memory network visible: entries cluster through typed relations, feedback, and recent activity instead of appearing as a flat note list.

<p align="center">
  <img src="images/readme-graph.jpg" alt="mnemo 2D knowledge graph with live metrics" width="900">
</p>

The search interface provides hybrid full-text + semantic + graph search with real-time result ranking and maintenance task dispatch.

<p align="center">
  <img src="images/2-compressed.jpg" alt="mnemo search interface with hybrid results and maintenance tasks" width="900">
</p>

The detail panel keeps each memory inspectable. Status, scope, source, tags, feedback, lifecycle events, and related entries stay close to the content, so agents and humans can audit why a memory should still be trusted.

<p align="center">
  <img src="images/readme-detail.jpg" alt="mnemo knowledge detail panel with metadata and related entries" width="900">
</p>

## Architecture

```
MCP client ──┐                         ┌── FTS5 (BM25)
             ├──▶ mnemo service ──┬──▶──┼── sqlite-vec (semantic)
CLI ─────────┘                    │     └── relation graph (typed edges)
                                  ▼
                          SQLite single file
```

Six tables — `knowledge`, `relation`, `knowledge_meta`, `knowledge_event`, `knowledge_vec`, `knowledge_fts` — all in one `.db` file. No external services.

## Configuration

All settings are environment variables prefixed with `MNEMO_`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `MNEMO_DATA_DIR` | `~/.mnemo` | Database location |
| `MNEMO_EMBEDDING_MODEL` | `qwen3-embedding:0.6b` | Ollama embedding model |
| `MNEMO_OLLAMA_URL` | `http://localhost:11434` | Ollama endpoint |
| `MNEMO_DEFAULT_SCOPE` | `global` | Default scope for new entries |

**Feature flags** — every lifecycle capability can be toggled independently:

`MNEMO_WRITE_GATE_ENABLED`, `MNEMO_FRESHNESS_ENABLED`, `MNEMO_STATE_MACHINE_ENABLED`, `MNEMO_FEEDBACK_LOOP_ENABLED`, `MNEMO_CONTRADICTION_PAIR_ENABLED`, `MNEMO_CONTEXT_AWARE_RANK_ENABLED`

All default to `true` (except context-aware rank). Set any to `false` to disable without code changes.

## Development

End users do **not** install mnemo from source — see [Quick Start](#quick-start)
for the binary path. The instructions below are for contributors.

Prerequisites: Python 3.11+ and (optionally) [Ollama](https://ollama.ai)
with `qwen3-embedding:0.6b` for vector search.

```bash
git clone https://github.com/zhuqingyv/mnemo.git
cd mnemo
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/
```

Regression gate (must pass before merge):

```bash
MNEMO_HYBRID=1 python scripts/phase3_regression_gate.py
```

Build a local binary:

```bash
pip install pyinstaller
scripts/build.sh        # macOS / Linux
# Windows: python -m PyInstaller mnemo.spec --noconfirm --clean
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).

---

Built on the [Model Context Protocol](https://modelcontextprotocol.io) by Anthropic.
