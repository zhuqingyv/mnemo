# mnemo 测试数据集

## 为什么要做这个

用户本地有大量真实的聊天记录——和 Claude 的对话（3.8G JSONL）、Cursor 的对话（4197 条）、各项目的记忆文件和文档。这些是无数个 Agent 实例工作过的痕迹，里面沉淀着用户的偏好、项目的决策、踩过的坑、好用的技巧、业务的黑话。

问题是：这些知识散落在几百个文件和几千条对话里，每个新 Agent 进来都从零开始，之前的经验全部浪费。

**我们要从这些真实数据中提炼出一套"黄金测试数据集"。** 这套数据集有两个作用：

1. **作为种子数据灌入 mnemo**，让新 Agent 通过查询就能变成"老人"
2. **作为永久回归测试**，每次数据库升级、架构改动、搜索算法调整，跑一遍验证智能程度有没有退化

## 终极验证场景

一个全新的 Claude 实例被 spawn 到某个项目目录。它什么都不知道。但之前的 Agent 们把经验写进了 mnemo。

这个新 Agent 通过几次 mnemo 查询就应该知道：

- **用户是谁**：直接简洁不废话，有证据才能说完成，破坏性改动先确认
- **用户的雷区**：不 mock 测试，不要自作主张 push 代码，不要 AI 腔
- **项目的坑**：FTS5 中文分词有问题、basic-memory 是 AGPL 别碰、sqlite-vec pre-v1 锁版本
- **项目黑话**：mteam 是什么、Phase 1 包含什么、write-gate 是什么意思
- **好用的技巧**：用 TeamCreate 组建团队、派两路交叉验证避免信息偏差
- **架构决策**：纯数据库不落文件、闭源 proprietary、不 fork AGPL 项目、为什么这样选

如果新 Agent 搜不到这些，说明知识库不合格。如果升级后搜不到了，说明升级有退化。

## 数据提取计划

### 第一步：总编辑分类调查

一个 Agent 扫描所有数据源，采样分析，产出 `CATEGORY_REPORT.md`。

这份报告定义知识的分类框架（8-15 个类别），每个类别包含：
- 类别描述和价值（为什么新 Agent 需要这类知识）
- 从真实数据中采到的样本
- 数据量估算和来源
- 提取难度
- 搜索场景示例（新 Agent 会怎么搜这类知识）

### 第二步：用户确认分类后，并行提取

按类别派 10-15 个 Agent 同时工作，每个 Agent 负责一个类别：

1. **读取**：从分配的数据源中读取原始聊天记录
2. **推理**：判断这段对话里有什么值得保留的知识（不是所有对话都有价值）
3. **过滤**：去掉噪声（工具调用输出、系统消息、重复内容、临时调试信息）
4. **整理**：输出结构化知识条目（title/tags/summary/content/scope/claim_type）
5. **关联**：标注和其他知识的 `[[wikilink]]` 关系
6. **场景**：为每条知识写搜索测试场景（"搜 X 应该命中这条"）

### 第三步：沉淀为项目永久资产

所有产出落到这个目录，成为 mnemo 项目的一部分。

## 数据来源

| 来源 | 路径 | 格式 | 规模 |
|------|------|------|------|
| Claude 会话 | `~/.claude/projects/*/` | JSONL | 3.8G, 223+ 会话 |
| Cursor 对话 | `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb` | SQLite | 236M, 4197 条 bubble |
| Claude 记忆文件 | `~/.claude/memory/*.md`, 各项目 `.claude/memory/*.md` | Markdown | ~12 文件 |
| 项目文档 | 各项目 `CLAUDE.md`, `docs/*.md` | Markdown | ~300 文件 |

## 目录结构

```
tests/fixtures/
  README.md              ← 本文档（计划和规范）
  CATEGORY_REPORT.md     ← 分类调查报告（总编辑产出）
  knowledge/             ← 提取的知识条目（按类别分文件）
    user_preferences.json
    architecture_decisions.json
    pitfalls.json
    ...
  scenarios/             ← 搜索测试场景（按类别分文件）
    user_preferences_scenarios.json
    architecture_decisions_scenarios.json
    ...
```

## 数据格式

### 知识条目 (knowledge/*.json)

```json
[
  {
    "title": "知识标题",
    "tags": ["tag1", "tag2"],
    "summary": "一句话摘要",
    "content": "完整内容，可含 [[wikilink]] 建立关联",
    "scope": "global|project",
    "project_name": "项目名（scope=project 时）",
    "claim_type": "fact|decision|procedure|hypothesis",
    "source": "来源标识（哪段对话、哪个文件）",
    "related": ["关联知识标题"]
  }
]
```

### 搜索场景 (scenarios/*.json)

```json
[
  {
    "description": "场景描述：新 Agent 想知道什么",
    "query": "搜索关键词",
    "query_type": "search|tag-search|related",
    "expected_hits": ["应该命中的知识标题"],
    "expected_not_hits": ["不应该命中的知识标题"],
    "scope": "global|project|null",
    "notes": "为什么这个查询应该命中这些结果"
  }
]
```

## 使用方式

```bash
# 灌入测试数据
python tests/fixtures/load_fixtures.py

# 跑场景测试
pytest tests/test_scenarios.py -v
```

## 质量标准

- 每条知识必须经过 Agent 推理判断"值不值得存"，不做机械搬运
- 每条知识自带至少一个搜索场景（自测试的）
- 只存"会改变未来 Agent 行为"的信息
- 去重：同一知识不同来源只保留质量最高的版本
- 敏感信息脱敏（密码、API key、token 等不入库）
