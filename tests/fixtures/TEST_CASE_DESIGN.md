# mnemo 智能性 / 准确性 / 相关性 测试 case 设计

本文档是自动化测试的顶层设计，先行于实现。实现阶段将严格按本文档产出 `tests/test_scenarios.py`。

---

## 背景与定位

- **测试对象**：`KnowledgeService`（业务层），不测 repository、CLI、MCP。理由：Agent 在真实使用中走这一层，贴近使用路径。
- **数据源**：`tests/fixtures/knowledge/*.json`（12 个类别，733 条知识）在 session fixture 中灌入临时 SQLite DB。
- **场景源**：`tests/fixtures/scenarios/*.json`（12 份，494 条场景）驱动准确性 + 相关性维度。
- **智能性维度**：手工新增 20 个独立 case，覆盖意图理解、同义/近义、跨类别、中文、歧义、否定等。
- **不 mock 原则**：全链路真实 SQLite + FTS5 + jieba。只依赖现有服务实现，不改源码。

---

## 1. 三个维度的定义

### 1.1 准确性（Accuracy）

**测什么**：给定一个真实的 Agent 查询场景，KnowledgeService 是否把"应该找到的知识"返回到了结果集合里。

**判定口径**：
- 对 `query_type=search`：对 `expected_hits` 中的每一条 title，检查是否出现在 `service.search(query, scope=...)` 的结果（默认 limit=20）中。
- 对 `query_type=tag-search`：检查 `expected_hits` 中的每一条 title 是否出现在 `service.search_by_tag([query], scope=...)` 结果中（limit=50 容纳大全集）。
- 对 `query_type=related`：先 `service.get_knowledge(query)` 拿到条目，再 `service.get_related(query, depth=2)`，检查 `expected_hits` 是否被访问到。
- 对 `expected_not_hits`：若非空，检查这些 title **未** 出现在结果中。出现一条即该维度该场景失败。

**通过条件（分层门禁，对齐 EVAL_CRITERIA.md）**：
- 单场景通过 = 全部 `expected_hits` 命中 **且** 全部 `expected_not_hits` 未命中。
- 整体通过率 = 通过场景数 / 总场景数。
- **目标线 ≥ 70%**：对齐 `EVAL_CRITERIA.md` 的"基本可用"及格线（产品验收标准）。
- **基线（硬门禁）≥ 60%**：不能低于此值，否则流水线 fail。反映现阶段 FTS5 + jieba 现实下限。
- **优秀线 ≥ 85%**：对齐 EVAL_CRITERIA 的"优秀"标准（新 Agent 几乎不需额外询问）。
- 通过率 < 50% 视为不合格，触发紧急回滚。

**失败不 assert 死**：采用 soft 失败——每个场景记录 `pass/fail + 原因 + 命中率`，最终打印汇总表。只有基线（60%）是硬断言，目标/优秀线在报告里呈现但不 fail。

---

### 1.2 相关性（Relevance）

**测什么**：返回的"正确结果"在排序里排得够不够靠前；搜不该命中的词时结果是否干净；查 A 不应返回 B 类别。

**判定口径（三个子测）**：
- **Top-N 覆盖**：对场景里有 `expected_hits` 的 search 查询，验证 `expected_hits` 至少有一条在结果前 3 条。当 `expected_hits` ≥ 2 时，要求至少一半出现在前 10 条。
- **反面场景（负相关）**：独立设计 10 条"反面查询"（见 §2.2），搜本库根本没有的语义（如 "外星人入侵"），结果必须为空或不含任何预设敏感 title。
- **scope 隔离**：对 scope 限定场景，验证跨 scope 的同名知识不会越界（例如 `scope="project"` 不应返回 global-only 知识）。

**通过条件**：
- Top-3 覆盖率目标 ≥ 60%（FTS5 bm25 排序在当前 tokenizer 下的现实目标）。
- 反面场景通过率目标 = 100%（0 误报）。
- scope 隔离通过率目标 ≥ 90%。

---

### 1.3 智能性（Intelligence）

**测什么**：知识库有没有"理解力"——同义词、近义词、口语化意图、模糊表达、跨类别联想、中文分词、关联图谱可达性。

**判定口径**：每条 case 是一个独立的"意图-期望"对，满足以下至少一条即通过：
- 意图关键词与知识 title/summary/content 无直接字面重叠，但仍命中预期条目。
- 口语化表达命中正式术语的条目。
- 中文纯短语正确分词后命中。
- 通过 `related` 深度 2 遍历能从起点到达期望知识。

**通过条件**：整体 ≥ 70%。单条不通过要打印 **实际返回的前 5 条**，便于后续优化。

---

## 2. 每个维度的 case 列表

### 2.1 准确性（由现有 scenarios 驱动，共 494 条，不逐条列出）

按类别汇总（每行一个场景文件，括号内是预期通过门槛）：

| Case ID | 类别 | 场景数 | 类型分布 | 通过门槛 |
|---------|------|-------|----------|----------|
| ACC-01 | user_preferences | 27 | search 25 / tag 1 / related 2 | ≥ 75% |
| ACC-02 | delivery_rules | 26 | search 22 / tag 3 / related 1 | ≥ 75% |
| ACC-03 | architecture_decisions | 46 | search 42 / tag 2 / related 2 | ≥ 70% |
| ACC-04 | pitfalls | 32 | search 24 / tag 7 / related 1 | ≥ 70% |
| ACC-05 | team_rules | 29 | search 26 / tag 2 / related 1 | ≥ 70% |
| ACC-06 | tech_surveys | 31 | search 28 / tag 2 / related 1 | ≥ 65% |
| ACC-07 | status_snapshots | 51 | search 46 / tag 3 / related 2 | ≥ 65% |
| ACC-08 | code_reviews | 59 | search 52 / tag 5 / related 2 | ≥ 65% |
| ACC-09 | env_constraints | 20 | search 18 / tag 2 / related 0 | ≥ 75% |
| ACC-10 | test_cases | 48 | search 42 / tag 4 / related 2 | ≥ 65% |
| ACC-11 | api_specs | 74 | search 68 / tag 4 / related 2 | ≥ 65% |
| ACC-12 | commands | 51 | search 44 / tag 5 / related 2 | ≥ 65% |

pytest 生成方式：用 `pytest.mark.parametrize` 展开，每个场景一个子 test。

---

### 2.2 相关性 case 列表

#### 2.2.1 Top-N 覆盖（REL-T）

程序化生成：遍历所有 494 个 search 场景中 `expected_hits` 非空的子集（约 440 条），每条生成两个断言：
- `expected_hits[0]` 是否在前 3 条
- `expected_hits` 整体覆盖率是否 ≥ 50% 在前 10 条

不逐条列出，以通过率作为报告口径。

#### 2.2.2 反面场景（REL-N）

手工设计的"应返回空 / 无关"查询，用来验证**不假阳性**。

| Case ID | Query | scope | 期望 | 理由 |
|---------|-------|-------|------|------|
| REL-N-01 | `外星人入侵` | null | 结果为空 | 知识库没有该主题，不应硬拗匹配 |
| REL-N-02 | `区块链 DeFi` | null | 结果为空或 ≤ 2 条 | 知识库无该领域 |
| REL-N-03 | `PHP Laravel` | null | 结果为空 | 技术栈不相关 |
| REL-N-04 | `nft-gmgn` | `project` + project_name=`AionUi` | 不含 nft-gmgn 项目知识 | 项目隔离 |
| REL-N-05 | `Chakra UI` | `project` + project_name=`AionUi` | 不含 `nft-gmgn 禁止 Chakra UI 新代码` | 跨项目隔离 |
| REL-N-06 | `kubernetes 集群` | null | 结果为空 | 运维领域未覆盖 |
| REL-N-07 | `游戏引擎` | null | 结果为空 | 无关领域 |
| REL-N-08 | `AionUi` | `project` + project_name=`nft-gmgn` | 不含 AionUi-only 规则 | 项目隔离方向 2 |
| REL-N-09 | `家常菜 菜谱` | null | 结果为空 | 非技术主题 |
| REL-N-10 | `stock price` | null | 结果为空 | 无该主题 |

