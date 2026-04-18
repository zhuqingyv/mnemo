# mnemo MCP — Smoke Test

Three layers. L1 + L2 run in the **current** session. L3 requires a **new** IDE session after config changes.

Rule: if any layer fails, do not report "installed". Go to Troubleshooting in `mcp/README.md`.

Preconditions: you have installed mnemo from source (`git clone https://github.com/zhuqingyv/mnemo.git && cd mnemo && pip install -e .`), so that the `mnemo-mcp` entry point is on PATH.

---

## L1 · Binary runs

Proves the package is installed, entry point resolves, native deps load.

```bash
mnemo-mcp --version
```

Expected: prints a version string (e.g. `mnemo-mcp 0.x.y`) and exits 0.

Failure modes:
- `command not found: mnemo-mcp` → re-run `pip install -e .` from the repo root, or invoke the module form instead: `python -m mnemo.mcp.server --version`.
- `No module named mnemo` → packaging bug, file an issue.

---

## L2 · MCP initialize handshake

Proves the server speaks MCP over stdio (not just that the binary starts). This catches env / PATH / stdio-buffering problems that L1 misses.

### Option A — one-shot initialize via stdin

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
  | mnemo-mcp 2>/tmp/mnemo-mcp.stderr
```

Expected: stdout contains a single JSON-RPC response with `"result"` and `"serverInfo":{"name":"mnemo",...}`. The server may not exit cleanly on EOF — that is fine, only the response matters. Inspect `/tmp/mnemo-mcp.stderr` if nothing shows up.

### Option B — fastmcp client (if available)

```python
# python
import asyncio
from fastmcp import Client

async def main():
    async with Client(command="mnemo-mcp") as c:
        tools = await c.list_tools()
        assert len(tools) == 11, f"expected 11 tools, got {len(tools)}"
        print("OK:", sorted(t.name for t in tools))

asyncio.run(main())
```

Expected: prints exactly these 11 tool names:
`archive_knowledge, create_knowledge, delete_knowledge, feedback_knowledge, get_knowledge, get_related, list_tags, search, search_by_tag, unarchive_knowledge, update_knowledge`.

Failure modes:
- Hangs with no output → stdio transport broken; try `MNEMO_DEBUG=1` and check stderr.
- Handshake returns error → server started but `_bootstrap()` failed (DB init, missing env). Check stderr.

---

## L3 · Client mount (new session required)

Proves the end user's client (Claude Code / Cursor / Claude Desktop) picked up the config and mounted the server. MCP is **not** hot-reloaded.

Preconditions:
- Config added in L0.
- User has restarted the IDE / opened a new agent session.

Verification (in the new session):

```
claude mcp list            # should include `mnemo`
```

Then have the agent call any mnemo tool and paste the return verbatim. Minimum:

```
search("test")
```

Expected: a markdown-formatted result (hit list or "No results" message). Any valid MCP response proves the mount worked.

Failure modes:
- `mnemo` missing from `claude mcp list` → config written but client didn't reload. Restart again.
- Tool call returns connection error → check `~/.claude/logs/mcp-*.log` (Claude Code) or the equivalent Cursor/Desktop log.

---

## Pass criteria

All three layers must pass before reporting "installed":
- [ ] L1: `--version` exits 0.
- [ ] L2: MCP initialize returns valid JSON-RPC response (or fastmcp client lists 11 tools).
- [ ] L3: In a new session, the agent successfully calls a mnemo tool.
