# Mnemo 架构

## 分层

```
Frontend (React)  →  Interface (WS Gateway)  →  Backend (Python) / System (Rust)
  纯渲染               消息路由+协议转换           领域逻辑        CLI调度+Agent安装
```

| 层 | 位置 | 职责 |
|---|---|---|
| **Frontend** | `desktop/src/` | 纯 UI 渲染，发 WS 消息 / 收 WS 事件 |
| **Interface** | `desktop/src-tauri/src/interface/` | WS Gateway，JSON-RPC 路由，协议转换 |
| **Backend** | `src/mnemo/` | 知识 CRUD、搜索、Guide、数据库、MCP |
| **System** | `desktop/src-tauri/src/system/` | CLI 调度、Agent 安装/检测/Link、版本同步 |
| **CLI** | `src/mnemo/cli/` | 独立 standalone binary，不接触前端 |

---

## 架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                     User Terminal                                     │
│                     $ mnemo search "xxx"                              │
│                     $ mnemo create "..."                              │
│                     $ mnemo setup --auto                              │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ 直接使用（独立的 standalone binary）
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      mnemo CLI (standalone binary)                     │
│   cli/main.py — Typer commands: create, search, get, update, delete,  │
│   archive, tags, serve, setup, mcp, open                              │
│                                                                       │
│   直接调用 KnowledgeService → SQLite                                   │
│   完全不经过前端 / Interface / Rust                                     │
└──────────────────────────────────────────────────────────────────────┘

                               ║
                               ║  (独立边界，CLI 不触碰 GUI 系统)
                               ║