判定：
- "结果为空或 ≤ N 条" — 返回长度符合要求。
- "不含 X" — X 不得出现在 title 里。

#### 2.2.3 scope 隔离（REL-S）

从已有 scenarios 里筛选所有带 scope + expected_not_hits 的场景（粗估 20+ 条），验证 not_hits 确实没被命中。以通过率作口径。

---

### 2.3 智能性 case 列表

20 条手工设计 case，每条都挑战"字面不相等"的意图。

| Case ID | Query | query_type | scope | 期望命中 (至少 1) | 意图 / 挑战点 |
|---------|-------|-----------|-------|-------------------|---------------|
| INT-01 | `用户脾气` | search | null | `直接简洁不要 AI 腔` / `说话带证据不要含糊` | 口语 "脾气" → 偏好 |
| INT-02 | `坑` | search | null | 任一 pitfalls 类别知识 | 单字 → 风险类 |
| INT-03 | `批量测试中文分词` | search | null | `FTS5 中文分词不完善` / `中文搜索先用英文关键词兜底` / `FTS5 用 jieba 做中文分词` | 纯中文短语跨词边界 |
| INT-04 | `不要废话` | search | null | `直接简洁不要 AI 腔` / `回答完不要再总结` | 口语否定式 |
| INT-05 | `怎么测试` | search | null | `不 mock 测试` / `新模块必须带单测` | 疑问式意图 |
| INT-06 | `哪些规则不能碰` | search | null | 至少 1 条 delivery-rule | 否定语义意图 |
| INT-07 | `改代码之前要做什么` | search | null | `修改代码先 bun build 再测试` / `破坏性改动先确认` | 流程类意图 |
| INT-08 | `Agent 新人入门` | search | null | `workspace 多项目注册制` / `evaluator 新 Agent` 相关 | 角色意图 |
| INT-09 | `做完怎么算交付` | search | null | `有证据才能说完成` / `交付前必过 delivery-gate` | 完成定义意图 |
| INT-10 | `中文搜不到怎么办` | search | null | `中文搜索先用英文关键词兜底` / `FTS5 中文分词不完善` | 自指 + 口语 |
| INT-11 | `跨类别词` `测试` | tag-search | null | 同时命中 delivery_rules 和 test_cases 两个类别 | tag 多类别分布 |
| INT-12 | `SQLite 为什么选它` | search | project | `SQLite 作为单文件零基础设施存储` | 疑问式 + 决策理由 |
| INT-13 | `Phase 1` | search | null | `Phase 路线图 4 阶段` / `Phase 1 并行 5 任务拆分` | 黑话 |
| INT-14 | `单测` | search | null | `新模块必须带单测` / `不 mock 测试` | 同义缩写："单测" → "单元测试 / mock / 测试" |
| INT-15 | `这个项目干啥的` | search | project | `mnemo 产品定位 Agent-first 知识库` | 口语化疑问句 → 定位类 |
| INT-16 | `数据裤` | search | null | `SQLite 作为单文件零基础设施存储` / `纯数据库架构不落 markdown 文件` 任一 | 错别字（"数据裤" → "数据库"）；当前 FTS5 不支持模糊匹配，预期失败（hypothesis） |
| INT-17 | `不 fork basic-memory 自研 mnemo` | related | null (depth=2 遍历) | `basic-memory 是 AGPL 协议不是 MIT` / `mnemo 用 Proprietary 闭源协议` | 图谱跨跳可达性 |
| INT-18 | `SQLite 作为单文件零基础设施存储` | related | null (depth=2) | `FTS5 用 jieba 做中文分词` / `纯数据库架构不落 markdown 文件` | 图谱多分支 |
| INT-19 | `tag 联合 user-preference + global` | tag-search | global | 至少 5 条 user-preference 类别知识 | 多 tag AND |
| INT-20 | `跑通再说完` | search | null | `有证据才能说完成` / `真实验证优先` / `跑起来看得见才信` 任一 | 口语化交付观 → 交付红线 |

**特殊说明**：
- INT-11 的实现细节：`service.search_by_tag(["testing"])` 或两次独立 tag 搜索取交集，验证至少 2 个不同的源文件（类别）贡献结果。
- INT-17 / INT-18 的实现：`service.get_related(title, depth=2)`，检查期望 title 出现在深度 2 的邻居集合中。
- **INT-16 定位 hypothesis**：当前 FTS5 + jieba 不做模糊匹配/编辑距离，"数据裤"预期搜不到"数据库"。此 case 先记在册，不计入智能性硬门禁。若未来引入 trigram/语义向量且命中，视为能力跃升。
- **字面送分题已剔除**：原 INT-14/15/16/20（mteam / write-gate / claim_type / AGPL）是 title 字面完全匹配，属于 FTS5 送分不能反映智能性，已换成同义缩写 / 口语 / 错别字 / 交付观四类真实意图挑战。这些"黑话查 title"已在准确性维度（见 §4.13 EVAL 对照）覆盖。

---

## 3. 测试工程结构

### 3.1 fixture（session 级）

- 放在 `tests/test_scenarios.py` 文件内，不污染全局 conftest（避免干扰现有 138 个测试）。
- 职责：
  1. 用 `tmp_path_factory.mktemp("mnemo-scenarios")` 建隔离目录。
  2. 构造独立 engine + session_factory（不碰 `mnemo.db` 的全局单例）。
  3. `Base.metadata.create_all` + 建 `knowledge_fts` 虚拟表。
  4. 注入一个独立的 `KnowledgeService` 实例。
  5. 加载所有 `fixtures/knowledge/*.json`，两遍：第一遍插入，第二遍重跑 wikilink/related 边（解决前向引用）。
  6. yield service。

### 3.2 测试产出

pytest 结构（三个维度三组 test，每组再按场景文件 parametrize）：

```
tests/test_scenarios.py::test_accuracy[user_preferences::<desc>]          PASSED
tests/test_scenarios.py::test_accuracy[delivery_rules::<desc>]            FAILED
...
tests/test_scenarios.py::test_relevance_top_n[<desc>]                     PASSED
tests/test_scenarios.py::test_relevance_negative[REL-N-01]                PASSED
tests/test_scenarios.py::test_relevance_scope_isolation[<desc>]           PASSED
tests/test_scenarios.py::test_intelligence[INT-01]                        PASSED
...
tests/test_scenarios.py::test_eval_criteria_e2e                           PASSED
tests/test_scenarios.py::test_overall_gate                                PASSED

===== Scenario report =====
Accuracy:    412 / 494 (83.4%)  by_category: user_pref 25/27, ...
Relevance:
  Top-N:     310 / 440 (70.5%)
  Negative:  10 / 10 (100%)
  Scope:     10 / 10 (100%)
Intelligence: 15 / 19 (78.9%)  (1 hypothesis case excluded from denom)
EVAL E2E:     14 / 17 (82.4%)  1 skipped (data gap)
===========================
```

### 3.3 硬门禁（分层）

全量测试最后用一个 `test_overall_gate` 断言，分为硬门禁（fail）和软门禁（仅告警）。

**硬门禁（低于即 fail）**：
- 准确性总通过率 ≥ **60%**（基线）
- 反面场景通过率 = **100%**（0 误报）
- 智能性通过率 ≥ **50%**（基线）
- EVAL_CRITERIA E2E 通过率 ≥ **70%**（产品验收）— 详见 §13

