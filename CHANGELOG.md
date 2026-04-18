# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] — 2026-04-30

### Added

- MCP server with 11 tools: search, create, get, update, delete, feedback, archive, unarchive, get_related, list_tags, search_by_tag
- Hybrid search engine: FTS5 full-text + sqlite-vec embeddings + knowledge graph, fused via Reciprocal Rank Fusion (RRF)
- Knowledge graph: auto-linking by vector similarity + keyword co-occurrence + typed edges
- Knowledge lifecycle: active → stale → superseded → archived with time-based freshness decay
- Write gate: near-duplicate detection before every create, suggests update instead
- Contradiction surfacing: conflicting entries returned together with `contradicts_with`
- Feedback loop: helpful / misleading / outdated signals adjust ranking scores
- Health check system: P1/P2 problem detection + search-time task dispatch
- HTTP server: FastAPI with MCP-over-SSE + MCP streamable-http transports
- REST API for timeline, events, and knowledge CRUD
- CLI: `mnemo serve`, `mnemo search`, `mnemo create`, `mnemo get`, `mnemo update`, `mnemo delete`, `mnemo tags`, `mnemo tag-search`, `mnemo related`
- Real-time visualization: 2D Canvas list/graph view + 3D WebGL force-directed graph
- Timeline API for knowledge growth replay
- i18n support: English, 简体中文, 繁體中文
- Feature flags for all lifecycle capabilities (write gate, freshness, state machine, feedback loop, contradiction pair, context-aware rank)
- Configuration via `MNEMO_*` environment variables
- Regression gate script for CI quality enforcement
