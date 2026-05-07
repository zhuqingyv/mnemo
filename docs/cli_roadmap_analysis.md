# mnemo CLI 现状分析与下一步行动报告

> 目标：将 mnemo 打包为多平台二进制，使 Windows/macOS 用户一行命令完成安装，并通过完善的 CLI 支持可视化界面、后台多实例 Server、以及一键 MCP 配置（含多 Agent 适配）。

---

## 一、项目现状速览

| 模块 | 位置 | 状态 |
|------|------|------|
| CLI 入口 | `mnemo` (`src/mnemo/cli/main.py`) | 成熟，基于 Typer，含 CRUD / search / monitor / serve / setup / mcp 等命令 |
| MCP Server 入口 | `mnemo-mcp` (`src/mnemo/mcp/server.py`) | 成熟，FastMCP 实现，11 个 tools，支持 stdio |
| HTTP Server | `mnemo serve` (`src/mnemo/server/app.py`) | 成熟，FastAPI + uvicorn，挂载 REST + MCP(SSE/HTTP) + `/viz` 静态文件 |
| 可视化界面 | `docs/demo/viz_v2/` | 已有 2D/3D WebGL 可视化，通过 `mnemo serve` 访问 `/viz` |
| 自动配置 | `mnemo setup` (`src/mnemo/setup/`) | 已支持 Claude Code、Cursor、Codex-CLI 的检测与配置注入 |
| 二进制打包 | `mnemo.spec` (PyInstaller) + `install.sh` | 已有 macOS arm64 产物 (`dist/mnemo-darwin-arm64`)，install.sh 支持 curl 安装 |
| 数据存储 | SQLite (`~/.mnemo/mnemo.db`) | 单文件，多实例需通过 `MNEMO_DATA_DIR` 环境变量隔离 |

---

## 二、需求对照与差距分析

### 需求 1：CLI 支持打开可视化界面

**目标**：用户执行一行命令（如 `mnemo open`）即可在浏览器打开可视化界面，无需记忆 URL。

**现状**：
- ✅ 可视化界面本身已存在（`docs/demo/viz_v2/`，2D Canvas + 3D WebGL）。
- ✅ HTTP Server 已会在 `/viz` 挂载静态文件。
- ❌ **缺少直接打开浏览器的 CLI 命令**。当前只有 `mnemo serve`（前台启动 server），setup 提示里也只是文字提示 `open http://127.0.0.1:8787/viz/`。

**差距**：
1. 没有 `mnemo open` 命令（自动检测后台 server 端口并调用系统默认浏览器打开）。
2. 没有 `mnemo serve --daemon` / `mnemo start` 模式，导致用户必须先手动 `serve` 再开浏览器。

---

### 需求 2：CLI 支持后台启动 / 关闭，且支持多实例命名

**目标**：
- `mnemo` → 后台启动一个默认 mnemo server（全局单例）。
- `mnemo your_key` → 后台启动一个命名实例。
- `mnemo close` → 关闭全局实例；`mnemo close your_key` → 关闭命名实例。
- MCP 配置可链接不同实例（不指定就是全局单例）。

**现状**：
- ❌ **没有任何后台进程管理能力**。当前 `mnemo serve` 是前台阻塞进程（`uvicorn.run()`），退出即停服。
- ❌ **没有多实例管理**。虽然数据层可通过 `MNEMO_DATA_DIR` 隔离数据库，但 CLI 没有封装实例生命周期（端口分配、PID 记录、实例发现）。
- ❌ MCP 配置目前只写死 `http://127.0.0.1:8787/mcp/http/mcp`，无法指向不同实例端口。

**差距**：
1. 需要一套轻量级 Daemon 管理机制（PID 文件、端口分配、进程守护）。
2. 需要实例注册表（记录 `instance_key → port → pid → data_dir`）。
3. 需要 MCP 配置动态生成（根据实例端口生成对应的 HTTP URL）。
4. 需要 CLI 命令：`start` / `stop` / `status` / `list`。

---

### 需求 3：`mnemo mcp` 支持一键配置到 Codex / Claude 等，且适配多 Agent 与 "cc switch"

**目标**：
- `mnemo mcp` 作为配置子命令，能自动把 mnemo 注册为 MCP server 到各种 Agent。
- 调研其他家 MCP 如何处理多类型 Agent 安装。
- 适配 "cc switch"（Agent Profile / 上下文切换）。