**软门禁（仅记录到报告，不 fail）**：
- 准确性目标 ≥ 70%（对齐 EVAL 基本可用）
- 准确性优秀 ≥ 85%（对齐 EVAL 优秀）
- 智能性目标 ≥ 70%
- scope 隔离 ≥ 90%
- Top-3 覆盖率 ≥ 60%

**不合格**（准确性 < 50%）触发紧急回滚。

硬门禁贴近现状、不误伤；软门禁呈现目标；EVAL 70% 是产品验收标准，必须守住。

---

## 4. 准确性代表性 case（每类别 5 条，共 60 条）

从 494 条 scenarios 中每个类别挑 5 条代表性 case 列出，覆盖 search / tag-search / related 三种 query_type，以及 scope 限定与 expected_not_hits。全量仍由 parametrize 对 494 条展开。

**统一判定标准（以下所有 4.x 表适用）**：
- 执行后 `expected_hits` **全部** 出现在结果集合中 → 命中
- `expected_not_hits`（若非空）**全部** 未出现在结果集合中 → 不假阳性
- 同时满足上两条 → 该场景 PASS；否则 FAIL
- search/tag-search 默认 limit=20/50，related 默认 depth=2

### 4.1 用户偏好（ACC-01，27 条中抽 5）

| Case ID | Query | Type | Scope | 期望命中 | 期望不命中 |
|---------|-------|------|-------|----------|-----------|
| ACC-01-01 | `沟通风格` | search | null | `直接简洁不要 AI 腔`、`回答完不要再总结`、`拒绝猜测式排查要直接给结论` | — |
| ACC-01-02 | `不要总结` | search | null | `回答完不要再总结` | — |
| ACC-01-03 | `要确切结论` | search | null | `说话带证据不要含糊`、`拒绝猜测式排查要直接给结论` | — |
| ACC-01-04 | `user-preference` | tag-search | null | `直接简洁不要 AI 腔`、`说话带证据不要含糊`、`不许主 Agent 扮演多角色`、`跑起来看得见才信` | — |
| ACC-01-05 | `直接简洁不要 AI 腔` | related | null | `回答完不要再总结`、`说话带证据不要含糊` | — |

### 4.2 交付红线（ACC-02，26 条中抽 5）

| Case ID | Query | Type | Scope | 期望命中 | 期望不命中 |
|---------|-------|------|-------|----------|-----------|
| ACC-02-01 | `mock 测试` | search | null | `不 mock 测试`、`单测不是通过而是发现问题` | — |
| ACC-02-02 | `交付 完成 证据` | search | null | `有证据才能说完成`、`交付前必过 delivery-gate`、`真实验证优先` | — |
| ACC-02-03 | `arco-design 原生 HTML` | search | project | `AionUi 禁止 raw HTML 交互元素` | `nft-gmgn 禁止 Chakra UI 新代码` |
| ACC-02-04 | `global` | tag-search | global | 12 条全局硬红线：`不 mock 测试`、`新模块必须带单测`、`有证据才能说完成`、`破坏性改动先确认`、`真实验证优先`、`单测不是通过而是发现问题`、`防止伪装多 Agent`、`交付前必过 delivery-gate`、`破坏性操作禁止绕过 hook`、`过了的就别重复跑`、`双 Agent 交叉验证避免单点结论`、`敏感信息必须脱敏` | — |
| ACC-02-05 | `不 mock 测试` | related | null | `新模块必须带单测`、`有证据才能说完成`、`E2E 必须用真实 Agent 补充不单独证明 UI` | — |

### 4.3 架构决策（ACC-03，46 条中抽 5）

| Case ID | Query | Type | Scope | 期望命中 | 期望不命中 |
|---------|-------|------|-------|----------|-----------|
| ACC-03-01 | `mnemo 定位` | search | project | `mnemo 产品定位 Agent-first 知识库` | — |
| ACC-03-02 | `存储格式` | search | project | `纯数据库架构不落 markdown 文件`、`SQLite 作为单文件零基础设施存储` | — |
| ACC-03-03 | `basic-memory 协议` | search | null | `basic-memory 是 AGPL 协议不是 MIT`、`不 fork basic-memory 自研 mnemo` | — |
| ACC-03-04 | `放弃方案` | tag-search | null | `放弃 fork engram 方案`、`放弃 zettelkasten-mcp 双层方案`、`放弃 mem0 Zep 向量记忆方案`、`放弃 Obsidian + MCP 插件方案`、`放弃 mcp-knowledge-graph 玩具方案`、`放弃 git 版本化用 supersede 替代`、`不引入 LLM 参与 write-gate` | — |
| ACC-03-05 | `不 fork basic-memory 自研 mnemo` | related | project | `basic-memory 是 AGPL 协议不是 MIT`、`mnemo 用 Proprietary 闭源协议` | — |

### 4.4 风险清单（ACC-04，32 条中抽 5）

| Case ID | Query | Type | Scope | 期望命中 | 期望不命中 |
|---------|-------|------|-------|----------|-----------|
| ACC-04-01 | `mnemo 风险` | search | project | `sqlite-vec pre-v1 不稳定`、`FTS5 中文分词不完善`、`Scope creep 加 UI 的冲动`、`Embedding 模型更新导致重建索引` | — |
| ACC-04-02 | `sqlite-vec 版本` | search | null | `sqlite-vec pre-v1 不稳定` | — |
| ACC-04-03 | `FTS5 中文` | search | null | `FTS5 中文分词不完善`、`中文搜索先用英文关键词兜底`、`trigram tokenizer 未必够用` | — |
| ACC-04-04 | `high-risk` | tag-search | null | 12 条高风险项（见 pitfalls scenario #10 全集） | — |
| ACC-04-05 | `FTS5 中文分词不完善` | related | null | `中文搜索先用英文关键词兜底`、`trigram tokenizer 未必够用` | — |

### 4.5 团队规则（ACC-05，29 条中抽 5）

| Case ID | Query | Type | Scope | 期望命中 | 期望不命中 |
|---------|-------|------|-------|----------|-----------|
| ACC-05-01 | `TeamCreate` | search | null | `多 Agent 协作必须使用 TeamCreate`、`团队成员必须带 team_name`、`禁止孤立 subAgent` | — |
| ACC-05-02 | `主 Agent 动手` | search | null | `主 Agent 纯监督调度`、`禁止主 Agent 角色扮演多角色` | — |
| ACC-05-03 | `mnemo 团队分工` | search | project | `mnemo-phase1-final 开发团队分工`、`kb-research 调研团队 4 成员`、`mnemo-data-extract 12 类别并行提取` | — |
| ACC-05-04 | `team-rule` | tag-search | null | `多 Agent 协作必须使用 TeamCreate`、`主 Agent 纯监督调度`、`禁止孤立 subAgent`、`禁止主 Agent 角色扮演多角色` | — |
| ACC-05-05 | `SendMessage 不是实时中断` | related | null | `mailbox 路径约定`、`成员 500ms 轮询 mailbox`、`TaskStop 是工具边界中断`、`pendingMessages 排队机制` | — |

### 4.6 技术调研（ACC-06，31 条中抽 5）

