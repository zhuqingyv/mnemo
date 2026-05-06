<p align="center">
  <img src="assets/logo-256.png" alt="mnemo" width="128">
</p>

<h1 align="center">mnemo</h1>
<p align="center"><strong>记忆不该永远塞在提示词里。</strong></p>
<p align="center">让 Agent 从检索记忆，进化到自己记得。</p>

<p align="center">
  <a href="#为什么需要-mnemo">为什么需要</a> •
  <a href="#mnemo-是什么">是什么</a> •
  <a href="#快速开始">快速开始</a> •
  <a href="#看见-agent-的大脑正在形成">可视化</a> •
  <a href="#未来终局不需要工具调用的记忆">未来终局</a> •
  <a href="README.md">English</a>
</p>

<p align="center">
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/protocol-MCP-green" alt="Protocol: MCP"></a>
  <img src="https://img.shields.io/badge/local--first-SQLite-blue" alt="Local-first SQLite">
  <img src="https://img.shields.io/badge/install-prebuilt_binary-purple" alt="Prebuilt binary">
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT License">
</p>

---

## 为什么需要 mnemo

每次新的 Agent 会话开始，它都像刚醒来。

它不记得你上次纠正过什么，不记得这个项目的规矩，不记得已经踩过的坑，也不记得哪条路已经浪费过一个下午。

大多数 memory 工具的答案是：把记忆存起来，下次再搜。

这有用，但不够。

因为真正的记忆不应该永远靠提示词提醒，也不应该每次都多一次工具调用。一个真正会进化的 Agent，应该把反复出现的项目规则、用户偏好、工具习惯、失败教训和成功路径，逐渐变成自己的长期行为。

mnemo 要做的就是这件事：

```text
不是让 Agent 永远调用记忆工具。
而是让 Agent 通过经历，持续进化出自己的记忆。
```

## mnemo 是什么

mnemo 是一个 agent-first 的本地记忆系统。

今天，它通过 MCP 给 Agent 一个本地海马体：

- **可搜索**：全文、语义、图谱混合搜索。
- **可反馈**：Agent 用过的知识可以标记 helpful / misleading / outdated。
- **可纠错**：过时、矛盾、重复、低质量记忆不会永远污染结果。
- **可观察**：知识、关系、反馈、生命周期和 Agent 活动都能在本地看见。
- **可安装**：预编译二进制发布，不需要 PyPI、不需要 npm、不需要从源码安装。

明天，它会把真正稳定、有价值、反复验证过的记忆蒸馏成训练样本，在本地空闲时训练 LoRA / Adapter。

也就是说：

```text
今天：MCP 让 Agent 查到记忆。
明天：LoRA 让模型自己记得。
终局：Agent 通过每一次经历持续进化。
```

## mnemo 的不同点

- **记忆不是笔记，是生命周期**
  - 记忆会被创建、使用、反馈、修正、替代、归档，而不是越堆越脏。

- **Agent 是一等用户**
  - mnemo 不只是给人看的知识库；它内置面向 Agent 的行为契约，让 Agent 知道什么时候 search、什么时候写入、什么时候反馈。

- **搜索也是维护入口**
  - search 不只是返回结果，还可以顺手派发小型维护任务，让知识库在真实工作中变干净。

- **纠错不是事后补丁**
  - `feedback`、`write gate`、`supersede`、`contradiction`、`archive` 都是正常流程。

- **记忆可以被训练**
  - mnemo 的终局不是把记忆永远塞进上下文，而是把高质量经验变成本地模型的长期行为偏置。

- **Agent 可以持续进化**
  - 每一次纠错、每一次成功任务、每一次工具调用，都可以成为下一轮行为升级的原料。

## 特性

- **MCP-native agent contract**：兼容 Claude Code、Claude Desktop、Cursor、Codex CLI 和所有 MCP 客户端。
- **混合搜索**：FTS5 全文搜索 + sqlite-vec 语义搜索 + typed knowledge graph，通过 RRF 融合排序。
- **搜索时维护任务**：search 结果可附带一个相关清理任务，比如归档过时知识或清理重复条目。
- **知识生命周期**：条目可在 `active`、`stale`、`superseded`、`archived` 之间流转。
- **反馈驱动排序**：Agent 使用知识后反馈 `helpful`、`misleading` 或 `outdated`，影响后续可信度。
- **写入门禁**：写入前检测近似重复、弱证据和潜在矛盾，引导 Agent 更新旧知识而不是制造噪声。
- **自动建边**：向量相似度、关键词、wikilink、手动关系和反馈权重共同形成知识图谱。
- **矛盾浮现**：冲突条目通过 `contradicts_with` 一起返回，而不是被 embedding 黑盒静默吞掉。
- **本地可视化**：List、2D Canvas、3D WebGL 视图展示记忆、关系和最近 Agent 活动。
- **时间轴 API**：回放知识增长和 Agent 活动过程。
- **零基础设施**：单个本地 SQLite 数据库，可选 Ollama embedding，不需要托管服务。

