<p align="center">
  <img src="assets/logo-256.png" alt="mnemo" width="128">
</p>

<h1 align="center">mnemo</h1>
<p align="center">Agent-first local knowledge base — zero infrastructure, infinite memory.</p>

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
  <img src="https://img.shields.io/badge/tests-passing-brightgreen" alt="tests: passing">
</p>

---

## What is mnemo?

Every time you start a new AI agent session, your agent forgets everything — the conventions, the decisions, the bugs you burned hours on. `CLAUDE.md` handles the first page of stable rules but can't hold anything that evolves. Vector memory services (mem0, Zep) store embeddings behind an opaque wall: you can't see what's stored, can't tell why a result ranked first, and can't correct a wrong entry without re-embedding everything.

mnemo is a local knowledge base that your agent writes to, searches, and maintains autonomously. One SQLite file, no cloud, no LLM token costs. Knowledge ages naturally, feedback moves rankings visibly, and contradictions surface together instead of being silently resolved. It works with any MCP-compatible client — Claude Code, Cursor, or your own tooling.

## Features

- **MCP protocol** — works with Claude Code, Cursor, and any MCP client out of the box
- **Hybrid search** — FTS5 full-text + sqlite-vec embeddings + knowledge graph, fused via Reciprocal Rank Fusion
- **Knowledge lifecycle** — active → stale → superseded → archived; time decay sinks unused entries
- **Auto-linking** — vector similarity + keyword edges + feedback-driven weight evolution
- **Write gate** — near-duplicate detection before every write; suggests updating instead of creating redundancies
- **Contradiction surfacing** — conflicting entries appear together with `contradicts_with` instead of silent resolution
- **Health check system** — P1/P2 problem detection + search-time task dispatch
- **Real-time visualization** — 2D Canvas + 3D WebGL force-directed knowledge graph
- **Timeline API** — replay knowledge growth over time
- **i18n** — English, 简体中文, 繁體中文
- **Zero infrastructure** — single SQLite file, runs entirely locally

## Quick Start

### Prerequisites

- Python 3.11+
- (Optional) [Ollama](https://ollama.ai) with `qwen3-embedding:0.6b` for vector search

### Install

```bash
pip install m-nemo
```

Or install from source:

```bash
git clone https://github.com/zhuqingyv/mnemo.git
cd mnemo
pip install -e .
```

### Run as HTTP server

```bash
mnemo serve --port 8787
```

### Connect to Claude Code

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "mnemo": {
      "command": "mnemo-mcp"
    }
  }
}
```

Or connect via HTTP (streamable-http transport):

```json
{
  "mcpServers": {
    "mnemo": {
      "type": "http",
      "url": "http://127.0.0.1:8787/mcp/http/mcp"
    }
  }
}
```

### Connect to Cursor

Add to `~/.cursor/mcp.json` using the same format as above.

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
- **Feedback loop** — agents call `feedback_knowledge` with `helpful`, `misleading`, or `outdated` after using a result

## Visualization

```bash
# Start server then open in browser
mnemo serve --port 8787
open http://127.0.0.1:8787/viz/
```

The visualization provides a real-time force-directed graph of your knowledge base, showing connections between entries and highlighting recent agent activity.

![viz](docs/screenshots/viz-list.png)

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

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).

---

Built on the [Model Context Protocol](https://modelcontextprotocol.io) by Anthropic.