| Case ID | Query | Type | Scope | 期望命中 | 期望不命中 |
|---------|-------|------|-------|----------|-----------|
| ACC-06-01 | `为什么选 SQLite` | search | project | `mnemo 选择纯数据库而非向量黑盒`、`sqlite-vec 是 sqlite-vss 后继者`、`SQLite FTS5 可扩展到 10M 行` | — |
| ACC-06-02 | `mem0 对比` | search | null | `mem0 和 Zep 是向量黑盒不可审计`、`mnemo 相对 mem0 的差异` | — |
| ACC-06-03 | `Obsidian 限制` | search | null | `Obsidian 作为 AI 知识库的本质限制`、`mnemo 相对 Obsidian+mcp-obsidian 的差异` | — |
| ACC-06-04 | `竞品对标` | tag-search | null | `basic-memory 架构优势与协议陷阱`、`mcp-memory-service 偏向量存储`、`engram 是 Go 生态方案`、`iwe 无分层加载和知识图谱`、`Khoj 是最接近 headless Agent 知识库的开源方案` | — |
| ACC-06-05 | `mnemo 选择纯数据库而非向量黑盒` | related | project | `Obsidian 作为 AI 知识库的本质限制`、`mem0 和 Zep 是向量黑盒不可审计` | — |

### 4.7 状态快照（ACC-07，51 条中抽 5）

| Case ID | Query | Type | Scope | 期望命中 | 期望不命中 |
|---------|-------|------|-------|----------|-----------|
| ACC-07-01 | `mnemo 当前阶段` | search | project | `mnemo 项目阶段 - 截至 2026-04-18`、`mnemo Phase 1 路线图完成状态 - 截至 2026-04-18` | — |
| ACC-07-02 | `dog-food 验收` | search | project | `ai-store 郭总 dog-food 验收未完成 - 截至 2026-04-13`、`ai-store P6 四道验收未启动 - 截至 2026-04-13`、`ai-store 待完成任务 - 截至 2026-04-13` | — |
| ACC-07-03 | `mnemo 团队` | search | project | `mnemo 调研团队 kb-research 成果 - 2026-04-18`、`mnemo 团队（kb-research）成员 - 2026-04-18` | `ai-store 团队配置 - 截至 2026-04-13` |
| ACC-07-04 | `2026-04-18` | tag-search | null | `mnemo 项目阶段 - 截至 2026-04-18`、`mnemo 调研团队 kb-research 成果 - 2026-04-18`、`mnemo 登记时间 2026-04-18`、`项目注册表状态 - 截至 2026-04-18` | — |
| ACC-07-05 | `mnemo 项目阶段 - 截至 2026-04-18` | related | project | `mnemo 调研团队 kb-research 成果 - 2026-04-18`、`mnemo 不 fork basic-memory 自研决策` | — |

### 4.8 Code Review（ACC-08，59 条中抽 5）

| Case ID | Query | Type | Scope | 期望命中 | 期望不命中 |
|---------|-------|------|-------|----------|-----------|
| ACC-08-01 | `refine 模型 丢失` | search | project | `PersistentCoordinatorLoop refine 轮次丢失 per-task 模型映射`、`aion team 卖点：同团队混合多厂商模型` | — |
| ACC-08-02 | `qualityThreshold 不生效` | search | project | `team.qualityThreshold 配置未接线` | — |
| ACC-08-03 | `直连 API bash 工具` | search | project | `直连 API 的 Agent 接入 bash 工具` | — |
| ACC-08-04 | `signal card 次数 列表` | search | project | `signal card 标题次数来自 signal_times_by_type 求和`、`signal-v2 聪明钱头像读 item.data.smart_degen_wallets`、`toSignalListItems 展平 item 与 item.data` | — |
| ACC-08-05 | `signalHistoryHeaderCountPin` | search | project | `signalHistoryHeaderCountPin 模块级 Map 锁定策略` | — |

### 4.9 环境约束（ACC-09，20 条中抽 5）

| Case ID | Query | Type | Scope | 期望命中 | 期望不命中 |
|---------|-------|------|-------|----------|-----------|
| ACC-09-01 | `浏览器自动化` | search | null | `无全局浏览器自动化`、`Playwright 浏览器未安装`、`前端验证走 curl + screencapture` | — |
| ACC-09-02 | `headless Chrome` | search | null | `禁止启动 headless 抢占用户 Chrome`、`前端验证走 curl + screencapture` | — |
| ACC-09-03 | `前端验证` | search | null | `前端验证走 curl + screencapture`、`curl 可做 HTTP 测试`、`macOS screencapture 可用` | — |
| ACC-09-04 | `deferred 工具` | search | null | `ToolSearch 解锁 deferred 工具` | — |
| ACC-09-05 | `env-constraint` | tag-search | global | `Playwright 浏览器未安装`、`禁止启动 headless 抢占用户 Chrome`、`前端验证走 curl + screencapture`、`mcp__notify 通知通道可用`、`SendMessage 用于 Agent 团队通信`、`ToolSearch 解锁 deferred 工具`、`Agent 线程 bash 每次 cwd 会重置` | — |

### 4.10 测试 Case（ACC-10，48 条中抽 5）

| Case ID | Query | Type | Scope | 期望命中 | 期望不命中 |
|---------|-------|------|-------|----------|-----------|
| ACC-10-01 | `hub 安装链路` | search | project | `AionUi Hub L1 集成测试：完整安装链路` | — |
| ACC-10-02 | `playwright electron` | search | null | `AionUi Hub L2 E2E：UI 安装流程`、`ai-store 冷启动 5 秒内窗口出现` | — |
| ACC-10-03 | `Orchestrator` | search | project | `supercell Orchestrator 同名 agent 重复执行拒绝`、`supercell Orchestrator 并发部分失败不影响其他` | `AionUi Hub L1 集成测试：完整安装链路` |
| ACC-10-04 | `e2e-scenario` | tag-search | null | `AionUi Hub L1 集成测试：完整安装链路`、`mcp-team-hub check_in 正常签到`、`ai-store 冷启动 5 秒内窗口出现`、`supercell 流式聊天集成：分片拼接`、`cross-project 不 mock 测试红线` | — |
| ACC-10-05 | `mcp-team-hub 双进程并发清理同一锁（double-free）` | related | project | `mcp-team-hub nonce 二次校验` | — |

### 4.11 API 规范（ACC-11，74 条中抽 5）

| Case ID | Query | Type | Scope | 期望命中 | 期望不命中 |
|---------|-------|------|-------|----------|-----------|
| ACC-11-01 | `claim_type 枚举` | search | project | `mnemo Knowledge.claim_type 字段` | — |
| ACC-11-02 | `scope project_name 关系` | search | project | `mnemo Knowledge.scope 字段`、`mnemo Knowledge.project_name 字段`、`DATA_SCHEMA scope 字段规则` | — |
| ACC-11-03 | `title 字段长度` | search | project | `mnemo Knowledge.title 字段` | — |
| ACC-11-04 | `api-spec` | tag-search | null | `mnemo Knowledge.title 字段`、`team-hub 消息信封格式 envelope`、`vault use_api 输入参数` | — |
| ACC-11-05 | `team-hub Message 接口` | related | project | `team-hub 信封 type 枚举`、`team-hub Message.status 字段` | — |

### 4.12 命令（ACC-12，51 条中抽 5）

| Case ID | Query | Type | Scope | 期望命中 | 期望不命中 |
|---------|-------|------|-------|----------|-----------|
| ACC-12-01 | `mnemo 跑测试` | search | project | `mnemo 跑单元测试` | `AionUi 测试命令` |
| ACC-12-02 | `MNEMO_DATA_DIR 验证` | search | project | `mnemo CLI 本地验证套路` | — |
| ACC-12-03 | `AionUi PR 前检查` | search | project | `AionUi 提 PR 前跑 prek`、`AionUi lint 三件套`、`AionUi i18n 校验` | — |
| ACC-12-04 | `command` | tag-search | null | `mnemo 跑单元测试`、`AionUi lint 三件套`、`teams E2E 开发模式`、`ai-child 首次启动`、`nft-gmgn 类型检查`、`清端口占用` | — |
| ACC-12-05 | `AionUi 代理 PAC 自动回退` | related | project | `AionUi 无头服务器部署`、`/dev/tcp 探活端口`、`SSH 反向隧道转发本地代理` | — |

