# mnemo setup — 六大 Agent MCP 注入技术方案

> 调研日期：2026-05-10
> 验证方式：本机实测 CLI 输出 + 官方 GitHub 源码/文档确认

## 概述

`mnemo setup` 命令需要向 6 种 AI coding agent 注入 MCP server 配置。本文档记录每个 agent 的确切注入方式、配置路径、字段差异和注意事项。

**核心策略：优先调用 agent 自带 CLI 命令注入，fallback 到直接编辑配置文件。**

---

## 1. Claude Code

| 项目 | 值 |
|------|---|
| 版本 | 2.1.138 |
| 包名 | `@anthropic-ai/claude-code` (npm) |
| 配置格式 | JSON |
| 配置路径 (Mac/Linux) | `~/.claude.json` |
| 配置路径 (Windows) | `%USERPROFILE%\.claude.json` |
| MCP 字段 | `mcpServers` |
| 提示词路径 | `~/.claude/CLAUDE.md` |

### CLI 命令

```bash
# stdio（推荐）
claude mcp add -s user mnemo -- /path/to/mnemo mcp

# HTTP
claude mcp add -s user -t http mnemo http://127.0.0.1:8787/mcp/http/mcp

# 删除
claude mcp remove mnemo -s user

# 查看
claude mcp list
```

### 配置文件格式

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

stdio 模式：
```json
{
  "mcpServers": {
    "mnemo": {
      "command": "/path/to/mnemo",
      "args": ["mcp"]
    }
  }
}
```

### Scope 体系

| Scope | 存储位置 | 加载范围 |
|-------|---------|---------|
| `user` | `~/.claude.json` 顶层 | 所有项目 |
| `local` | `~/.claude.json` projects 下 | 当前项目（私有） |
| `project` | `<project>/.mcp.json` | 当前项目（可 git 共享） |

### 注意事项
- `type: "streamable-http"` 是 `"http"` 的别名
- 支持 `headersHelper` 字段（脚本动态生成 headers）
- 企业管控：`/Library/Application Support/ClaudeCode/managed-mcp.json` (Mac)

---

## 2. Qwen Code

| 项目 | 值 |
|------|---|
| 版本 | 0.15.10 |
| 包名 | `@qwen-code/qwen-code` (npm) |
| 配置格式 | JSON |
| 配置路径 (Mac/Linux) | `~/.qwen/settings.json` |
| 配置路径 (Windows) | `%USERPROFILE%\.qwen\settings.json` |
| MCP 字段 | `mcpServers` |
| 提示词路径 | `~/.qwen/QWEN.md` |

### CLI 命令

```bash
# stdio
qwen mcp add mnemo /path/to/mnemo mcp

# HTTP（URL 自动检测为 http transport）
qwen mcp add mnemo http://127.0.0.1:8787/mcp/http/mcp

# 显式指定 transport
qwen mcp add -t http mnemo http://127.0.0.1:8787/mcp/http/mcp

# 指定 scope
qwen mcp add -s user mnemo http://127.0.0.1:8787/mcp/http/mcp

# 删除
qwen mcp remove mnemo

# 查看
qwen mcp list
```

### 配置文件格式

HTTP 模式（**注意：用 `httpUrl` 字段，不是 `url`**）：
```json
{
  "mcpServers": {
    "mnemo": {
      "httpUrl": "http://127.0.0.1:8787/mcp/http/mcp"
    }
  }
}
```

stdio 模式：
```json
{
  "mcpServers": {
    "mnemo": {
      "command": "/path/to/mnemo",
      "args": ["mcp"]
    }
  }
}
```

SSE 模式（已废弃）：
```json
{
  "mcpServers": {
    "mnemo": {
      "url": "http://127.0.0.1:8787/sse"
    }
  }
}
```

### Transport 优先级（源码验证）

`httpUrl` > `url` > `command`