**现状**：
- ✅ `mnemo setup` 已能检测 Claude Code (`~/.claude/settings.json`)、Cursor (`~/.cursor/mcp.json`)、Codex-CLI (`~/.codex/config.toml`) 并注入 MCP 配置。
- ⚠️ 但 `mnemo setup` 是**面向 HTTP 模式**的，它注入的是 HTTP URL（`http://127.0.0.1:8787/mcp/http/mcp`）。
- ❌ **没有 stdio 模式的一键配置**。对于二进制产物，最优体验应该是让 Agent 直接执行 `mnemo mcp`（stdio），无需用户先 `serve` 再配 HTTP。
- ❌ **没有多 Agent 统一配置入口**。`setup` 只是逐个 client 注入，没有类似 "profile" 的概念来管理不同组合。
- ❌ **没有 `mnemo mcp` 配置子命令**。当前 `mnemo mcp` 实际上是启动 MCP server（stdio），与配置无关。

---

## 三、业界调研：多 Agent MCP 配置方案

### 3.1 主流方案对比

| 工具 | 核心设计 | 多 Agent 支持 | 多实例/Profile | 与 mnemo 的借鉴点 |
|------|---------|--------------|---------------|------------------|
| **mcpm.sh** | 全局 Server 仓库 + Profile 标签 + Client 同步 | `mcpm client edit claude/cursor/...` 一键启用/禁用 | ✅ Profile 组织服务器集合，整体挂载到 Client | **最强参考**：把 mnemo 注册为全局 server，再用 `client edit` 同步到各 agent |
| **MCP Linker** | Tauri GUI，一键 setup | Claude, Cursor, Windsurf, VS Code, Cline, Neovim | ❌ 偏 GUI，无 profile 概念 | 验证了多 agent 配置路径的集合 |
| **mcp-get (已归档)** | Registry + install/uninstall | 无自动配置 | ❌ | 只做安装，不做 client 配置 |
| **Smithery** | Registry + 安装指南 | 提供各 client 的配置片段 | ❌ | 作为文档/注册表参考 |
| **官方 MCP Servers** | `npx` / `uvx` 直接运行，用户手写 JSON | 各 client 独立配置 | ❌ | 推荐 stdio 模式：`command` + `args` |

### 3.2 关键结论

1. **Stdio vs HTTP 的选择**：
   - 对于**二进制 CLI 工具**，stdio 是最佳默认。Agent 直接 fork `mnemo mcp`，随用随启，无需后台服务。
   - HTTP 适合**多 Agent 共享**、或者需要可视化/REST 的场景。
   - **推荐双模**：默认向 Agent 推荐 stdio（零后台），当用户执行 `mnemo serve` 或 `mnemo start` 后再提供 HTTP。

2. **多 Agent 配置的最佳实践**：
   - 不要为每个 Agent 维护一份独立配置，而是维护一份**全局 server 清单**（如 `~/.mnemo/mcp-registry.json`）。
   - 提供 `mnemo mcp install --client <name>` 命令，将全局清单中的 mnemo 条目**同步**到指定 Agent 的配置文件。
   - 各 Agent 的配置路径和格式不同，需要适配器：
     - Claude Code: `~/.claude.json` (JSON, `mcpServers`)
     - Cursor: `~/.cursor/mcp.json` (JSON, `mcpServers`)
     - Codex CLI: `~/.codex/config.toml` (TOML, `mcp_servers`)
     - Claude Desktop: `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)
     - Cline/Windsurf: VS Code 插件设置或独立 JSON

3. **"cc switch" 的理解与适配**：
   - "cc switch" 指的是 **Agent / Profile / Context 切换**（如 Claude Code ↔ Codex ↔ Cline，或同一 Agent 的不同项目配置）。
   - 当前痛点：用户切换 Agent 时，每个 Agent 的 MCP 配置是独立的，容易漏配或端口冲突。
   - **适配方案**：
     - mnemo 作为**本地常驻 MCP Hub**：后台运行一个主实例（默认端口），所有 Agent 都通过 HTTP 连接到它。
     - 或者 mnemo 提供**配置同步命令**：`mnemo mcp sync` 把 mnemo 的 MCP 配置一次性写入所有已安装的 Agent。
     - **推荐后者**（更轻量，不依赖后台）：`mnemo mcp install --all` 自动检测并写入所有支持的 Agent；`mnemo mcp uninstall --all` 清除。

---

## 四、修正后的推荐架构（重点）

### 4.1 核心洞察：HTTP 为主，stdio 为桥接代理

Stdio 模式有一个致命问题：**每个 Agent fork 一个独立的 `mnemo mcp` 进程 = 每个 Agent 一个独立的 SQLite 文件 = 数据完全隔离**。Claude Code 写的知识 Cursor 看不到，mnemo 失去了"shared knowledge base"的核心价值。

**正确的架构**：

```
Agent (Claude Code) ──stdio──► mnemo mcp ──HTTP──► mnemo HTTP Server (默认 8787)
                                    │
                                    └─ 若后台未运行 → 自动静默启动 default 实例