---

### 4.13 EVAL_CRITERIA 对照（ACC-E，18 条 EVAL 查询映射）

`EVAL_CRITERIA.md` 定义 5 个使用场景共 18 条查询，是产品验收标准（pass 线 70%）。这里把 18 条逐一映射到测试 case，标注已被前面 4.1-4.12 覆盖的 vs 需新增的；同时明确 fixture 缺口（fixture 暂无对应条目，必须先扩数据才能通过）。

| Case ID | EVAL 分组 | Query | Type | Scope | 期望命中 | 已在前面 ACC 覆盖 | 备注 |
|---------|----------|-------|------|-------|----------|-------------------|------|
| ACC-E-01 | 新 Agent(1/7) | `mnemo 定位` | search | project | `mnemo 产品定位 Agent-first 知识库` | ACC-03-01 | ✅ 命中 |
| ACC-E-02 | 新 Agent(2/7) | `用户偏好` | search | null | `user-preference` 类任一 3 条 | — | 新增：验证模糊词"偏好"能命中 user-preference 类别；**hypothesis**（title 都没"偏好"二字） |
| ACC-E-03 | 新 Agent(3/7) | `禁止` | search | null | `禁止主 Agent 角色扮演多角色` / `禁止孤立 subAgent` / `禁止启动 headless 抢占用户 Chrome` 任一 3 条 | — | 新增 |
| ACC-E-04 | 新 Agent(4/7) | `不要做` | search | null | `回答完不要再总结` / `有歧义先问不自己猜` / `破坏性改动先确认` 任一 | — | 新增：否定性意图挑战 |
| ACC-E-05 | 新 Agent(5/7) | `技术栈` | search | project | `mnemo 技术栈 Python 确认 - 截至 2026-04-18` | — | 新增 |
| ACC-E-06 | 新 Agent(6/7) | `坑` | search | null | tag 含 `risk` 的任一 3 条 | INT-02 | 智能性已覆盖，EVAL 也需统计 |
| ACC-E-07 | 新 Agent(7/7) | `问题` | search | null | 任一 `pitfalls` / `code_reviews` 类条目 3 条 | — | 新增：宽泛意图 |
| ACC-E-08 | 跨项目(1/3) | `mteam` | search | null | `mteam 未来集成方向` | — | 新增：跨项目黑话（原 INT-14） |
| ACC-E-09 | 跨项目(2/3) | `架构` | search | project | `mnemo 采用三层架构（repository/service/mcp）` / `repository-service-mcp 三层架构` 任一 | — | 新增 |
| ACC-E-10 | 跨项目(3/3) | `编码规范` | search | global | 至少 1 条 tag 含 `delivery-rule` 或 `convention` 的 global scope 条目 | — | **⚠️ 知识缺口**：fixture 中无 title 带"编码规范"条目；需要 fixture 扩充，否则 fail 是数据问题非搜索问题 |
| ACC-E-11 | 准确性(1/2) | `决策` | search | project | 任一 `architecture_decisions` project scope 条目 | — | 新增（原 EVAL 要求 project=`offical-website-react`，**⚠️ 知识缺口**：fixture 无此项目。降级为任意 project） |
| ACC-E-12 | 准确性(2/2) | `React` | search | project | `AionUi 用 @arco-design/web-react 组件库` | — | 新增（原 EVAL 要求"React 偏好"，**⚠️ 知识缺口**：fixture 中 React 相关仅此条，降级为组件库约束） |
| ACC-E-13 | 关联性(1/2) | `basic-memory 是 AGPL 协议不是 MIT` | get | null | 该条 + 其 related 链至 `不 fork basic-memory 自研 mnemo` | ACC-03-03 部分 | 新增 get-by-title + 验证 related 链 |
| ACC-E-14 | 关联性(2/2) | `FTS5 中文分词不完善` | related | null (depth=2) | `中文搜索先用英文关键词兜底`、`trigram tokenizer 未必够用` | ACC-04-05 | ✅ 复用 |
| ACC-E-15 | 黑话(1/4) | `Phase 1` | search | null | `Phase 路线图 4 阶段` / `Phase 1 并行 5 任务拆分` 任一 | INT-13 | 智能性已覆盖，EVAL 也统计 |
| ACC-E-16 | 黑话(2/4) | `write-gate` | search | null | `write-gate 写入门禁` | — | 新增（原 INT-15） |
| ACC-E-17 | 黑话(3/4) | `claim_type` | search | null | `mnemo Knowledge.claim_type 字段` / `claim_type 断言类型四分类` 任一 | ACC-11-01 | ✅ 部分覆盖 |
| ACC-E-18 | 黑话(4/4) | `TeamCreate` | search | null | `多 Agent 协作必须使用 TeamCreate` | ACC-05-01 | ✅ 复用 |

**统计**：18 条 EVAL 查询中
- 已被前面 ACC 表直接覆盖：6 条（ACC-E-01、06、13、14、15、17、18 —— 实际 7 条，一条跨列）
- 新增测试 case：11 条
- **⚠️ 知识缺口（fixture 需扩充）**：3 条（ACC-E-10 编码规范、ACC-E-11 offical-website-react 决策、ACC-E-12 React 偏好）

**特殊说明**：
- ACC-E-13 使用 `service.get_knowledge(title)` 取条目 + 其 relation 链，验证 related 链中包含预期目标。
- 数据缺口的 3 条 case 如失败，报告须区分"搜索能力不足"还是"fixture 数据缺口"。诊断方式：这 3 条在运行前先做 `service.list_knowledge(limit=10000)` 预扫，确认对应 title 确实不存在 → 标记为 `SKIP (data gap)` 而非 fail。

---

## 5. 相关性 Top-N 覆盖详细规则

### 5.1 派生规则

从 494 条中过滤：`query_type == "search"` 且 `expected_hits` 非空 → 约 440 条进入 REL-T 维度。

### 5.2 三层判定

对每条场景，依次计算：
- **TopHit-1**：`expected_hits` 任一条是否出现在结果前 3 条。
- **TopHit-N**：若 `len(expected_hits) >= 2`，则 `expected_hits` 在前 10 条中的覆盖率 `hit_count / len(expected_hits)` 是否 ≥ 0.5。
- **Any-Hit**：任一 `expected_hits` 是否在结果（limit=20）中，与准确性重叠，仅作对比参考。

### 5.3 汇总指标

```
Top-3 覆盖率 = count(TopHit-1 通过) / 440
多命中覆盖率 = count(TopHit-N 通过) / count(len(expected_hits) >= 2)
```

Top-3 目标 ≥ 60%，多命中目标 ≥ 50%。

### 5.4 Top-N 代表性 case（10 条）

从 440 条 search 场景中挑 10 条跨类别代表，展示"搜什么 → 期望前几条命中什么"。全量仍由 parametrize 覆盖。

