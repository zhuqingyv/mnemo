<p align="center">
  <img src="assets/logo-256.png" alt="mnemo" width="128">
</p>

<h1 align="center">mnemo</h1>
<p align="center">面向 MCP agent 的 agent-first 本地记忆层。</p>

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
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT License">
</p>

---

## mnemo 是什么？

每次启动新的 agent 会话，它的操作记忆都是空的。上次做过的决策、本项目的约定、已经踩过的坑、用户昨天纠正过的话，都不会自然留在新会话里。

mnemo 是为 agent 作为一等公民设计的本地记忆层。agent 先搜索再工作，把新发现写回去，对真正用过的知识给反馈，并且可以在 search 结果里顺手接收小型维护任务。MCP instructions 和 tool contract 不是附属说明，而是产品的一部分：它们直接告诉 agent 什么时候 search、什么时候写入、什么时候更新、什么时候归档、什么时候反馈。

mnemo 只需要一个本地 SQLite 文件，不依赖云服务，也不为存储消耗 LLM token。知识可以老化、被修正、被新版本替代、被归档；矛盾会显式浮现，而不是被 embedding 黑盒静默吞掉。它兼容任何 MCP 客户端，包括 Claude Code、Cursor，或你自己的工具。

## mnemo 的不同点

- **agent-first 设计**：主要用户是 agent，不是人在笔记软件里点来点去。
- **MCP 行为契约显式化**：server 内置 instructions 和 tool descriptions，定义 agent 应该如何 search、write、update、archive、feedback。
- **search 也是维护入口**：搜索结果可以附带一个可选的小任务，比如归档过时知识、清理重复条目，让知识库在真实工作中顺手变好。
- **记忆可检查**：知识条目、关系、反馈、事件、生命周期状态都在本地 SQLite 表里，不是远端黑盒。
- **纠错是闭环的一部分**：feedback、write gate、supersede、contradiction、archive 都是常规操作，不是事后补救。

## 特性

- **MCP-native agent contract**：兼容 Claude Code、Cursor 及所有 MCP 客户端，并在 server 内置面向 agent 的行为说明。
- **混合搜索**：FTS5 全文搜索 + sqlite-vec 语义搜索 + typed knowledge graph，通过 RRF 融合排序。
- **搜索时维护任务**：P1/P2 健康检查可以在 search 结果末尾派发一个相关的清理任务。
- **知识生命周期**：条目在 `active`、`stale`、`superseded`、`archived` 之间流转；不用的知识会衰减，而不是永远同等可信。
- **反馈驱动排序**：agent 使用知识后记录 `helpful`、`misleading` 或 `outdated`，这些信号会影响后续排序。
- **写入门禁**：写入前检测近似重复、证据不足和潜在矛盾，引导 agent 更新旧知识，而不是制造噪声。
- **自动建边**：向量相似度、关键词边、wikilink、手动关系和反馈驱动权重，共同形成本地知识图谱。
- **矛盾浮现**：冲突条目通过 `contradicts_with` 一起返回，而不是静默选择一个答案。
- **本地可视化**：List、2D Canvas、3D WebGL 视图展示知识条目、关系和最近 agent 活动。
- **时间轴 API**：回放知识增长和 agent 活动过程。
- **零基础设施**：单个本地 SQLite 数据库，可选 Ollama embedding，不需要托管服务。

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

### 核心概念

- **Claim types** — `fact` | `decision` | `procedure` | `hypothesis`
- **Scopes** — `global` | `project` | `session`
- **Agent workflow** — 先 search，再使用或查看结果，完成任务后写回非显然知识，并反馈真正影响输出的条目
- **Search dispatch** — search 可能附带一个可选维护任务，agent 可在上下文匹配时顺手处理
- **Feedback loop** — agent 使用结果后调用 `feedback_knowledge`，标记 `helpful` / `misleading` / `outdated`

## 可视化

```bash
# 启动服务后在浏览器打开
open http://127.0.0.1:8787/viz/
```

支持三种视图：
- **List** — 仪表盘 + 知识卡片网格
- **2D** — Canvas 力导向图谱（Barnes-Hut O(n log n)）
- **3D** — WebGL 3D 图谱（three-forcegraph，CDN 懒加载）

2D 图谱展示 agent 维护出的记忆网络：知识条目会通过类型化关系、反馈和最近活动聚拢，而不是变成一组平铺的笔记。

<p align="center">
  <img src="images/readme-graph.jpg" alt="mnemo 2D 知识图谱和实时指标" width="900">
</p>

详情面板让每条记忆都可检查。status、scope、source、tags、反馈、生命周期事件和关联条目都贴近正文，agent 和人都能判断这条记忆为什么仍然值得信任。

<p align="center">
  <img src="images/readme-detail.jpg" alt="mnemo 知识详情面板和关联信息" width="900">
</p>

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