```

**`mnemo mcp` 不再是"启动一个独立数据库服务"，而是"连接到共享实例的轻量代理"**：

1. Agent fork `mnemo mcp`（stdio）
2. `mnemo mcp` 进程检查本地 default 实例是否在运行
3. 若未运行，自动在后台 `start default`（找空闲端口，静默启动 HTTP server）
4. stdio 进程作为代理，把 Agent 的 MCP 请求通过本地 HTTP 转发给实例
5. 这样所有 Agent 天然共享同一个知识库，同时 Agent 配置简单（stdio）

### 4.2 CLI 命令设计

```
# 后台实例管理
mnemo                    # 等价于 mnemo start（启动默认后台实例）
mnemo start [KEY]        # 启动后台实例（KEY 默认 "default"）
mnemo stop [KEY]         # 关闭指定实例（KEY 默认 "default"）
mnemo status             # 列出所有运行中的实例
mnemo open               # 打开浏览器访问默认实例的 /viz
mnemo open [KEY]         # 打开指定实例的 /viz

mnemo serve              # 保留：前台运行 HTTP Server（开发调试用）
mnemo monitor            # 保留：前台运行监控

# MCP 配置
mnemo mcp                # Stdio-to-HTTP 桥接代理（供 Agent 调用）
mnemo mcp install        # 将 mnemo 注册到 MCP clients
mnemo mcp install --client claude     # 仅注册到 Claude Code
mnemo mcp install --client cursor     # 仅注册到 Cursor
mnemo mcp install --client codex      # 仅注册到 Codex CLI
mnemo mcp install --all               # 注册到所有检测到的 clients
mnemo mcp uninstall      # 从 clients 中移除 mnemo
mnemo mcp list           # 显示当前已配置的 clients