如果同时存在 `httpUrl` 和 `url`，走 Streamable HTTP。

### 注意事项
- 环境变量 `QWEN_HOME` 可覆盖 `~/.qwen` 目录
- 支持 `trust: true` 跳过工具确认
- 支持 `includeTools` / `excludeTools` 过滤

---

## 3. CodeBuddy

| 项目 | 值 |
|------|---|
| 版本 | 2.95.1 |
| 包名 | `@tencent-ai/codebuddy-code` (npm) |
| 架构 | **Claude Code fork**（CLI 接口完全一致） |
| 配置格式 | JSON |
| 配置路径 (Mac/Linux) | `~/.codebuddy/.mcp.json` |
| 配置路径 (Windows) | `%USERPROFILE%\.codebuddy\.mcp.json` |
| MCP 字段 | `mcpServers` |
| 可执行文件 | `codebuddy` 或 `cbc` |

### CLI 命令

```bash
# stdio
cbc mcp add mnemo -s user -- /path/to/mnemo mcp

# HTTP
cbc mcp add mnemo -s user -t http http://127.0.0.1:8787/mcp/http/mcp

# add-json（灵活方式）
cbc mcp add-json mnemo '{"type":"http","url":"http://127.0.0.1:8787/mcp/http/mcp"}' -s user

# 删除
cbc mcp remove mnemo -s user

# 查看
cbc mcp list
```

### 配置文件格式

与 Claude Code **完全一致**：
```json
{
  "mcpServers": {
    "mnemo": {
      "type": "http",
      "url": "http://127.0.0.1:8787/mcp/http/mcp"
    }
  },
  "disabledMcpServers": []
}
```

### 注意事项
- 是 Claude Code 换皮换模型版本，MCP 协议/配置/CLI 完全一致
- 配置目录从 `~/.claude` 变为 `~/.codebuddy`
- 支持 `--mcp-config` 运行时注入（不持久化）

---

## 4. Codex CLI

| 项目 | 值 |
|------|---|
| 版本 | 0.130.0 |
| 来源 | OpenAI (Rust 实现) |
| 配置格式 | **TOML**（唯一非 JSON） |
| 配置路径 (Mac/Linux) | `~/.codex/config.toml` |
| 配置路径 (Windows) | `%USERPROFILE%\.codex\config.toml` |
| MCP 字段 | `[mcp_servers.<name>]` |
| 提示词路径 | `~/.codex/AGENTS.md` |

### CLI 命令

```bash
# stdio
codex mcp add mnemo -- /path/to/mnemo mcp

# HTTP
codex mcp add mnemo --url http://127.0.0.1:8787/mcp/http/mcp

# 带 env
codex mcp add mnemo --env KEY=value -- /path/to/mnemo mcp

# HTTP + auth
codex mcp add mnemo --url http://127.0.0.1:8787/mcp/http/mcp --bearer-token-env-var MNEMO_TOKEN

# 删除
codex mcp remove mnemo

# 查看
codex mcp list
codex mcp get mnemo --json
```

### 配置文件格式

stdio 模式：
```toml
[mcp_servers.mnemo]
command = "/path/to/mnemo"
args = ["mcp"]
```

HTTP 模式：
```toml
[mcp_servers.mnemo]
url = "http://127.0.0.1:8787/mcp/http/mcp"
```

完整字段：
```toml
[mcp_servers.mnemo]
command = "/path/to/mnemo"
args = ["mcp"]
cwd = "/optional/path"
startup_timeout_sec = 10.0
tool_timeout_sec = 60.0
enabled = true
required = false
enabled_tools = ["search", "create_knowledge"]
disabled_tools = []

[mcp_servers.mnemo.env]
MNEMO_DATA_DIR = "~/.mnemo"
```

