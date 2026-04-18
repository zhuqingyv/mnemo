<p align="center">
  <img src="assets/logo-256.png" alt="mnemo" width="128">
</p>

<h1 align="center">mnemo</h1>
<p align="center">Agent-first 本地知识库 — 零基础设施，无限记忆。</p>

<p align="center">
  <a href="#特性">特性</a> •
  <a href="#快速开始">快速开始</a> •
  <a href="#使用">使用</a> •
  <a href="#可视化">可视化</a> •
  <a href="#贡献">贡献</a> •
  <a href="README.md">English</a>
</p>

<p align="center">
  <a href="https://www.python.org"><img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11+"></a>
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/protocol-MCP-green" alt="Protocol: MCP"></a>
  <img src="https://img.shields.io/badge/tests-passing-brightgreen" alt="tests: passing">
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT License">
</p>

---

## mnemo 是什么？

每次启动新的 AI agent 会话，agent 都会失忆 — 代码规范、决策记录、你花了几小时才定位的 bug，全部归零。`CLAUDE.md` 能装下第一页的稳定规则，但装不下任何会演变的东西。向量记忆服务（mem0、Zep）把 embedding 藏在黑盒里：你看不到存了什么，不知道为什么这条排第一，也没法在不重新向量化的前提下修正一条错误记录。

mnemo 是一个本地知识库，agent 自主写入、搜索、维护。一个 SQLite 文件，不上云，不烧 LLM token。知识自然老化，反馈可见地驱动排序，矛盾条目成对浮现而不是被静默覆盖。兼容任何 MCP 客户端 — Claude Code、Cursor，或你自己的工具。

## 特性

- **MCP 协议** — 开箱即用，兼容 Claude Code、Cursor 及所有 MCP 客户端
- **混合搜索** — FTS5 全文 + sqlite-vec 向量 + 知识图谱，RRF 融合排序
- **知识生命周期** — active → stale → superseded → archived；时间衰减下沉不活跃条目
- **自动建边** — 向量相似度 + 关键词边 + 反馈驱动的权重演化
- **写入门禁** — 每次写入前检测近似重复，建议更新而非重复创建
- **矛盾浮现** — 冲突条目通过 `contradicts_with` 成对出现，而非静默覆盖
- **健康检查** — P1/P2 问题检测 + 搜索时任务派发
- **实时可视化** — 2D Canvas + 3D WebGL 力导向知识图谱
- **时间轴 API** — 回放知识增长过程
- **多语言** — English、简体中文、繁體中文
- **零基础设施** — 单个 SQLite 文件，完全本地运行

## 快速开始

### 前置条件

- Python 3.11+
- （可选）[Ollama](https://ollama.ai) + `qwen3-embedding:0.6b` 模型（用于向量搜索）

### 安装

```bash
pip install m-nemo
```

### 启动

```bash
mnemo serve --port 8787
```

### 连接 Claude Code

在 `~/.claude/settings.json` 中添加：

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

重启 Claude Code 后即可使用。

## 使用

mnemo 提供 11 个 MCP 工具：

| 工具 | 用途 |
|------|------|
| `search` | 全文+语义+图谱混合搜索 |
| `create_knowledge` | 创建知识条目 |
| `update_knowledge` | 更新已有条目（旧版本标记为 superseded） |
| `delete_knowledge` | 删除不该存在的条目 |
| `feedback_knowledge` | 标记 helpful / misleading / outdated |
| `archive_knowledge` | 归档过时条目（从搜索中隐藏） |
| `unarchive_knowledge` | 恢复归档条目 |
| `get_knowledge` | 按 ID 或标题获取完整内容 |
| `get_related` | 探索知识关联 |
| `list_tags` | 浏览标签体系 |
| `search_by_tag` | 按标签过滤搜索 |

## 可视化

```bash
# 启动服务后在浏览器打开
open http://127.0.0.1:8787/viz/
```

支持三种视图：
- **List** — 仪表盘 + 知识卡片网格
- **2D** — Canvas 力导向图谱（Barnes-Hut O(n log n)）
- **3D** — WebGL 3D 图谱（three-forcegraph，CDN 懒加载）

## 架构

```
MCP client ──┐                         ┌── FTS5 (BM25)
             ├──▶ mnemo service ──┬──▶──┼── sqlite-vec (semantic)
CLI ─────────┘                    │     └── relation graph (typed edges)
                                  ▼
                          SQLite single file
```

## 配置

所有设置通过 `MNEMO_` 前缀的环境变量控制：

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `MNEMO_DATA_DIR` | `~/.mnemo` | 数据库存放路径 |
| `MNEMO_EMBEDDING_MODEL` | `qwen3-embedding:0.6b` | Ollama embedding 模型 |
| `MNEMO_OLLAMA_URL` | `http://localhost:11434` | Ollama 端点地址 |
| `MNEMO_DEFAULT_SCOPE` | `global` | 新条目的默认作用域 |

## 开发

```bash
git clone https://github.com/zhuqingyv/mnemo.git
cd mnemo
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/
```

## 贡献

参见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

MIT — 参见 [LICENSE](LICENSE)。
