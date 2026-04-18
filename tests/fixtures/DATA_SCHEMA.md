# 测试数据格式规范

所有提取 Agent 必须严格按照本规范输出，不允许自定义字段或格式。

## 知识条目 (knowledge/{category}.json)

每个类别一个 JSON 文件，内容为数组：

```json
[
  {
    "title": "不 mock 测试",
    "tags": ["testing", "delivery-rule"],
    "summary": "用户明确禁止在测试中使用 mock，所有测试必须连接真实数据库",
    "content": "用户要求所有测试必须使用真实数据库（如 SQLite 内存库），不允许 mock。原因：之前项目中 mock 通过但生产环境 migration 失败的教训。",
    "scope": "global",
    "project_name": null,
    "claim_type": "decision",
    "source": "~/.claude/CLAUDE.md + Cursor bubble",
    "related": ["新模块必须带单测", "有证据才能说完成"],
    "search_queries": ["mock 测试", "单测规范", "测试要求"]
  }
]
```

### 字段规则

| 字段 | 类型 | 必填 | 规则 |
|------|------|------|------|
| title | string | 是 | 唯一、简短、可被搜索命中 |
| tags | string[] | 是 | 必须含类别标签（见下方对照表），可加自定义标签 |
| summary | string | 是 | 一句话摘要，不超过 100 字 |
| content | string | 是 | 完整内容，可含 `[[其他知识标题]]` 建立关联 |
| scope | string | 是 | `global` 或 `project` |
| project_name | string/null | 否 | scope=project 时必填，填项目目录名 |
| claim_type | string | 是 | 四选一：`fact` / `decision` / `procedure` / `hypothesis` |
| source | string | 是 | 从哪提取的（文件路径、会话 ID、Cursor bubble 等） |
| related | string[] | 否 | 关联知识的 title 列表 |
| search_queries | string[] | 是 | 至少 2 个搜索词，验证时用来测搜索命中率 |

### claim_type 说明

| 值 | 含义 | 过期规则 | 例子 |
|----|------|---------|------|
| fact | 客观事实 | 被证伪则 supersede | "basic-memory 是 AGPL 协议" |
| decision | 人做的决策 | 可被新决策替代 | "不 fork basic-memory，自研" |
| procedure | 操作流程 | 工具/环境变了则过期 | "AionUi lint 三件套命令" |
| hypothesis | 假设/未验证 | 不保证正确 | "trigram tokenizer 可能够用" |

### 类别标签对照表

| 类别 | 标签 | 文件名 |
|------|------|--------|
| 1. 用户偏好与沟通风格 | `user-preference` | user_preferences.json |
| 2. 代码红线与交付门禁 | `delivery-rule` | delivery_rules.json |
| 3. 项目架构决策与技术选型 | `architecture` | architecture_decisions.json |
| 4. 风险清单与避坑指南 | `risk` | pitfalls.json |
| 5. Agent 协作规则与团队约定 | `team-rule` | team_rules.json |
| 6. 技术调研与对比报告 | `tech-survey` | tech_surveys.json |
| 7. 项目状态快照与进度事实 | `status-snapshot` | status_snapshots.json |
| 8. 真实代码审阅结论 | `code-review` | code_reviews.json |
| 9. 工具能力与环境约束 | `env-constraint` | env_constraints.json |
| 10. 可复用测试 case | `e2e-scenario` | test_cases.json |
| 11. API/协议/数据字段规范 | `api-spec` | api_specs.json |
| 12. 命令与运维操作 | `command` | commands.json |

## 搜索场景 (scenarios/{category}_scenarios.json)

每个类别一个场景文件：

```json
[
  {
    "description": "新 Agent 想知道测试有什么规矩",
    "query": "mock 测试",
    "query_type": "search",
    "scope": null,
    "expected_hits": ["不 mock 测试"],
    "expected_not_hits": [],
    "notes": "用户多次强调这条规则，是最高优先级的交付红线"
  }
]
```

### 字段规则

| 字段 | 类型 | 必填 | 规则 |
|------|------|------|------|
| description | string | 是 | 用自然语言描述场景：新 Agent 想知道什么 |
| query | string | 是 | 实际执行的搜索词 |
| query_type | string | 是 | `search` / `tag-search` / `related` |
| scope | string/null | 否 | 限定搜索范围，null 表示不限 |
| expected_hits | string[] | 是 | 应该命中的知识 title 列表 |
| expected_not_hits | string[] | 否 | 不应该命中的 title 列表 |
| notes | string | 否 | 补充说明 |

## 质量红线

- 每条知识的 search_queries 至少 2 个
- 不做机械搬运，只存"会改变未来 Agent 行为"的信息
- 敏感信息脱敏：过滤 `sk-*`、`ghp_*`、`AKIA*`、`.env` 内容
- 同一知识不同来源只保留质量最高的版本
- content 中的 `[[wikilink]]` 目标必须是同批次中存在的 title

## 数据源位置

| 来源 | 路径 |
|------|------|
| Claude 全局记忆 | `~/.claude/memory/*.md` |
| Claude 项目记忆 | `/Users/zhuqingyu/project/*/.claude/memory/*.md` |
| Claude 会话 | `~/.claude/projects/*/` (JSONL) |
| Cursor 对话 | `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb` (SQLite, key=`cursorDiskKV.bubbleId:*`) |
| 项目文档 | `/Users/zhuqingyu/project/*/CLAUDE.md`, `AGENTS.md`, `docs/*.md` |