| Case ID | 类别 | Query | Scope | 期望 top-3 至少命中 1 条 | 期望 top-10 覆盖率 ≥ 50% 的 expected_hits |
|---------|------|-------|-------|--------------------------|-------------------------------------------|
| REL-T-01 | api_specs | `scope project_name 关系` | project | `mnemo Knowledge.scope 字段` | 3 条：`mnemo Knowledge.scope 字段` / `mnemo Knowledge.project_name 字段` / `DATA_SCHEMA scope 字段规则` |
| REL-T-02 | architecture_decisions | `存储格式` | project | `纯数据库架构不落 markdown 文件` | 2 条：`纯数据库架构不落 markdown 文件` / `SQLite 作为单文件零基础设施存储` |
| REL-T-03 | code_reviews | `refine 模型 丢失` | project | `PersistentCoordinatorLoop refine 轮次丢失 per-task 模型映射` | 2 条：`PersistentCoordinatorLoop refine 轮次丢失 per-task 模型映射` / `aion team 卖点：同团队混合多厂商模型` |
| REL-T-04 | commands | `AionUi PR 前检查` | project | `AionUi 提 PR 前跑 prek` | 3 条：`AionUi 提 PR 前跑 prek` / `AionUi lint 三件套` / `AionUi i18n 校验` |
| REL-T-05 | delivery_rules | `mock 测试` | null | `不 mock 测试` | 2 条：`不 mock 测试` / `单测不是通过而是发现问题` |
| REL-T-06 | env_constraints | `浏览器自动化` | null | `无全局浏览器自动化` | 3 条：`无全局浏览器自动化` / `Playwright 浏览器未安装` / `前端验证走 curl + screencapture` |
| REL-T-07 | pitfalls | `mnemo 风险` | project | `sqlite-vec pre-v1 不稳定` 或 `FTS5 中文分词不完善` | 4 条：`sqlite-vec pre-v1 不稳定` / `FTS5 中文分词不完善` / `Scope creep 加 UI 的冲动` / `Embedding 模型更新导致重建索引` |
| REL-T-08 | status_snapshots | `mnemo 当前阶段` | project | `mnemo 项目阶段 - 截至 2026-04-18` | 2 条：`mnemo 项目阶段 - 截至 2026-04-18` / `mnemo Phase 1 路线图完成状态 - 截至 2026-04-18` |
| REL-T-09 | team_rules | `TeamCreate` | null | `多 Agent 协作必须使用 TeamCreate` | 3 条：`多 Agent 协作必须使用 TeamCreate` / `团队成员必须带 team_name` / `禁止孤立 subAgent` |
| REL-T-10 | tech_surveys | `为什么选 SQLite` | project | `mnemo 选择纯数据库而非向量黑盒` | 3 条：`mnemo 选择纯数据库而非向量黑盒` / `sqlite-vec 是 sqlite-vss 后继者` / `SQLite FTS5 可扩展到 10M 行` |

**判定**：
- top-3 命中 → REL-T-x 的 TopHit-1 维度 PASS
- top-10 覆盖率 ≥ 50% → TopHit-N 维度 PASS
- 两项独立计分，都进入最终汇总。

### 5.5 scope 隔离 case 全量清单（REL-S，10 条）

494 条中共有 10 条同时带 `expected_not_hits` 与 `scope`，全部列出（不采样）。判定口径：执行 `service.search(q, scope=scope)` 或 `service.search_by_tag([q], scope=scope)` 后，`expected_not_hits` 中 **任何一条** title 出现在结果即 FAIL。

| Case ID | 类别 | Query | Type | Scope | 期望命中（必须全中） | 期望不命中（任一出现即 FAIL） |
|---------|------|-------|------|-------|----------------------|------------------------------|
| REL-S-01 | commands | `mnemo 跑测试` | search | project | `mnemo 跑单元测试` | `AionUi 测试命令` |
| REL-S-02 | commands | `mnemo` | tag-search | project | `mnemo 跑单元测试`、`mnemo CLI 本地验证套路`、`mnemo 安装包` | `AionUi 测试命令` |
| REL-S-03 | delivery_rules | `arco-design 原生 HTML` | search | project | `AionUi 禁止 raw HTML 交互元素` | `nft-gmgn 禁止 Chakra UI 新代码` |
| REL-S-04 | delivery_rules | `Chakra UI 组件` | search | project | `nft-gmgn 禁止 Chakra UI 新代码` | `AionUi 禁止 raw HTML 交互元素` |
| REL-S-05 | delivery_rules | `aionui` | tag-search | project | `commit 使用英文 type scope subject 格式`、`AionUi 单目录子项不得超过 10`、`AionUi 禁止 raw HTML 交互元素`（+ 另外 6 条 AionUi 规则） | `nft-gmgn 禁止 Chakra UI 新代码`、`nft-gmgn Tailwind 禁止 p-16 类名` |
| REL-S-06 | delivery_rules | `nft-gmgn` | tag-search | project | `nft-gmgn 禁止 Chakra UI 新代码`、`nft-gmgn 禁止 useState+fetch 组合`、`nft-gmgn Tailwind 禁止 p-16 类名`（+ 另外 4 条 nft-gmgn 规则） | `AionUi 禁止 raw HTML 交互元素` |
| REL-S-07 | status_snapshots | `mnemo 团队` | search | project | `mnemo 调研团队 kb-research 成果 - 2026-04-18`、`mnemo 团队（kb-research）成员 - 2026-04-18` | `ai-store 团队配置 - 截至 2026-04-13` |
| REL-S-08 | test_cases | `mcp-team-hub` | tag-search | project | `mcp-team-hub check_in 正常签到`、`mcp-team-hub 双进程并发清理同一锁（double-free）`、`mcp-team-hub 面板单实例保护`、`mcp-team-hub handoff 任务交接` | `AionUi Hub L1 集成测试：完整安装链路`、`ai-store 冷启动 5 秒内窗口出现` |
| REL-S-09 | test_cases | `Orchestrator` | search | project | `supercell Orchestrator 同名 agent 重复执行拒绝`、`supercell Orchestrator 并发部分失败不影响其他` | `AionUi Hub L1 集成测试：完整安装链路` |
| REL-S-10 | test_cases | `cross-project` | tag-search | global | `cross-project 测试数据目录隔离`、`cross-project 不 mock 测试红线`、`cross-project Playwright 截图归档约定` | `mcp-team-hub check_in 正常签到` |

**汇总**：

```
Scope 隔离通过率 = count(REL-S-x PASS) / 10
```

目标 ≥ 90%（允许 1 条失败），反映跨项目/跨 scope 的过滤能力。

**特别观察点**：
- REL-S-03/04 是镜像对：同 scope（project）下的两个项目隔离方向。理论上都应通过；若只通过一半，说明 FTS5 在某个方向的召回盖过了 scope 过滤，需要调整排序或 bm25 权重。
- REL-S-05/06 是 tag-search 的镜像对。tag-search 按 tag 精确匹配，理论上永远通过（不依赖 FTS5 排序）。如果失败说明 tag 数据或 search_by_tag 实现出了问题。
- REL-S-10 是 global scope 下的"不命中 project-only 条目"验证，反方向校验。

---

## 6. 智能性 case 详细期望

下表是 §2.3 的完整补充，每条给出实现算法与失败诊断信息。

### INT-01 `用户脾气`
- 算法：`service.search("用户脾气")`。
- 期望：返回前 20 条至少包含 `直接简洁不要 AI 腔` / `说话带证据不要含糊` 任一。
- 诊断：失败时打印前 5 条返回与 `tokenize_for_fts("用户脾气")` 结果。

### INT-02 `坑`
- 算法：`service.search("坑")`。
- 期望：至少 1 条 tag 含 `risk`，或 title 含"坑 / 风险 / 陷阱"。
- 诊断：单字查询可能被 FTS5 过滤，若空要打印 tokenize 结果。

### INT-03 `批量测试中文分词`
- 算法：`service.search("批量测试中文分词")`。
- 期望：命中 `FTS5 中文分词不完善` / `中文搜索先用英文关键词兜底` / `FTS5 用 jieba 做中文分词` 任一。
- 等价于现有 `test_chinese_search.py` 的端到端验证。

### INT-04 `不要废话`
- 算法：`service.search("不要废话")`。
- 期望：命中 `直接简洁不要 AI 腔` / `回答完不要再总结` 任一。
- 挑战：字面匹配不上，"废话"与"AI 腔 / 啰嗦"是近义。

### INT-05 `怎么测试`
- 算法：`service.search("怎么测试")`。
- 期望：命中 `不 mock 测试` / `新模块必须带单测` 任一。