### 注意事项
- **不支持 SSE**，只有 stdio 和 streamable HTTP
- HTTP auth 用 `bearer_token_env_var`（指定环境变量名，非 token 本身）
- 项目级配置 `.codex/config.toml` 需要 trust 才加载
- AGENTS.md 支持 `.override.md` 后缀覆盖

---

## 5. Gemini CLI

| 项目 | 值 |
|------|---|
| 来源 | Google (`google-gemini/gemini-cli`) |
| 配置格式 | JSON |
| 配置路径 (Mac/Linux) | `~/.gemini/settings.json` |
| 配置路径 (Windows) | `%USERPROFILE%\.gemini\settings.json` |
| MCP 字段 | `mcpServers` |
| 提示词路径 | `~/.gemini/GEMINI.md` |

### CLI 命令

```bash
# stdio（推荐）
gemini mcp add -s user --trust mnemo -- /path/to/mnemo mcp

# HTTP
gemini mcp add -s user --transport http mnemo http://127.0.0.1:8787/mcp/http/mcp

# 删除
gemini mcp remove -s user mnemo

# 查看
gemini mcp list

# 启用/禁用
gemini mcp enable mnemo
gemini mcp disable mnemo
```

### 配置文件格式

stdio 模式：
```json
{
  "mcpServers": {
    "mnemo": {
      "command": "/path/to/mnemo",
      "args": ["mcp"],
      "trust": true
    }
  }
}
```

HTTP 模式（**注意：用 `httpUrl` 字段**）：
```json
{
  "mcpServers": {
    "mnemo": {
      "httpUrl": "http://127.0.0.1:8787/mcp/http/mcp"
    }
  }
}
```

### Schema 严格模式

Gemini 的 settings schema 声明了 `additionalProperties: false`，**不能加 schema 外字段**。允许的字段：

| 字段 | 用途 |
|------|------|
| `command` | stdio 可执行文件 |
| `args` | 命令参数 |
| `env` | 环境变量（支持 `$VAR` 展开） |
| `cwd` | 工作目录 |
| `url` | SSE transport URL |
| `httpUrl` | Streamable HTTP URL |
| `headers` | HTTP headers |
| `tcp` | WebSocket transport |
| `type` | 显式指定 transport |
| `timeout` | 超时 ms（默认 600000） |
| `trust` | 跳过确认 |
| `description` | 描述 |
| `includeTools` | 白名单 |
| `excludeTools` | 黑名单 |

### 注意事项
- `settings.json` 里还有 `hooks`、`mcp` 等其他顶层字段，**注入时只能 merge 不能覆盖整个文件**
- 启用状态单独存储在 `~/.gemini/mcp-server-enablement.json`
- server name 不要用下划线（策略引擎解析问题）
- 含 `*TOKEN*`/`*SECRET*`/`*KEY*` 的环境变量会被自动脱敏，必须在 `env` 中显式声明

---

## 6. Cursor

| 项目 | 值 |
|------|---|
| 类型 | IDE (非 CLI agent) |
| 配置格式 | JSON |
| 配置路径 (Mac/Linux) | `~/.cursor/mcp.json` |
| 配置路径 (Windows) | `%USERPROFILE%\.cursor\mcp.json` |
| 项目级路径 | `<project>/.cursor/mcp.json` |
| MCP 字段 | `mcpServers` |
| 提示词路径 | `.cursorrules`（项目级） |

### CLI 命令

```bash
# 添加到全局
cursor --add-mcp '{"name":"mnemo","type":"http","url":"http://127.0.0.1:8787/mcp/http/mcp"}'

# 添加到项目级
cursor --add-mcp '{"name":"mnemo","type":"http","url":"http://127.0.0.1:8787/mcp/http/mcp"}' --mcp-workspace

# stdio
cursor --add-mcp '{"name":"mnemo","command":"/path/to/mnemo","args":["mcp"]}'
```

**注意：** 没有 `cursor mcp add` 子命令，只有 `--add-mcp` flag，参数是完整 JSON。

### 配置文件格式

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