## 快速开始

mnemo 只通过 GitHub Releases 发布预编译二进制。

**终端用户不需要 Python，不需要 pip，不需要 npm，也不需要从源码构建。**

### macOS / Linux

```bash
curl -fsSL https://github.com/zhuqingyv/mnemo/releases/latest/download/install.sh | sh
```

### Windows PowerShell

```powershell
irm https://github.com/zhuqingyv/mnemo/releases/latest/download/install.ps1 | iex
```

安装器会：

1. 自动识别系统和 CPU 架构。
2. 下载匹配的预编译二进制。
3. 校验 release 中的 `SHA256SUMS`。
4. 安装到 `~/.mnemo/bin/mnemo` 或 `%LOCALAPPDATA%\mnemo\bin\mnemo.exe`。
5. 自动执行 `mnemo setup --auto`，为检测到的 AI 客户端写入 MCP 配置和 Agent 提示词。

支持的客户端：

- **Claude Code**
- **Claude Desktop**
- **Cursor**
- **Codex CLI**

安装完成后，重启你的 AI 客户端，然后验证：

```bash
mnemo --version
mnemo --help
```

### 交给本地 Agent 安装

如果你想让 Cursor / Claude Code / Codex 之类的编码 Agent 帮你安装 mnemo，只需要把这个仓库交给它，并要求它遵循 [AGENTS.md](AGENTS.md)。

`AGENTS.md` 里已经写死了正确安装方式，以及不要 `pip install`、不要 `pipx`、不要 `npm`、不要手改配置文件的规则。

### 重跑、预览和卸载

```bash
mnemo setup --auto       # 幂等执行，可重复运行
mnemo setup --dry-run    # 预览会改哪些配置
mnemo setup --uninstall  # 从所有检测到的客户端移除 mnemo 配置
```

### 多客户端共享和可视化

默认情况下，`mnemo setup` 写入的是 stdio MCP 配置：客户端直接启动 `mnemo mcp`，不需要后台服务。

如果你想让多个客户端共享同一个 mnemo，或打开实时可视化页面，可以切换到 HTTP 模式：

```bash
mnemo setup --mode http --port 8787
mnemo serve --port 8787
open http://127.0.0.1:8787/viz/
```

## 使用

mnemo 提供 11 个 MCP 工具：

| 工具 | 用途 |
|------|------|
| `search` | 全文 + 语义 + 图谱混合搜索 |
| `create_knowledge` | 创建结构化知识条目，并触发写入门禁检查 |
| `get_knowledge` | 按 ID 或标题获取完整内容 |
| `update_knowledge` | 修订已有条目，旧版本可被标记为 superseded |
| `delete_knowledge` | 硬删除条目 |
| `feedback_knowledge` | 记录 helpful / misleading / outdated 反馈 |
| `archive_knowledge` | 归档过时条目，从搜索中软隐藏 |
| `unarchive_knowledge` | 恢复归档条目 |
| `get_related` | 沿知识图谱探索相关条目 |
| `list_tags` | 浏览标签体系 |
| `search_by_tag` | 按标签过滤搜索 |

CLI 也可以直接使用：

```bash
mnemo search "websocket heartbeat"
mnemo create --title "Deploy needs --chain flag" \
  --tags "deploy,gotcha" --summary "Without --chain, tx silently drops" \
  --body "Deploy script ignores pending tx unless --chain is passed." \
  --claim-type fact
mnemo get 42
mnemo tags
```

核心概念：

- **Claim types**：`fact` | `decision` | `procedure` | `hypothesis`
- **Scopes**：`global` | `project` | `session`
- **Agent workflow**：先 search，再工作，最后把非显然经验写回，并反馈真正用过的知识。
- **Search dispatch**：search 可能附带一个维护任务，让 Agent 顺手清理记忆。
- **Feedback loop**：用过的知识会收到反馈，后续排序会越来越贴近真实价值。

## 看见 Agent 的大脑正在形成

mnemo 不只是把记忆存在数据库里，它会让记忆先变得可见。

你可以看到 Agent 的经验如何聚成图谱，看到搜索如何融合全文、语义和关系，看到每条记忆为什么仍然值得信任。

```bash
mnemo serve --port 8787
open http://127.0.0.1:8787/viz/
```

### 记忆图谱：经验之间长出连接

2D 图谱展示 Agent 维护出的记忆网络：条目会通过类型化关系、反馈和最近活动聚拢，而不是变成一组平铺的笔记。

<p align="center">
  <img src="images/1-compressed.jpg" alt="mnemo 大规模 2D 记忆图谱和实时指标" width="900">
</p>

### 搜索界面：不是查笔记，是调动记忆

搜索界面同时融合全文、语义和图谱关系，并展示实时排序与维护任务。Agent 不只是拿到答案，还能在工作过程中顺手修正记忆库。

