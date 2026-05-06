# AGENTS.md

This repository ships **only as prebuilt binaries from GitHub Releases.**
There is no PyPI package, no `pipx` install, no `npm` package. Do not try to
build from source unless the user explicitly says so.

If a coding agent (Cursor / Claude Code / Codex / etc.) is asked to "install
mnemo" for the user, follow these steps **in order** and stop as soon as one
succeeds.

## Installing mnemo for the user

### macOS / Linux

```bash
curl -fsSL https://github.com/zhuqingyv/mnemo/releases/latest/download/install.sh | sh
```

### Windows (PowerShell)

```powershell
irm https://github.com/zhuqingyv/mnemo/releases/latest/download/install.ps1 | iex
```

The installer:

1. Detects OS + ARCH and downloads the matching binary.
2. Verifies SHA256 against the release-provided `SHA256SUMS`.
3. Places the binary at `~/.mnemo/bin/mnemo` (POSIX) or
   `%LOCALAPPDATA%\mnemo\bin\mnemo.exe` (Windows). No sudo / admin required.
4. Adds that directory to the user's shell PATH idempotently.
5. Runs `mnemo setup --auto`, which writes the MCP config (stdio mode) and
   the agent system prompt into every detected client:
   - Claude Code (`~/.claude.json`)
   - Claude Desktop (`~/Library/Application Support/Claude/...` /
     `%APPDATA%\Claude\...`)
   - Cursor (`~/.cursor/mcp.json`)
   - Codex CLI (`~/.codex/config.toml`)

After it finishes, tell the user to **restart their AI client** so the new
MCP server registration takes effect.

## Verifying the install

```bash
mnemo --version
mnemo --help
```

Either of these returning non-zero means the install failed. Do not try to
"fix" it by reinstalling Python or running `pip install` — open
[an issue](https://github.com/zhuqingyv/mnemo/issues) instead, or fall back
to the manual recovery procedure below.

## Manual recovery (only if the installer fails)

1. Download the right binary from the latest release page:
   <https://github.com/zhuqingyv/mnemo/releases/latest>
   - macOS Apple Silicon: `mnemo-darwin-arm64`
   - macOS Intel: `mnemo-darwin-x86_64`
   - Linux x86_64: `mnemo-linux-x86_64`
   - Windows x86_64: `mnemo-windows-x86_64.exe`
2. Optionally verify against `SHA256SUMS` from the same release.
3. Move the binary somewhere on PATH (`~/.mnemo/bin/mnemo` on POSIX,
   `%LOCALAPPDATA%\mnemo\bin\mnemo.exe` on Windows). On macOS run
   `xattr -d com.apple.quarantine <path>` if Gatekeeper blocks it.
4. Run `mnemo setup --auto` to register the MCP server with detected
   clients and inject the agent prompt.

## Things you must NOT do

- Do **not** run `pip install m-nemo` or `pip install -e .` to "install" mnemo
  for an end user. The repo is structured for binary distribution.
- Do **not** suggest `uvx`, `pipx`, `brew`, or `npm` install paths — none
  exist for this project.
- Do **not** edit the user's `~/.claude.json`, `~/.cursor/mcp.json`,
  `~/.codex/config.toml`, or shell rc files by hand. Always go through
  `mnemo setup` so changes are idempotent and reversible (`mnemo setup --uninstall`).

## Default behavior of `mnemo setup`

`mnemo setup --auto` (the default) writes a stdio MCP entry like:

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

Stdio means clients spawn `mnemo mcp` directly — no background server
required. If the user wants the HTTP transport (multi-client / `/viz`
visualization), run `mnemo setup --mode http --port 8787` and remind the
user to keep `mnemo serve --port 8787` running.

## Working with mnemo (after install)

When the user wants you to use mnemo for actual knowledge work, follow the
contract bundled in [`src/mnemo/setup/prompts/agents_md.md`](src/mnemo/setup/prompts/agents_md.md):

1. **`mnemo__search` first** — search before answering or starting any task.
2. **`feedback_knowledge`** — rate each entry you actually used.
3. **`create_knowledge` before finishing** — store new facts, decisions,
   user requirements/preferences.
4. **User words are facts** — record them verbatim as `claim_type: fact`.

Zero mnemo calls during a task means the workflow is broken.