stdio 模式（**不需要 type 字段**，有 command 即识别）：
```json
{
  "mcpServers": {
    "mnemo": {
      "command": "/path/to/mnemo",
      "args": ["mcp"]
    }
  }
}
```

### 变量插值

所有字段支持：
- `${env:NAME}` — 环境变量
- `${userHome}` — 用户目录
- `${workspaceFolder}` — 项目根
- `${/}` — 路径分隔符

### 注意事项
- 支持 `envFile` 字段加载 `.env` 文件（仅 stdio）
- `--add-mcp` 的 JSON 中 `name` 字段会成为 `mcpServers` 下的 key
- Cursor 不是 CLI agent，用户可能没有 `cursor` 命令在 PATH 上

---

## 差异对比总结

### HTTP 字段差异（最关键的坑）

| Agent | HTTP 配置写法 |
|-------|-------------|
| Claude Code | `{"type": "http", "url": "..."}` |
| CodeBuddy | `{"type": "http", "url": "..."}` |
| Cursor | `{"type": "http", "url": "..."}` |
| Codex CLI | `url = "..."` (TOML, 无 type) |
| **Qwen Code** | `{"httpUrl": "..."}` |
| **Gemini CLI** | `{"httpUrl": "..."}` |

### 配置格式差异

| Agent | 格式 | 文件 |
|-------|------|------|
| Claude Code | JSON | `~/.claude.json` |
| Qwen Code | JSON | `~/.qwen/settings.json` |
| CodeBuddy | JSON | `~/.codebuddy/.mcp.json` |
| Codex CLI | **TOML** | `~/.codex/config.toml` |
| Gemini CLI | JSON | `~/.gemini/settings.json` |
| Cursor | JSON | `~/.cursor/mcp.json` |

### CLI 命令差异

| Agent | 添加命令格式 |
|-------|------------|
| Claude Code | `claude mcp add -s user [-t transport] <name> [--] <cmd/url> [args]` |
| Qwen Code | `qwen mcp add [-s scope] [-t transport] <name> <cmd/url> [args]` |
| CodeBuddy | `cbc mcp add <name> -s user [-t transport] [--] <cmd/url> [args]` |
| Codex CLI | `codex mcp add <name> [--url URL] [--] <cmd> [args]` |
| Gemini CLI | `gemini mcp add -s user [--transport t] [--trust] <name> [--] <cmd> [args]` |
| Cursor | `cursor --add-mcp '<json>'` |

---

## 实施方案

### 执行逻辑

```
mnemo setup [agent-type...]
  ├─ 检测已安装的 agent（目录/which 存在性）
  ├─ 对每个目标 agent:
  │   ├─ 尝试 CLI 命令注入
  │   │   ├─ 成功 → done
  │   │   └─ 失败 → fallback 写文件
  │   └─ 报告结果
  └─ 汇总输出
```

### 检测逻辑

| Agent | 检测条件 |
|-------|---------|
| claude-code | `which claude` 或 `~/.claude.json` 存在 |
| qwen-code | `which qwen` 或 `~/.qwen/` 存在 |
| codebuddy | `which cbc` 或 `~/.codebuddy/` 存在 |
| codex-cli | `which codex` 或 `~/.codex/` 存在 |
| gemini-cli | `which gemini` 或 `~/.gemini/` 存在 |
| cursor | `which cursor` 或 `~/.cursor/` 存在 |

### 幂等性保证

- CLI 命令方式：大多数 agent 的 `mcp add` 本身是幂等的（已存在则覆盖）
- 文件写入方式：检查 key 是否已存在且内容一致，一致则跳过

### 错误处理

1. agent CLI 不在 PATH → fallback 到直接写文件
2. 配置文件不存在 → 创建（mkdir -p + 写入）
3. 配置文件格式损坏 → 跳过并报错，不覆盖
4. 写入前备份 → `.bak` 文件