<p align="center">
  <img src="images/2-compressed.jpg" alt="mnemo 搜索界面、混合结果和维护任务" width="900">
</p>

### 详情面板：每条记忆都有可信档案

详情面板让每条记忆都可检查。status、scope、source、tags、反馈、生命周期事件和关联条目都贴近正文，Agent 和人都能判断这条记忆为什么仍然值得信任。

<p align="center">
  <img src="images/3-compressed.jpg" alt="mnemo 记忆详情面板、元数据和关联条目" width="900">
</p>

### 3D 图谱：看见记忆从平面长成空间

3D 视图把记忆网络变成可以旋转、探索、观察聚类的空间结构。它让你看到哪些经验已经形成稳定核心，哪些记忆还在边缘等待更多证据。

<p align="center">
  <img src="images/4-compressed.jpg" alt="mnemo 3D 记忆图谱和空间聚类" width="900">
</p>

mnemo 先让记忆可见，然后让记忆内化，最后让 Agent 进化。

一开始，你能看到图谱生长。然后，你能看到 Agent 使用它。最后，真正有价值的部分会被蒸馏进模型本身，变成下一次更聪明的反应。

## 未来终局：不需要工具调用的记忆

MCP 记忆只是第一层。

它给 Agent 一个可以搜索、更新、纠错的本地海马体。但检索不是记忆的最终形态。

mnemo 的目标是把反复出现的经历变成模型自身的行为。

当 Agent 一次又一次遇到同样的项目规则、用户偏好、工具模式或纠错时，mnemo 不应该永远把它作为 search 结果返回。

mnemo 应该蒸馏它、评估它、版本化它，然后训练进本地 LoRA / Adapter。

最终，你的 Agent 不应该调用工具才记得你怎么工作。

它应该本来就知道。

这就是 mnemo 想要的进化路径：

```text
从外部检索
  ↓
到本地记忆
  ↓
到反刍蒸馏
  ↓
到模型内化
  ↓
到 Agent 行为进化
```

```text
外界经历
  ↓
短期记忆
  ↓
反刍蒸馏
  ↓
训练样本
  ↓
LoRA / Adapter
  ↓
长期行为偏置
```

更完整的路线是：

```text
Event Log
  ↓
Working Memory
  ↓
Memory Candidate Pool
  ↓
Distiller
  ↓
Training Dataset Registry
  ↓
Rumination Job
  ↓
Eval Gate
  ↓
LoRA Registry
  ↓
Runtime Loader
```

这条路线不是要用 LoRA 记住所有事实。

具体文档、日志、数字、历史记录仍然适合通过 RAG / MCP 查询；而 LoRA 更适合内化用户偏好、行为风格、稳定项目规则、Agent 工作习惯和反复纠错后的模式。

一句话：

```text
RAG 记事实，mnemo 训练习惯。
```

## 架构

```text
MCP client ──┐                         ┌── FTS5 (BM25)
             ├──▶ mnemo service ──┬──▶──┼── sqlite-vec (semantic)
CLI ─────────┘                    │     └── relation graph (typed edges)
                                  ▼
                          SQLite single file
```

当前版本的核心数据都在一个本地 SQLite 文件里。知识、关系、反馈、事件、生命周期和向量索引都可检查、可迁移、可备份。

长期路线会继续向反刍式记忆内化推进：

```text
mnemo = Agent 的海马体 + 睡眠反刍系统 + 记忆蒸馏器 + LoRA 训练工厂 + 记忆版本注册中心
```

## 配置

所有设置通过 `MNEMO_` 前缀的环境变量控制：

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `MNEMO_DATA_DIR` | `~/.mnemo` | 数据库存放路径 |
| `MNEMO_EMBEDDING_MODEL` | `qwen3-embedding:0.6b` | Ollama embedding 模型 |
| `MNEMO_OLLAMA_URL` | `http://localhost:11434` | Ollama 端点地址 |
| `MNEMO_DEFAULT_SCOPE` | `global` | 新条目的默认作用域 |

功能开关：

```text
MNEMO_WRITE_GATE_ENABLED
MNEMO_FRESHNESS_ENABLED
MNEMO_STATE_MACHINE_ENABLED
MNEMO_FEEDBACK_LOOP_ENABLED
MNEMO_CONTRADICTION_PAIR_ENABLED
MNEMO_CONTEXT_AWARE_RANK_ENABLED
```

除 context-aware rank 外，默认都为 `true`。

## 开发

终端用户不要从源码安装 mnemo。上面的 Quick Start 是唯一推荐安装路径。

下面内容只给贡献者使用：

```bash
git clone https://github.com/zhuqingyv/mnemo.git
cd mnemo
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/
```

回归门禁：

```bash
MNEMO_HYBRID=1 python scripts/phase3_regression_gate.py
```

构建本地二进制：

```bash
pip install pyinstaller
scripts/build.sh
```

## 贡献

参见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

MIT — 参见 [LICENSE](LICENSE)。