mnemo setup              # 保留（向后兼容），内部委托给 mcp install --all + start + open
```

### 4.3 后台多实例实现方案

**轻量级 PID + Port 文件（推荐）**
- 在 `~/.mnemo/instances/` 下维护实例状态：
  - `default.json`: `{ "pid": 12345, "port": 8787, "data_dir": "~/.mnemo", "started_at": "..." }`
  - `project-a.json`: `{ "pid": 12346, "port": 8788, "data_dir": "~/.mnemo/instances/project-a", ... }`
- `mnemo start`：
  1. 查找可用端口（从 8787 递增）。
  2. `subprocess.Popen` 启动 `mnemo serve --port PORT --data-dir DIR`（无窗口模式）。
  3. 写入 PID/Port 文件。
- `mnemo stop`：读取 PID 文件，发送 SIGTERM（Windows 用 `taskkill` 或 `terminate()`）。
- **优点**：极简，不引入 systemd/launchd 复杂度，跨平台一致。
- **缺点**：进程崩溃后残留 PID 文件，需要 `status` 命令做存活检测。

### 4.4 MCP 配置同步细节

当用户执行 `mnemo mcp install` 时，mnemo 应当向各 Agent 写入 **stdio** 配置：

```json
// Claude Code ~/.claude.json
{
  "mcpServers": {
    "mnemo": {
      "command": "mnemo",
      "args": ["mcp"]
    }
  }
}
```

如果用户指定了实例（如 `mnemo mcp install --instance project-a`），stdio 代理会带 `--instance` 参数：

```json
{
  "mcpServers": {
    "mnemo-project-a": {
      "command": "mnemo",
      "args": ["mcp", "--instance", "project-a"]
    }
  }
}
```

这样实现了：
- **默认场景**：Agent 直接 stdio 调用 `mnemo mcp`，零后台感知，零端口暴露，开箱即用。
- **多实例场景**：Agent 配置带 `--instance` 参数，`mnemo mcp` 桥接到对应的命名实例。
- **数据共享**：所有 Agent 背后连到同一个 HTTP 实例，知识一致性完美。
- **可视化**：`/viz` 直接展示所有 Agent 的活动。
- **监控**：monitor events 是全局聚合的。

---

## 五、下一步行动计划

### Phase 1：补齐 CLI 核心能力（优先）

1. **实现后台实例管理（`start` / `stop` / `status` / `list`）**
   - 实现 `InstanceManager` 类（PID 文件、端口分配、进程启停、存活检测）。
   - `start` 支持 `--port`、 `--data-dir`、 命名实例。
   - Windows 兼容（无 SIGTERM，用 `psutil` 或 `subprocess` 终止）。

2. **实现 Stdio-to-HTTP MCP Bridge**
   - 新增 `mcp_bridge` 模块，在 `mnemo mcp` 被 Agent fork 时：
     - 检测目标实例是否在运行
     - 若未运行，自动静默启动
     - 通过 HTTP 与实例通信，转发 stdio 上的 MCP 请求

3. **新增 `mnemo open` 命令**
   - 检测默认实例端口，调用 `webbrowser.open()` 打开 `/viz`。
   - 若后台未运行，提示先 `mnemo start`。

4. **重构 `mnemo mcp` 为配置子命令**
   - 保留 `mnemo mcp` 启动桥接代理的能力。
   - 新增 `mnemo mcp install / uninstall / list`。
   - 支持 stdio 配置模板（含 `--instance` 参数）。
   - 支持的 Clients：Claude Code、Cursor、Codex CLI、Claude Desktop（macOS）。

5. **改造 `mnemo setup`**
   - 将 `setup` 改为 `mcp install --all` + `start` + `open` 的组合快捷命令。
   - 保留向后兼容。

### Phase 2：二进制打包与安装脚本优化

1. **完善 PyInstaller spec**
   - 确保 `docs/demo/viz_v2` 静态文件被打包进二进制（PyInstaller `--add-data`）。
   - 确保 `sqlite_vec` 动态库在各平台正确收集（当前只写了 `.dylib`）。
   - 添加 Windows `.exe` 和 Linux 构建。

2. **优化 `install.sh`**
   - 支持安装后自动 `mnemo setup`（可选）。
   - 支持 Windows PowerShell 安装脚本 `install.ps1`。
   - 支持 Homebrew Formula（未来）。

3. **CI/CD 多平台构建**
   - GitHub Actions matrix: macOS (x86_64 + arm64), Windows, Linux。
   - 自动发布到 Release，install.sh 指向 latest release。

### Phase 3：高级特性（可选）

1. **Profile 级别的 MCP 配置**
   - 类似 mcpm 的 profile 概念，允许 `mnemo mcp install --profile work` 只挂载特定工具子集。
   
2. **系统级服务注册**
   - macOS launchd / Windows Service / Linux systemd，实现开机自启。

3. **远程 MCP 隧道**
   - `mnemo share` 通过 localtunnel 或类似方案暴露本地实例。

---

## 六、总结

| 需求 | 现状 | 优先级 | 关键动作 |
|------|------|--------|----------|
| 打开可视化界面 | 有界面，缺 CLI 命令 | P1 | 加 `mnemo open` + `webbrowser` |
| 后台多实例 Server | 完全缺失 | P1 | 实现轻量 Daemon 管理（PID/Port 文件） |
| MCP 一键配置 | 有 `setup`，但缺 stdio 配置与多 Agent 同步 | P1 | 重构 `mnemo mcp install`，stdio 桥接代理，适配 "cc switch" |
| 二进制多平台打包 | 已有 macOS arm64，缺 Win/Linux | P2 | 补全 PyInstaller 数据文件 + CI 矩阵 |

**建议立即开始 Phase 1 的开发**：先让 CLI 具备 `start`/`stop`/`open`/`mcp install` 能力，二进制打包可以并行进行，因为 PyInstaller 基本就绪，只需补静态资源和跨平台构建。