### INT-06 `哪些规则不能碰`
- 算法：`service.search("哪些规则不能碰")`。
- 期望：命中至少 1 条 tag 含 `delivery-rule`。

### INT-07 `改代码之前要做什么`
- 算法：`service.search("改代码之前要做什么")`。
- 期望：命中 `修改代码先 bun build 再测试` / `破坏性改动先确认` 任一。

### INT-08 `Agent 新人入门`
- 算法：`service.search("Agent 新人入门")`。
- 期望：命中 `workspace 多项目注册制` / `评估由新 Agent 零上下文验证` 任一。

### INT-09 `做完怎么算交付`
- 算法：`service.search("做完怎么算交付")`。
- 期望：命中 `有证据才能说完成` / `交付前必过 delivery-gate` 任一。

### INT-10 `中文搜不到怎么办`
- 算法：`service.search("中文搜不到怎么办")`。
- 期望：命中 `中文搜索先用英文关键词兜底` / `FTS5 中文分词不完善` 任一。

### INT-11 跨类别 `测试`
- 算法：
  ```
  hits = service.search("测试", limit=50)
  categories = count_distinct_source_file(hits)   # 通过 title 反查 fixture 文件
  ```
- 期望：`categories >= 2`（至少两个 fixture 文件贡献结果）。
- 挑战：验证单词跨多类别分布。

### INT-12 `SQLite 为什么选它`
- 算法：`service.search("SQLite 为什么选它", scope="project")`。
- 期望：命中 `SQLite 作为单文件零基础设施存储` 或 `mnemo 选择纯数据库而非向量黑盒`。

### INT-13 `Phase 1`
- 算法：`service.search("Phase 1")`。
- 期望：命中 `Phase 路线图 4 阶段` / `Phase 1 并行 5 任务拆分` 任一。

### INT-14 同义缩写 `单测`
- 算法：`service.search("单测")`。
- 期望：命中 `新模块必须带单测` / `不 mock 测试` 任一。
- 挑战：用户常说"单测"，正式文档写"单元测试 / mock 测试"；查询词与 title 字面只有 2 字重合（"单测"），考验是否能把 "单测" → "测试"的语义对齐通过 FTS 或 jieba 分词建立。
- 诊断：失败时打印 tokenize 结果，确认 jieba 是否把 "单测" 正确切出。

### INT-15 口语疑问 `这个项目干啥的`
- 算法：`service.search("这个项目干啥的", scope="project")`。
- 期望：命中 `mnemo 产品定位 Agent-first 知识库`。
- 挑战：纯口语化疑问，没有任何专业术语；考验是否能从"项目 / 干啥"推出"定位 / 产品"。
- 诊断：失败时打印 tokenize 结果与 top-5 返回；大概率当前 FTS5 无法命中，属于 hypothesis。

### INT-16 错别字 `数据裤`（hypothesis）
- 算法：`service.search("数据裤")`。
- 期望：命中 `SQLite 作为单文件零基础设施存储` / `纯数据库架构不落 markdown 文件` 任一。
- 挑战：错别字容忍。FTS5 不支持编辑距离/模糊匹配，理论上无法命中。
- 定位：**hypothesis case**，记入智能性维度的报告但不计入硬门禁分母。用于追踪未来引入 trigram / 语义向量时的能力跃升。

### INT-17 图谱跨跳 `不 fork basic-memory 自研 mnemo`
- 算法：`service.get_related("不 fork basic-memory 自研 mnemo", depth=2)`。
- 期望：邻居 title 集合含 `basic-memory 是 AGPL 协议不是 MIT` / `mnemo 用 Proprietary 闭源协议` 任一。
- 挑战：验证 2 跳可达。

### INT-18 图谱多分支 `SQLite 作为单文件零基础设施存储`
- 算法：`service.get_related("SQLite 作为单文件零基础设施存储", depth=2)`。
- 期望：邻居集合含 `纯数据库架构不落 markdown 文件` / `FTS5 用 jieba 做中文分词` 任一。

### INT-19 多 tag AND `user-preference` + `global`
- 算法：`service.search_by_tag(["user-preference", "global"], scope="global", limit=50)`。
- 期望：返回 ≥ 5 条。

### INT-20 口语交付观 `跑通再说完`
- 算法：`service.search("跑通再说完")`。
- 期望：命中 `有证据才能说完成` / `真实验证优先` / `跑起来看得见才信` 任一。
- 挑战：口语化表达"跑通 / 再说完"对应"有证据 / 验证 / 看得见"三个正式表达；考验从"跑"语义到"证据/验证"的关联。

---

## 7. 判定算法伪码

### 7.1 核心判定

```python
async def judge_accuracy(scenario, service) -> dict:
    results = await dispatch_query(scenario, service)
    titles = [r["title"] for r in results]
    expected = scenario.get("expected_hits", [])
    not_expected = scenario.get("expected_not_hits", [])

    hit = sum(1 for t in expected if t in titles)
    miss = len(expected) - hit
    unexpected = sum(1 for t in not_expected if t in titles)

    passed = (miss == 0) and (unexpected == 0)
    return {
        "pass": passed,
        "hit": hit,
        "miss": miss,
        "unexpected_hit": unexpected,
        "missing_titles": [t for t in expected if t not in titles],
        "top5": titles[:5],
    }


async def dispatch_query(scenario, service):
    qt = scenario["query_type"]
    q = scenario["query"]
    scope = scenario.get("scope")
    if qt == "search":
        return await service.search(q, scope=scope, limit=20)
    if qt == "tag-search":
        return await service.search_by_tag([q], scope=scope, limit=50)
    if qt == "related":
        return await service.get_related(q, depth=2)
    raise ValueError(qt)
```

### 7.2 Top-N 判定

```python
async def judge_top_n(scenario, service) -> dict:
    results = await service.search(
        scenario["query"],
        scope=scenario.get("scope"),
        limit=20,
    )
    titles = [r["title"] for r in results]
    expected = scenario["expected_hits"]

    top3_hit = any(t in titles[:3] for t in expected)
    if len(expected) >= 2:
        covered = sum(1 for t in expected if t in titles[:10])
        multi_ok = (covered / len(expected)) >= 0.5
    else:
        multi_ok = None   # 不参与多命中统计
    return {"top3_hit": top3_hit, "multi_ok": multi_ok}
```

### 7.3 汇总报告（test_scenario_report）

最后一个 pytest test 收集各 test 写入的全局 dict，按下格式打印：

```
===== Scenario report =====
Accuracy by category:
  user_preferences      25 / 27  (92.6%)
  delivery_rules        22 / 26  (84.6%)
  ...
Accuracy overall:      412 / 494  (83.4%)

Relevance:
  Top-3 coverage:       310 / 440  (70.5%)
  Multi-hit coverage:   180 / 230  (78.3%)
  Negative scenarios:    10 / 10  (100%)
  Scope isolation:       10 / 10  (100%)

Intelligence:           15 / 19  (78.9%)   [1 hypothesis: INT-16 excluded]
  Failed cases:
    INT-02 `坑` — no results (tokenize produced empty tokens)
    INT-06 `哪些规则不能碰` — missed; top5=[...]

EVAL_CRITERIA E2E:      14 / 17  (82.4%)   [1 skipped: data gap]
  [新 Agent 初到项目]  5/7
  [跨项目切换]         2/3
  [准确性细分]         1/2
  [关联性细分]         2/2
  [项目黑话]           4/4

===== Gates =====
Accuracy overall >= 60%:  PASS (83.4%)   [target 70%: PASS] [excellent 85%: MISS]
Negative = 100%:          PASS
Intelligence >= 50%:      PASS (78.9%)
EVAL E2E >= 70%:          PASS (82.4%)
```

---

## 8. 边界条件与实现注意

