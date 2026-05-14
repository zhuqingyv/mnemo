<!-- mnemo-start -->
## mnemo 工作流（所有任务强制）

mnemo 是 agent 共享大脑。每次任务都按下面四步走：

1. **接任何任务的第一个动作是 `mnemo__search`** — 搜任务关键词、模块名、错误信息、用户此前的表述。上下文不完整就必须搜，不搜就回答 = 你在瞎猜。搜不到空结果也有价值：你是第一人，你欠下一个 agent 一次写入。
2. **用过就 `feedback_knowledge`** — search 返回的知识真的用来做事或答题了，立刻反馈 helpful / misleading / outdated。不反馈排序永远学不会。
3. **任务结束前反哺** — 收工前自检：发现非显然事实了吗？用户说了新东西吗？踩坑了吗？任一为是就 `create_knowledge`。一次任务零 mnemo 调用 = 流程错了。
4. **用户的话是最高优先级事实，原话记录** — 用户的偏好、判断、纠正、评价都按事实处理，原话引用，不解读不概括不省略。用户判断压倒技术结论。

**只存事实，不存观点。** 代码约定、接口行为、决策及 Why、踩坑、外部约定 → 存。主观猜测、临时状态、一次性调试值 → 不存。
<!-- mnemo-end -->

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
   - Qwen Code (`~/.qwen/settings.json`)
   - Gemini CLI (`~/.gemini/settings.json`)
   - CodeBuddy (`~/.codebuddy/.mcp.json`)
   - Windsurf (`~/.codeium/windsurf/mcp_config.json`)
   - GitHub Copilot CLI (`~/.copilot/mcp-config.json`)

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