┌──────────────────────────────────────────────────────────────────────┐
│                      Desktop Application                              │
│                                                                       │
│  ┌─────────────────────────────────┐                                  │
│  │  Frontend (React)               │                                  │
│  │  desktop/src/                   │                                  │
│  │                                 │                                  │
│  │  • Dashboard 页面（agent 状态）   │                                  │
│  │  • Guide 页面（Q&A 对话）         │                                  │
│  │  • 未来：知识管理页面、Viz 页面    │                                  │
│  │                                 │                                  │
│  │  只做：                          │                                  │
│  │  - 发送 WS 消息请求数据/操作      │                                  │
│  │  - 接收 WS 消息更新 UI 状态       │                                  │
│  │  - 管理本地 UI 状态               │                                  │
│  │                                 │                                  │
│  │  不做：                          │                                  │
│  │  - 直接调 Tauri invoke           │                                  │
│  │  - 直接 fetch HTTP API           │                                  │
│  │  - 任何业务逻辑判断               │                                  │
│  │  - 直接读写文件系统               │                                  │
│  └──────────────┬──────────────────┘                                  │
│                 │                                                     │
│                 │  ws://127.0.0.1:8788/ws                             │
│                 │  JSON-RPC 双向双工                                   │
│                 │  ┌─────────────────────────┐                        │
│                 │  │ 请求: {id, method, params}│                       │
│                 │  │ 响应: {id, result/error} │                       │
│                 │  │ 推送: {event, data}      │                       │
│                 │  └─────────────────────────┘                        │
│                 ▼                                                     │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  Interface Layer (WS Gateway) — Rust, 端口 8788               │    │
│  │  位置：desktop/src-tauri/src/interface/                        │    │
│  │                                                                │    │
│  │  职责：                                                         │    │
│  │  1. 接受前端 WS 连接，维护连接状态                               │    │
│  │  2. 解析 JSON-RPC 请求，路由到正确的处理器                       │    │
│  │  3. 将后端/系统结果序列化返回给前端                              │    │
│  │  4. 主动推送事件给前端（server status, agent change, etc.）      │    │
│  │  5. 不包含业务逻辑，纯消息路由 + 协议转换                         │    │
│  │                                                                │    │
│  │  路由表：                                                       │    │
│  │  ┌──────────────────────┬──────────────────────────────────┐   │
│  │  │ method 前缀           │ 转发到                            │   │
│  │  ├──────────────────────┼──────────────────────────────────┤   │
│  │  │ knowledge.*           │ → Backend (Python)               │   │
│  │  │ guide.*               │ → Backend (Python)               │   │
│  │  │ search.*              │ → Backend (Python)               │   │
│  │  │ stats.*               │ → Backend (Python)               │   │
│  │  │ agent.*               │ → System (Rust)                  │   │
│  │  │ system.*              │ → System (Rust)                  │   │
│  │  │ cli.*                 │ → System (Rust)                  │   │
│  │  └──────────────────────┴──────────────────────────────────┘   │
│  └──────┬──────────────────────────────────────────────┬─────────┘    │
│         │                                              │              │
│         │ backend ops（90%+ 请求）                      │ system ops   │
│         │ 内部 HTTP localhost:8787                      │ 进程内调用    │
│         ▼                                              ▼              │
│  ┌──────────────────────────────────┐  ┌──────────────────────────┐   │
│  │  Backend (Python)                │  │  System Layer (Rust)     │   │
│  │  src/mnemo/                      │  │  desktop/src-tauri/      │   │
│  │                                  │  │  src/system/             │   │
│  │  ┌────────────────────────────┐  │  │                          │   │
│  │  │ FastAPI Server (port 8787) │  │  │  职责：                   │   │
│  │  │ • /api/v1/*  REST API      │  │  │  • 启动/停止 Backend 进程  │   │
│  │  │ • /mcp  MCP transport      │  │  │  • CLI 进程调度/管理      │   │
│  │  └────────────────────────────┘  │  │  • 检测已安装的 AI agent   │   │
│  │                                  │  │  • Link/Unlink agent     │   │
│  │  ┌────────────────────────────┐  │  │  • CLI 版本检测与同步     │   │
│  │  │ KnowledgeService           │  │  │  • 窗口/托盘管理          │   │
│  │  │ • Hybrid search (FTS+vec)  │  │  │  • 文件系统操作           │   │
│  │  │ • CRUD + relation graph    │  │  │                          │   │
│  │  │ • Rerank + lifecycle       │  │  │  ┌────────────────────┐  │   │
│  │  └────────────────────────────┘  │  │  │ CLI Scheduler      │  │   │
│  │                                  │  │  │ • 按需 spawn mnemo  │  │   │
│  │  ┌────────────────────────────┐  │  │  │ • 管理 CLI 实例     │  │   │
│  │  │ Guide System               │  │  │  │ • stdin/stdout 通信  │  │   │
│  │  │ • IntentRouter             │  │  │  └────────────────────┘  │   │
│  │  │ • FAQ + KnowledgePack      │  │  │                          │   │
│  │  │ • FallbackHandler          │  │  │  ┌────────────────────┐  │   │
│  │  └────────────────────────────┘  │  │  │ CLI Version Sync   │  │   │
│  │                                  │  │  │ • bundled vs 系统   │  │   │
│  │  ┌────────────────────────────┐  │  │  │   CLI 版本比较      │  │   │
│  │  │ Repository Layer           │  │  │  │ • 自动覆盖旧版本    │  │   │
│  │  │ • SQLite (mnemo.db)        │  │  │  └────────────────────┘  │   │
│  │  │ • FTS5 + sqlite-vec        │  │  └──────────────────────────┘   │
│  │  └────────────────────────────┘  │                                 │
│  │                                  │                                 │
│  │  对外接口：                       │                                 │
│  │  • HTTP REST API (被 Interface    │                                 │
│  │    Layer 内部调用)                │                                 │
│  │  • MCP (被 AI agent 调用)         │                                 │
│  └──────────────────────────────────┘                                 │
└──────────────────────────────────────────────────────────────────────┘
```

---

## WS 消息协议

采用 JSON-RPC 2.0 over WebSocket：

```json
// 请求
{"jsonrpc": "2.0", "id": 1, "method": "agent.detect", "params": {}}

// 响应
{"jsonrpc": "2.0", "id": 1, "result": {"agents": [...]}}

// 错误
{"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "..."}}

// 推送事件（无 id）
{"jsonrpc": "2.0", "method": "event.server_status", "params": {"status": "running"}}
```

### 方法列表

| method | params | result | 路由 |
|--------|--------|--------|------|
| `agent.detect` | `{}` | `{agents: [...]}` | System |
| `agent.link` | `{name: "claude-code"}` | `{status: "ok"}` | System |
| `agent.unlink` | `{name: "claude-code"}` | `{status: "ok"}` | System |
| `agent.link_all` | `{}` | `{status: "ok"}` | System |
| `agent.unlink_all` | `{}` | `{status: "ok"}` | System |
| `system.ensure_server` | `{}` | `{status: "started"/"already_running"}` | System |
| `system.status` | `{}` | `{server, cli_version, bundled_version}` | System |
| `cli.sync` | `{}` | `{updated: bool, version: "..."}` | System |
| `guide.ask` | `{question: "..."}` | `{answer, intent, commands, source, cards_used}` | Backend |
| `knowledge.create` | `{title, content, ...}` | `{id: "..."}` | Backend |
| `knowledge.search` | `{query, limit, mode}` | `{items: [...], total}` | Backend |
| `knowledge.get` | `{id: "..."}` | `{item: {...}}` | Backend |
| `knowledge.update` | `{id, ...}` | `{item: {...}}` | Backend |
| `knowledge.delete` | `{id: "..."}` | `{status: "ok"}` | Backend |
| `knowledge.feedback` | `{id, signal}` | `{status: "ok"}` | Backend |
| `knowledge.related` | `{id: "..."}` | `{items: [...]}` | Backend |
| `stats.overview` | `{}` | `{total, active, archived, ...}` | Backend |

---

## 前端 WS Hook

```typescript
// desktop/src/hooks/useWS.ts
const { send, on, off, ready, connected } = useWS("ws://127.0.0.1:8788/ws");

// 请求-响应
const agents = await send("agent.detect");
const answer = await send("guide.ask", { question: "如何安装？" });

// 事件监听
on("event.server_status", (data) => setServerStatus(data.status));
```

---

## 启动顺序

```
1. Rust Shell 启动（Tauri setup）
2. Rust 启动 WS Gateway（端口 8788）
3. Rust spawn Python Backend（mnemo serve --port 8787）
4. Rust 轮询 Backend 健康检查（GET /health）
5. Backend ready → WS Gateway 推送 event.server_ready 给前端
6. Rust 执行 CLI 版本检测与同步
7. 前端连接 WS，请求初始数据
```

---

## 目录结构

```
desktop/src-tauri/src/
├── main.rs              # 入口
├── lib.rs               # Tauri 生命周期 + 命令注册（精简后）
├── interface/           # Interface Layer
│   ├── mod.rs
│   ├── server.rs        # WS server (tokio-tungstenite)
│   ├── router.rs        # JSON-RPC 方法路由
│   └── protocol.rs      # 消息序列化/反序列化
├── system/              # System Layer
│   ├── mod.rs
│   ├── agents.rs        # Agent 检测/安装/Link
│   ├── backend.rs       # Backend 进程管理
│   ├── cli.rs           # CLI 调度/版本同步
│   └── tray.rs          # 托盘/窗口管理
└── backend/             # Backend 代理
    └── mod.rs           # HTTP client → Python FastAPI

desktop/src/
├── hooks/
│   └── useWS.ts         # WS 连接管理 Hook
├── App.tsx              # 改为只通过 useWS 通信
└── guide/
    ├── GuidePage.tsx
    └── hooks/
        └── useGuide.ts  # 改为通过 useWS 发 guide.ask
```

---

## 当前 → 目标 对照

| 当前耦合 | 改造后 |
|----------|--------|
| `App.tsx` 调 `invoke("detect_agents")` | `send("agent.detect")` |
| `App.tsx` 调 `invoke("link_agent")` | `send("agent.link", {name})` |
| `useGuide.ts` 调 `fetch("/api/v1/guide/ask")` | `send("guide.ask", {question})` |
| `lib.rs` 注册 `detect_agents` 等 Tauri command | 移到 `system/agents.rs`，被 Interface 层调用 |
| `lib.rs` 嵌入 `guide_ask` Python 脚本 | 删除，Guide 逻辑已在 Python Backend |
| `lib.rs` 直接 spawn `mnemo serve` | 移到 `system/backend.rs` |
| 前端硬编码 `127.0.0.1:8787` | 改为 WS URL `ws://127.0.0.1:8788/ws` |
