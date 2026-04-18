# Contributing to mnemo

Thanks for your interest in contributing! mnemo is an agent-first local knowledge base built with Python + SQLite.

## Development Setup

```bash
git clone https://github.com/zhuqingyv/mnemo.git
cd mnemo
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Optional: install [Ollama](https://ollama.ai) with `qwen3-embedding:0.6b` for vector search during development.

## Code Style

- **Python**: formatted and linted with [ruff](https://docs.astral.sh/ruff/) (`ruff format` + `ruff check`)
- **JavaScript (viz)**: no build tools, Tailwind via CDN, vanilla ES modules
- Line length: 100 characters

Run the linter before committing:

```bash
ruff check src/ tests/
ruff format --check src/ tests/
```

## Testing

All tests use real SQLite — no mocks.

```bash
# Run all tests
pytest tests/

# Run with coverage
pytest tests/ --cov=mnemo

# Regression gate (required before merge)
MNEMO_HYBRID=1 python scripts/phase3_regression_gate.py
```

The regression gate compares against a frozen baseline. A drop of more than 0.5 percentage points blocks the merge.

## Pull Request Process

1. Fork the repo and create a branch from `main`
2. One feature or fix per PR
3. All tests must pass (`pytest tests/`)
4. Regression gate must exit 0
5. Use conventional commit messages: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`

## Architecture Overview

```
src/mnemo/
├── cli/          # Typer CLI (mnemo search, mnemo create, etc.)
├── mcp/          # MCP server (FastMCP, stdio + streamable-http)
├── server/       # FastAPI HTTP app (mounts MCP + REST + viz)
├── services/     # Core business logic (KnowledgeService, EmbeddingService)
├── repository/   # SQLAlchemy data access layer
├── ranking/      # Hybrid search fusion (FTS5 + vector + graph RRF)
├── health/       # Health check system (problem detection + task dispatch)
├── monitor/      # Runtime metrics collector
├── models/       # Pydantic models + SQLAlchemy ORM
├── config.py     # Environment-based configuration
└── db.py         # Database initialization + session factory
```

## Feature Flags

Every new capability must ship behind a feature flag (environment variable). When the flag is off, behavior must be identical to the previous version. This ensures safe incremental rollout.

## Questions?

Open an [issue](https://github.com/zhuqingyv/mnemo/issues) — we're happy to help.