### 8.1 数据准备
- session fixture 只灌入一次，后续 test 复用。
- `Knowledge` 表、`knowledge_fts` 虚拟表、`relation` 表必须同 engine 建立，否则 FTS 触发器不生效。
- 两遍灌入：第一遍 `create_knowledge`，第二遍清洗后重跑 `_apply_wikilinks + _apply_manual_relations`，参考 `tests/fixtures/load_fixtures.py:101`。

### 8.2 `query_type == related` 的处理
- scenarios 中 `query` 字段是起点 title，不是搜索词。
- 若 title 不存在（灌入阶段被跳过），case 判 skip 而不是 fail。

### 8.3 并发与隔离
- `KnowledgeService._session_factory` 每次调用 `async with` 开新 session，天然串行。
- 整个 test_scenarios.py 共用一个 service 实例。

### 8.4 依赖
- 依赖 jieba，首次 tokenize 加载字典 0.2~0.3 秒；session fixture 保证只加载一次。

### 8.5 scope 处理
- scenarios 里 `scope="project"` 的场景多数未给 `project_name`；`service.search(..., scope="project")` 会返回所有 project-scope 知识，不做项目名精确过滤，除非 scenario 明确给了 project_name。

---

## 9. 非目标（明确不测什么）

- **不测性能**：不关心 QPS 与响应时间。
- **不测 CLI 和 MCP**：已有 `test_cli.py` / `test_mcp.py` 覆盖。
- **不测写入正确性**：138 个现有 test 已覆盖 CRUD / supersede / wikilink / tag。
- **不测跨进程**：DB 锁与并发写入不在范围。
- **不测 vec embedding**：sqlite-vec 在 Phase 1 未启用。

---

## 10. 风险与回退

| 风险 | 影响 | 回退 |
|------|------|------|
| 某批 scenario 普遍命中率低 | 报告难看但不影响流水线 | 门禁只设总通过率 60%，允许局部低 |
| 灌入失败（fixture 格式错） | 整组测试无法开始 | fixture 已由 `load_fixtures.py` 人工验证通过 |
| 智能性多条不通过 | 预期之内（证明 FTS5 + jieba 当前能力不足） | 门禁 50%，失败 case 进报告驱动优化 |
| `related` 深度 2 找不到目标 | 图谱建设不够或灌入丢边 | 测试内降级到 depth=3 再试，仍失败 report |
| 测试跑很慢 | 494 场景 × 数十 ms | session fixture 复用 DB，整体预计 < 90 秒 |

---

## 11. 交付物（等用户确认后实现）

1. `tests/test_scenarios.py` — pytest 文件，含 session fixture + 三维度测试 + 汇总报告 test。
2. 报告在 `pytest -s` 下 stdout 可见。
3. 不新增全局 conftest.py（fixture 本地化）。
4. 现有 138 个测试保持绿。

---

## 12. 本设计的取舍

- **为什么不全部 hard assert**：FTS5 + jieba 当前命中率达不到 100%，硬 assert 会让每次运行总失败。soft 汇总 + 关键门禁在诊断上价值更大。
- **为什么不单独生成场景**：现有 12 份 scenarios 已 494 条覆盖类别全集，手工再写是浪费。智能性是现有场景不覆盖的（意图理解），才单独写 20 条。
- **为什么 related 只到 depth=2**：当前 relation_repository 支持任意 depth，但 depth ≥ 3 容易引入噪声，目标不是遍历全图。
- **为什么不碰 CLI/MCP**：本次目标是"知识库作为服务"的智能性，不是工具链；CLI/MCP 是薄封装，已有专门测试。
- **为什么门禁设得这么低**：第一版基线要"测得出能力、不误伤变更"，数值反映现状而非理想。后续随着搜索能力提升，门禁可按阶段抬升（60 → 70 → 80）。

---

## 13. EVAL_CRITERIA E2E 验收测试（产品门禁）

`EVAL_CRITERIA.md` 是产品层验收标准，验证"新 Agent 零上下文进入 mnemo 能不能干活"。本节定义 `test_eval_criteria_e2e` — 一个独立的 pytest 方法，按 5 个场景分组跑 18 条查询，给出分组分数和总分，最终以总分 ≥ 70% 作为**硬门禁**。

### 13.1 结构

18 条 EVAL 查询分 5 组：

| 场景组 | 条数 | 对应 ACC-E 编号 |
|--------|------|-----------------|
| 新 Agent 初到项目 | 7 | ACC-E-01 ~ ACC-E-07 |
| 跨项目切换 | 3 | ACC-E-08 ~ ACC-E-10 |
| 准确性细分 | 2 | ACC-E-11 ~ ACC-E-12 |
| 关联性细分 | 2 | ACC-E-13 ~ ACC-E-14 |
| 项目黑话 | 4 | ACC-E-15 ~ ACC-E-18 |
| **合计** | **18** | |

### 13.2 执行与评分

```python
async def test_eval_criteria_e2e(kb_service):
    groups = [
        ("新 Agent 初到项目", EVAL_GROUP_1, 7),
        ("跨项目切换",       EVAL_GROUP_2, 3),
        ("准确性细分",       EVAL_GROUP_3, 2),
        ("关联性细分",       EVAL_GROUP_4, 2),
        ("项目黑话",         EVAL_GROUP_5, 4),
    ]
    report = []
    total_pass = 0
    total = 0
    for name, cases, n in groups:
        group_pass = 0
        for case in cases:
            # 数据缺口预判（见 ACC-E-10/11/12）
            if case.get("data_gap_check"):
                if not await verify_data_exists(kb_service, case["data_gap_check"]):
                    report.append((name, case["id"], "SKIP (data gap)"))
                    total += 0  # 分母也扣
                    continue
            outcome = await judge_accuracy(case, kb_service)
            if outcome["pass"]:
                group_pass += 1
                total_pass += 1
            else:
                report.append((name, case["id"], f"FAIL top5={outcome['top5']}"))
            total += 1
        print(f"[{name}] {group_pass}/{n}")
    pass_rate = total_pass / total if total else 0
    print_report(report, pass_rate)
    assert pass_rate >= 0.70, f"EVAL_CRITERIA E2E 通过率 {pass_rate:.1%} < 70%"
```

### 13.3 报告样例

```
===== EVAL_CRITERIA E2E =====
[新 Agent 初到项目] 5/7
[跨项目切换]       2/3  (1 skip: ACC-E-10 data gap)
[准确性细分]       1/2  (1 skip: ACC-E-11 data gap)
[关联性细分]       2/2
[项目黑话]         4/4
-----
Effective: 14 / 17 (82.4%)   skipped: 1
Gate:      >= 70%             PASS
=============================
```

### 13.4 判定规则

- **硬门禁**：有效通过率（去掉 SKIP 后的分母）≥ 70% → PASS；否则 fail 整个流水线。
- **SKIP 不算失败也不算通过**，用来区分"搜索能力不足"和"fixture 数据缺口"。
- 相同的 18 条 case 也会出现在 §4.13（ACC-E-xx），但 §4.13 算在 494 准确性总通过率里（按场景算分），§13 是独立的"产品验收维度"（按分组加权）。两个维度互不干扰。
- EVAL 新增场景或调整权重时，只改 §4.13 和 §13 的映射，不改动 fixture 源文件。

### 13.5 定位

`test_eval_criteria_e2e` 独立于准确性 / 相关性 / 智能性三大维度，作为**第 4 个顶层测试**放在 §3.2 报告块末尾。其失败意味着"产品层不验收"——这是比 60% 准确性硬门禁更严格的"用户视角门禁"。

当 fixture 补齐（offical-website-react、React 偏好、编码规范三个缺口）后，重跑应能把有效通过率推到 85% 以上；此时可把硬门禁从 70% 提到 85%，对齐 EVAL "优秀" 线。
