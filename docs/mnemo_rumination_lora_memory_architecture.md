# Mnemo 反刍式记忆内化系统设计文档

> 目标：把 Agent 的外界经历从「日志 / 笔记 / RAG 外挂」进一步推进到「可训练、可版本化、可回滚、可评估的长期行为记忆」。
>
> 核心路线：**经历进入 Mnemo → 蒸馏为高质量训练数据 → 空闲时反刍训练 LoRA → 评估通过后发布 LoRA 版本 → 运行时按场景加载。**

---

## 0. 一句话定义

Mnemo 不只是知识库。

Mnemo 应该成为 Agent 的：

```text
海马体 + 睡眠反刍系统 + 记忆蒸馏器 + LoRA 训练工厂 + 记忆版本注册中心
```

最终形成：

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

---

## 1. 背景与问题

当前大模型的问题是：

```text
输入 → 推理 → 输出 → 进程结束 / 状态死亡
```

这导致：

1. 每次对话都像重新唤醒。
2. 外挂记忆只是“笔记”，并没有真正改变模型行为。
3. Agent 不能像人一样把经历沉淀成长期倾向。
4. 项目经验、用户偏好、纠错反馈容易丢失。
5. 模型无法在空闲时“反刍”和“内化”。

目标不是简单做 RAG，而是做一条更接近人类记忆机制的链路：

```text
经历记录
  ↓
短期记忆
  ↓
重要性筛选
  ↓
睡眠反刍
  ↓
长期内化
  ↓
行为改变
```

---

## 2. 设计目标

### 2.1 核心目标

1. **持续记录经历**
   - 用户消息
   - Agent 输出
   - 外部工具结果
   - 环境事件
   - 用户纠错
   - 成功 / 失败任务
   - 代码修改、测试结果、PRD 变更

2. **从日志中抽取可训练记忆**
   - 偏好
   - 事实
   - 行为规则
   - 项目知识
   - 错误案例
   - 成功案例
   - 用户长期习惯
   - Agent 自身任务经验

3. **蒸馏成高质量训练样本**
   - 不直接用原始日志训练。
   - 必须清洗、归纳、去重、打分、分类。

4. **在空闲时训练 LoRA**
   - 小批量、低成本、可中断。
   - 训练完成后进入评估门禁。

5. **LoRA 版本化**
   - 可发布。
   - 可回滚。
   - 可对比。
   - 可禁用。
   - 可按场景加载。

6. **建立遗忘机制**
   - 短期记忆自然衰减。
   - 长期记忆也需要根据新经验更新。
   - 过期偏好不能永久固化。

---

## 3. 不做什么

第一版不要做这些：

1. 不追求实时修改主模型权重。
2. 不直接训练 Base Model。
3. 不直接把所有日志塞进训练集。
4. 不把所有 LoRA 无限叠加。
5. 不自动上线未经评估的 LoRA。
6. 不让项目记忆污染用户通用人格。
7. 不把短期情绪、临时任务、一次性偏好训练成长期记忆。

---

## 4. 总体架构

```text
┌──────────────────────────────────────┐
│              外界事件流               │
│ 用户消息 / 环境变化 / 工具结果 / 反馈   │
└──────────────────┬───────────────────┘
                   ↓
┌──────────────────────────────────────┐
│          Mnemo Event Log              │
│ 原始经历日志，可审计、可追溯             │
└──────────────────┬───────────────────┘
                   ↓
┌──────────────────────────────────────┐
│          Working Memory               │
│ 短期记忆池：最近状态、注意力、上下文       │
└──────────────────┬───────────────────┘
                   ↓
┌──────────────────────────────────────┐
│          Memory Candidate Pool        │
│ 候选记忆池：重要、重复、纠错、高惊讶事件   │
└──────────────────┬───────────────────┘
                   ↓
┌──────────────────────────────────────┐
│          Distiller                    │
│ 蒸馏器：清洗、归纳、分类、打分、转样本     │
└──────────────────┬───────────────────┘
                   ↓
┌──────────────────────────────────────┐
│          Training Dataset Registry    │
│ 高质量训练数据集，带来源、权重、版本       │
└──────────────────┬───────────────────┘
                   ↓
┌──────────────────────────────────────┐
│          Rumination Job               │
│ 空闲反刍任务：训练 LoRA / Adapter        │
└──────────────────┬───────────────────┘
                   ↓
┌──────────────────────────────────────┐
│          Eval Gate                    │
│ 评估门禁：回归、偏好、幻觉、污染、遗忘     │
└──────────────────┬───────────────────┘
                   ↓
┌──────────────────────────────────────┐
│          LoRA Registry                │
│ LoRA 版本库：stable / rejected / draft  │
└──────────────────┬───────────────────┘
                   ↓
┌──────────────────────────────────────┐
│          Runtime Loader               │
│ 运行时按用户 / 项目 / 任务加载 LoRA        │
└──────────────────────────────────────┘
```

---

## 5. 核心模块

---

### 5.1 Event Log：经历日志层

作用：

```text
完整记录 Agent 的经历。
```

包括：

| 类型 | 示例 |
|---|---|
| 用户消息 | 用户说“不要啰嗦” |
| Agent 输出 | Agent 给出过长回答 |
| 用户纠错 | 用户说“你理解错了” |
| 工具结果 | 运行测试失败 |
| 外部环境 | 摄像头看到某物体 |
| 系统事件 | Agent 空闲 20 分钟 |
| 任务事件 | AionUI 后端重构完成 |
| 项目决策 | 决定使用 PostgreSQL + Redis |
| 失败案例 | Claude team 假装多 Agent |

建议字段：

```json
{
  "event_id": "evt_20260506_000001",
  "timestamp": "2026-05-06T18:30:00+08:00",
  "source": "user | agent | tool | environment | system",
  "project_id": "aionui",
  "agent_id": "agent_leader_001",
  "session_id": "session_abc",
  "event_type": "message | correction | decision | failure | success | observation | tool_result",
  "content": "用户要求：不要啰嗦，给我直接可执行方案。",
  "raw_payload": {},
  "importance": 0.82,
  "surprise": 0.71,
  "emotion": "neutral | positive | negative | urgent",
  "ttl": "short | medium | long | permanent_candidate",
  "privacy_level": "private | project | team | public",
  "tags": ["preference", "style", "response_policy"]
}
```

---

### 5.2 Working Memory：短期工作记忆

作用：

```text
让 Agent 的下一次反应受当前状态影响。
```

不是永久记忆，而是“当前脑内状态”。

应包含：

```json
{
  "agent_id": "agent_001",
  "current_focus": "AionUI backend 重构",
  "recent_user_intent": "希望把 Mnemo 做成反刍式记忆系统",
  "active_goals": [
    "设计日志到 LoRA 的记忆内化链路",
    "保证可评估、可回滚"
  ],
  "recent_events": ["evt_001", "evt_002"],
  "attention_objects": ["Mnemo", "LoRA", "Rumination Job"],
  "mood_state": "focused",
  "expires_at": "2026-05-06T20:30:00+08:00"
}
```

短期记忆需要遗忘：

```text
时间越久，权重越低。
重复出现，权重上升。
用户纠错，权重上升。
被长期内化后，短期权重下降。
```

---

### 5.3 Memory Candidate Pool：候选记忆池

不是所有日志都能训练。

只有满足条件的事件才进入候选池：

| 条件 | 说明 |
|---|---|
| 高频重复 | 用户多次强调的偏好 |
| 明确纠错 | “你搞错了，应该这样” |
| 高重要性 | 项目关键决策 |
| 高惊讶度 | 与过去认知冲突的信息 |
| 高价值案例 | 成功解决过的任务 |
| 高风险失败 | 反复出错的问题 |
| 长期稳定 | 不是临时偏好 |

候选记忆结构：

```json
{
  "candidate_id": "memcand_001",
  "source_events": ["evt_001", "evt_002", "evt_003"],
  "candidate_type": "preference | fact | skill | behavior_rule | negative_case | project_knowledge",
  "summary": "用户偏好简洁、直接、可执行的回答，尤其在技术决策中反感啰嗦。",
  "evidence": [
    "用户多次说：不要啰嗦",
    "用户要求：立刻回复、言简意赅"
  ],
  "importance": 0.91,
  "stability": 0.88,
  "trainability": 0.84,
  "risk": 0.22,
  "status": "pending_distill"
}
```

---

### 5.4 Distiller：记忆蒸馏器

作用：

```text
把候选记忆转成高质量训练样本。
```

蒸馏器必须做 7 件事：

1. 去重。
2. 去噪。
3. 判断是否长期有效。
4. 判断是否适合训练。
5. 标记适用范围。
6. 生成正例和反例。
7. 保留来源追溯。

输入：

```text
多条 Event Log + Memory Candidate
```

输出：

```text
高质量训练样本 JSONL
```

样本格式建议：

```json
{
  "sample_id": "train_001",
  "memory_type": "preference",
  "scope": "user_global",
  "project_id": null,
  "source_events": ["evt_001", "evt_002"],
  "instruction": "根据用户长期偏好，回答技术问题时应直接、准确、少废话，并优先给可执行结论。",
  "input": "stdio 和 JSON-RPC 是什么关系？",
  "output": "stdio 是传输管道，JSON-RPC 是消息格式。stdio 负责把字节流传过去，JSON-RPC 规定这些字节流里的请求、响应、错误长什么样。",
  "negative_output": "stdio 和 JSON-RPC 是现代软件架构中非常重要的两个概念，我们可以从历史开始讲起……",
  "quality_score": 0.93,
  "importance_score": 0.89,
  "risk_score": 0.17,
  "created_at": "2026-05-06T18:45:00+08:00"
}
```

---

## 6. 训练数据类型

### 6.1 用户偏好类

```json
{
  "memory_type": "preference",
  "instruction": "用户偏好简洁、直接、准确、可执行的技术回答。",
  "input": "帮我解释一下 ACP 和 MCP 的区别。",
  "output": "ACP 管 Agent 会话，MCP 管工具调用。ACP 更像会话协议，MCP 更像工具协议。两者可以配合，但不是一层东西。"
}
```

### 6.2 行为规则类

```json
{
  "memory_type": "behavior_rule",
  "instruction": "当用户要求生成提示词时，应输出可直接复制使用的完整版本。",
  "input": "给我一个完整提示词。",
  "output": "下面是可直接复制使用的完整提示词：..."
}
```

### 6.3 项目知识类

```json
{
  "memory_type": "project_knowledge",
  "scope": "project:aionui",
  "instruction": "AionUI 的核心目标是构建一个 AI 团队操作系统，支持多 Agent 协作、权限、记忆、监督和可视化管理。",
  "input": "AionUI 的核心定位是什么？",
  "output": "AionUI 是一个 AI 团队操作系统，目标不是普通聊天工具，而是让数字员工以团队形式协作、沉淀记忆、接受监督并持续成长。"
}
```

### 6.4 纠错类

```json
{
  "memory_type": "correction",
  "instruction": "用户纠正过：多个 Agent 应被理解为多个独立进程或独立上下文，而不是单上下文角色扮演。",
  "input": "Claude team 是不是只是单上下文角色扮演？",
  "output": "不能直接这么判断。你关注的是是否存在独立 Agent 进程、独立上下文、任务分发和可验证的团队证据，而不是表面上有没有多个名字。"
}
```

### 6.5 负例类

```json
{
  "memory_type": "negative_case",
  "instruction": "不要在用户明确要求不要啰嗦时输出长篇背景介绍。",
  "input": "tmux 是干什么的？立刻告诉我。",
  "output": "tmux 是终端复用器：一个终端里开多个会话，断开后任务还能继续跑，回来还能恢复。",
  "negative_output": "tmux 是一个非常强大的命令行工具，最早诞生于..."
}
```

### 6.6 技能类

```json
{
  "memory_type": "skill",
  "instruction": "当用户要求设计 Agent 团队提示词时，需要包含角色、职责、验证机制、测试要求、记忆文件、交付标准。",
  "input": "给我一个 Claude Code team 的完整提示词。",
  "output": "你是 aion-forge 团队总控，请创建真实多 Agent 团队..."
}
```

---

## 7. 反刍任务 Rumination Job

### 7.1 触发条件

第一版建议简单粗暴：

```text
每 10 分钟：扫描日志，生成候选记忆
每 1 小时：蒸馏候选记忆
累计 200 条高质量样本：触发小反刍
累计 1000 条高质量样本：触发大反刍
设备空闲 + 电源接入 + 温度正常：开始训练
```

触发字段：

```json
{
  "job_id": "ruminate_001",
  "trigger": "sample_threshold | idle_time | manual | scheduled",
  "dataset_id": "dataset_user_20260506_v001",
  "target_model": "Qwen3.5-4B-MLX",
  "lora_scope": "user_global | project | agent_role",
  "sample_count": 500,
  "status": "pending | running | evaluating | completed | failed | rejected"
}
```

---

### 7.2 反刍任务流程

```text
1. 选择数据集
2. 数据质量检查
3. 切分 train / eval
4. 启动 LoRA 训练
5. 保存 draft LoRA
6. 跑评估集
7. 与上一版本对比
8. 通过则发布 stable
9. 不通过则 rejected
10. 记录版本和指标
```

---

## 8. LoRA 版本管理

### 8.1 命名规范

```text
mnemo-lora-user-global-v001
mnemo-lora-user-global-v002

mnemo-lora-project-aionui-v001
mnemo-lora-project-aionui-v002

mnemo-lora-agent-product-v001
mnemo-lora-agent-backend-v001
mnemo-lora-agent-qa-v001
```

### 8.2 Registry 结构

```json
{
  "lora_id": "mnemo-lora-project-aionui-v003",
  "base_model": "Qwen3.5-4B-MLX",
  "scope": "project:aionui",
  "parent_lora": "mnemo-lora-project-aionui-v002",
  "dataset_id": "dataset_aionui_20260506_v004",
  "status": "draft | stable | rejected | deprecated | rollback",
  "created_at": "2026-05-06T23:00:00+08:00",
  "metrics": {
    "preference_eval": 0.91,
    "project_memory_eval": 0.88,
    "anti_hallucination_eval": 0.86,
    "regression_eval": 0.93,
    "forgetting_eval": 0.82
  },
  "notes": "增强 AionUI 三层架构、虚拟 UI 权限层、Agent session 持久化相关记忆。"
}
```

### 8.3 状态机

```text
draft
  ↓
evaluating
  ↓
stable / rejected
  ↓
deprecated / rollback
```

上线规则：

```text
只有 stable LoRA 可以被 Runtime Loader 默认加载。
draft 只能用于测试。
rejected 禁止加载。
deprecated 仅保留历史。
rollback 表示当前已回滚到该版本。
```

---

## 9. Runtime Loader：运行时加载策略

运行时不要加载所有 LoRA。

建议采用：

```text
Base Model
  + User Global LoRA
  + Current Project LoRA
  + Current Agent Role LoRA
  + Working Memory Context
```

示例：

```json
{
  "runtime_id": "runtime_aionui_backend_agent",
  "base_model": "Qwen3.5-4B-MLX",
  "loaded_loras": [
    "mnemo-lora-user-global-v005",
    "mnemo-lora-project-aionui-v003",
    "mnemo-lora-agent-backend-v002"
  ],
  "working_memory_id": "wm_agent_backend_001",
  "retrieval_enabled": true,
  "max_lora_count": 3
}
```

规则：

1. 通用用户偏好加载 `user_global`。
2. 当前项目加载 `project`。
3. 当前 Agent 角色加载 `agent_role`。
4. 临时任务不训练 LoRA，只放 Working Memory。
5. 高风险 LoRA 需要人工确认。
6. 不同项目 LoRA 默认隔离。

---

## 10. 遗忘机制

### 10.1 短期记忆遗忘

短期记忆权重：

```text
memory_weight = importance × recency × repetition × correction_boost × emotional_intensity
```

简单衰减：

```text
每小时衰减 10% ~ 30%
被再次触发则增强
被长期内化后从短期池移除
```

### 10.2 长期记忆更新

长期记忆不能永久不变。

需要标记：

```json
{
  "memory_id": "mem_user_style_001",
  "content": "用户偏好简洁回答。",
  "last_confirmed_at": "2026-05-06T18:00:00+08:00",
  "confidence": 0.94,
  "decay_rate": 0.01,
  "contradiction_count": 0,
  "status": "active"
}
```

如果出现新证据：

```text
旧偏好：用户喜欢极简回答
新证据：用户要求深度报告
处理：不是覆盖，而是条件化
```

变成：

```text
技术排障：极简
架构设计：中等详细
深度调研：结构化长文
生成文件：完整详细
```

---

## 11. 评估门禁 Eval Gate

训练后必须评估。

### 11.1 必备评估集

```text
user_preference_eval.jsonl
project_memory_eval.jsonl
anti_hallucination_eval.jsonl
behavior_regression_eval.jsonl
forgetting_eval.jsonl
cross_project_pollution_eval.jsonl
```

### 11.2 评估维度

| 维度 | 目标 |
|---|---|
| 用户偏好 | 是否更符合用户风格 |
| 项目记忆 | 是否记住项目关键事实 |
| 行为规则 | 是否按用户规则行动 |
| 抗幻觉 | 是否减少瞎编 |
| 回归能力 | 是否没有破坏基础能力 |
| 跨项目隔离 | 是否没有把 A 项目知识带到 B 项目 |
| 遗忘控制 | 是否没有记住不该记的临时信息 |

### 11.3 通过标准

第一版建议：

```json
{
  "min_preference_eval": 0.85,
  "min_project_memory_eval": 0.80,
  "min_anti_hallucination_eval": 0.85,
  "min_regression_eval": 0.90,
  "max_cross_project_pollution": 0.05,
  "max_bad_memory_activation": 0.03
}
```

不满足则：

```text
LoRA 标记 rejected
保留日志
不进入 stable
生成失败分析报告
```

---

## 12. 数据库设计草案

### 12.1 mnemo_events

```sql
CREATE TABLE mnemo_events (
  id TEXT PRIMARY KEY,
  timestamp TEXT NOT NULL,
  source TEXT NOT NULL,
  project_id TEXT,
  agent_id TEXT,
  session_id TEXT,
  event_type TEXT NOT NULL,
  content TEXT NOT NULL,
  raw_payload JSON,
  importance REAL DEFAULT 0,
  surprise REAL DEFAULT 0,
  emotion TEXT,
  ttl TEXT,
  privacy_level TEXT,
  tags JSON,
  created_at TEXT NOT NULL
);
```

### 12.2 memory_candidates

```sql
CREATE TABLE memory_candidates (
  id TEXT PRIMARY KEY,
  candidate_type TEXT NOT NULL,
  summary TEXT NOT NULL,
  source_events JSON NOT NULL,
  evidence JSON,
  importance REAL DEFAULT 0,
  stability REAL DEFAULT 0,
  trainability REAL DEFAULT 0,
  risk REAL DEFAULT 0,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

### 12.3 training_samples

```sql
CREATE TABLE training_samples (
  id TEXT PRIMARY KEY,
  dataset_id TEXT,
  memory_type TEXT NOT NULL,
  scope TEXT NOT NULL,
  project_id TEXT,
  source_events JSON NOT NULL,
  instruction TEXT NOT NULL,
  input TEXT NOT NULL,
  output TEXT NOT NULL,
  negative_output TEXT,
  quality_score REAL DEFAULT 0,
  importance_score REAL DEFAULT 0,
  risk_score REAL DEFAULT 0,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL
);
```

### 12.4 rumination_jobs

```sql
CREATE TABLE rumination_jobs (
  id TEXT PRIMARY KEY,
  trigger_type TEXT NOT NULL,
  dataset_id TEXT NOT NULL,
  target_model TEXT NOT NULL,
  lora_scope TEXT NOT NULL,
  sample_count INTEGER NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  logs TEXT,
  created_at TEXT NOT NULL
);
```

### 12.5 lora_registry

```sql
CREATE TABLE lora_registry (
  id TEXT PRIMARY KEY,
  base_model TEXT NOT NULL,
  scope TEXT NOT NULL,
  project_id TEXT,
  parent_lora TEXT,
  dataset_id TEXT NOT NULL,
  status TEXT NOT NULL,
  metrics JSON,
  path TEXT NOT NULL,
  notes TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

### 12.6 eval_results

```sql
CREATE TABLE eval_results (
  id TEXT PRIMARY KEY,
  lora_id TEXT NOT NULL,
  eval_set TEXT NOT NULL,
  score REAL NOT NULL,
  details JSON,
  passed BOOLEAN NOT NULL,
  created_at TEXT NOT NULL
);
```

---

## 13. API 草案

### 13.1 写入事件

```http
POST /api/mnemo/events
```

```json
{
  "source": "user",
  "project_id": "aionui",
  "agent_id": "agent_leader",
  "session_id": "session_001",
  "event_type": "correction",
  "content": "用户要求：不要啰嗦，直接给可执行方案。",
  "importance": 0.9,
  "tags": ["preference", "style"]
}
```

---

### 13.2 扫描候选记忆

```http
POST /api/mnemo/candidates/scan
```

```json
{
  "project_id": "aionui",
  "time_range": "last_24h",
  "min_importance": 0.65
}
```

---

### 13.3 蒸馏训练样本

```http
POST /api/mnemo/distill
```

```json
{
  "candidate_ids": ["memcand_001", "memcand_002"],
  "scope": "project:aionui",
  "output_dataset": "dataset_aionui_20260506_v001"
}
```

---

### 13.4 创建反刍任务

```http
POST /api/mnemo/rumination-jobs
```

```json
{
  "dataset_id": "dataset_aionui_20260506_v001",
  "target_model": "Qwen3.5-4B-MLX",
  "lora_scope": "project:aionui",
  "train_mode": "lora"
}
```

---

### 13.5 查询 LoRA 版本

```http
GET /api/mnemo/loras?scope=project:aionui
```

---

### 13.6 激活 LoRA

```http
POST /api/mnemo/loras/activate
```

```json
{
  "runtime_id": "runtime_aionui_agent",
  "lora_ids": [
    "mnemo-lora-user-global-v005",
    "mnemo-lora-project-aionui-v003"
  ]
}
```

---

## 14. 后台任务设计

### 14.1 event_scanner

频率：

```text
每 10 分钟
```

职责：

```text
扫描新事件 → 计算重要性 → 生成候选记忆
```

---

### 14.2 memory_distiller

频率：

```text
每 1 小时 / 手动触发
```

职责：

```text
候选记忆 → 高质量训练样本
```

---

### 14.3 rumination_scheduler

频率：

```text
每 15 分钟
```

职责：

```text
判断是否满足训练条件：
- 样本数量达标
- 机器空闲
- 电源接入
- 温度正常
- 没有高优先级任务
```

---

### 14.4 lora_trainer

职责：

```text
执行 LoRA 训练
保存 checkpoint
支持中断恢复
输出训练日志
```

---

### 14.5 eval_runner

职责：

```text
跑固定评估集
生成评估报告
决定 stable / rejected
```

---

### 14.6 runtime_loader

职责：

```text
根据用户、项目、Agent 角色加载合适 LoRA
```

---

## 15. MVP 版本路线

### V0：日志层

目标：

```text
先把所有经历存下来。
```

交付：

- `mnemo_events` 表
- 事件写入 API
- 基础重要性评分
- 最近事件查询
- Agent 对话日志接入
- 工具结果接入

验收：

```text
可以完整回放一个 Agent session。
```

---

### V1：候选记忆 + 蒸馏

目标：

```text
从日志里抽出可训练记忆。
```

交付：

- `memory_candidates` 表
- 候选记忆扫描器
- 蒸馏器
- `training_samples` 表
- JSONL 导出

验收：

```text
每天能生成 100~500 条高质量训练样本。
```

---

### V2：手动反刍训练 LoRA

目标：

```text
先人工触发训练，避免自动污染。
```

交付：

- Rumination Job
- LoRA 训练脚本
- LoRA Registry
- 训练日志
- 手动发布 stable

验收：

```text
可以基于 Mnemo 数据训练出第一个 project LoRA。
```

---

### V3：评估门禁

目标：

```text
防止记错、训坏、污染。
```

交付：

- 用户偏好评估集
- 项目记忆评估集
- 抗幻觉评估集
- 回归测试
- 评估报告
- rejected / stable 状态机

验收：

```text
LoRA 不通过评估不能上线。
```

---

### V4：自动反刍

目标：

```text
进入真正的“睡眠学习”。
```

交付：

- 空闲检测
- 样本阈值检测
- 自动训练
- 自动评估
- 自动发布候选
- 人工确认上线

验收：

```text
晚上机器空闲时自动训练，早上生成记忆更新报告。
```

---

### V5：多 LoRA 路由

目标：

```text
不同用户、项目、Agent 角色加载不同记忆。
```

交付：

- Runtime Loader
- 用户 LoRA
- 项目 LoRA
- Agent 角色 LoRA
- LoRA 冲突检测
- 回滚策略

验收：

```text
AionUI 项目不会污染智能胚胎项目。
```

---

### V6：遗忘与重整合

目标：

```text
长期记忆也能更新和遗忘。
```

交付：

- 记忆衰减
- 冲突检测
- 记忆合并
- 旧 LoRA 废弃
- 新旧 LoRA 对比

验收：

```text
旧错误偏好能被新证据修正。
```

---

## 16. 关键风险

### 16.1 错误记忆固化

问题：

```text
Agent 一次理解错了，被训练进 LoRA。
```

解决：

```text
必须保留 source_events。
必须有用户纠错权重。
必须经过 eval gate。
```

---

### 16.2 临时偏好永久化

问题：

```text
用户某一次说“详细点”，不代表永远喜欢长文。
```

解决：

```text
蒸馏时标记 scope 和 condition。
```

例如：

```text
用户在“生成完整 MD 文件”时需要详细；
用户在“解释一个命令”时需要简洁。
```

---

### 16.3 项目污染

问题：

```text
AionUI 的架构知识污染其他项目。
```

解决：

```text
project LoRA 隔离。
Runtime Loader 按项目加载。
跨项目污染评估。
```

---

### 16.4 LoRA 叠加混乱

问题：

```text
user LoRA + project LoRA + agent LoRA 冲突。
```

解决：

```text
限制最大加载数量。
建立优先级。
冲突时以当前项目和用户明确指令优先。
```

---

### 16.5 模型变啰嗦或变固执

问题：

```text
训练后模型过拟合用户习惯，失去弹性。
```

解决：

```text
加入 negative_output。
加入风格回归测试。
加入多场景评估。
```

---

## 17. 训练样本质量标准

每条样本必须满足：

| 标准 | 要求 |
|---|---|
| 来源明确 | 必须能追溯 source_events |
| 长期有效 | 不是一次性上下文 |
| 表达清晰 | instruction 不含歧义 |
| 输出高质量 | output 是理想行为 |
| 有适用范围 | user / project / agent |
| 风险可控 | 不含隐私泄露或错误事实 |
| 可评估 | 可以设计测试问题验证 |

---

## 18. 记忆优先级

运行时冲突时按下面顺序：

```text
1. 当前用户明确指令
2. 当前任务约束
3. 当前项目规则
4. 当前 Agent 角色规则
5. 用户长期偏好
6. 历史项目经验
7. 通用模型能力
```

示例：

```text
用户长期偏好：不要啰嗦
当前任务：写完整 MD 文件
处理：当前任务优先，因此需要完整，不应该过短
```

---

## 19. 和 RAG 的关系

RAG 不是废物，但它不是最终记忆。

推荐关系：

```text
RAG：外部知识查询
Working Memory：当前状态
LoRA：长期行为偏置
Event Log：经历证据
Distiller：记忆加工
```

RAG 适合：

```text
查事实
查文档
查历史记录
查项目文件
```

LoRA 适合：

```text
用户偏好
行为风格
稳定项目规则
Agent 工作习惯
反复纠错后的模式
```

不要用 LoRA 记所有事实。

应该用 LoRA 记：

```text
怎么反应
怎么判断
怎么遵守偏好
怎么执行项目风格
```

用 RAG 记：

```text
具体文档
具体数字
具体日志
具体历史记录
```

---

## 20. 推荐第一版落地方案

### 20.1 最小闭环

```text
1. Mnemo 记录事件
2. 每天扫描重要事件
3. 自动生成训练样本
4. 人工确认训练样本
5. 手动触发 LoRA 训练
6. 跑固定 eval
7. 通过后 stable
8. Runtime 加载 stable LoRA
```

---

### 20.2 第一版目录结构

```text
mnemo/
  memory/
    events/
    candidates/
    datasets/
    evals/
    loras/
    reports/

  jobs/
    event_scanner.ts
    memory_distiller.ts
    rumination_scheduler.ts
    lora_trainer.ts
    eval_runner.ts

  schemas/
    event.schema.json
    candidate.schema.json
    training_sample.schema.json
    lora_registry.schema.json

  eval_sets/
    user_preference_eval.jsonl
    project_memory_eval.jsonl
    anti_hallucination_eval.jsonl
    regression_eval.jsonl

  reports/
    rumination_report_20260506.md
```

---

## 21. 给开发 Agent 的执行提示词

可以直接复制给本地开发团队：

```text
你们现在要为 Mnemo 实现“反刍式记忆内化系统”的 MVP。

目标不是普通 RAG，而是：
Event Log → Memory Candidate → Distilled Training Samples → Rumination Job → LoRA Registry → Eval Gate → Runtime Loader。

硬性要求：

1. 先实现事件日志层
   - 设计 mnemo_events 表
   - 支持用户消息、Agent 输出、工具结果、用户纠错、项目决策、失败案例
   - 每条事件必须有 source、project_id、agent_id、session_id、event_type、content、importance、tags、timestamp

2. 实现候选记忆层
   - 从事件中抽取 memory_candidates
   - 支持 preference、fact、skill、behavior_rule、negative_case、project_knowledge
   - 每条候选必须有 source_events，不能失去来源

3. 实现蒸馏层
   - 把候选记忆转成 training_samples
   - 输出 JSONL
   - 每条样本必须有 instruction、input、output、scope、source_events、quality_score、risk_score
   - 不允许直接拿原始日志训练

4. 实现反刍任务
   - rumination_jobs 表
   - 支持手动触发
   - 训练任务可以先 mock，但接口和状态机必须完整
   - 状态包括 pending、running、evaluating、completed、failed、rejected

5. 实现 LoRA Registry
   - 记录 lora_id、base_model、scope、dataset_id、status、metrics、path
   - status 必须支持 draft、stable、rejected、deprecated、rollback
   - 只有 stable 可以被默认加载

6. 实现 Eval Gate
   - 先用 mock eval
   - 必须有 user_preference_eval、project_memory_eval、anti_hallucination_eval、regression_eval
   - 不通过不能 stable

7. 实现 Runtime Loader 草案
   - 根据 user_global + project + agent_role 选择 LoRA
   - 限制最多加载 3 个 LoRA
   - 不同项目默认隔离

8. 输出内容
   - 数据库迁移
   - API 路由
   - 后台 job
   - 基础 UI 页面
   - README
   - 测试用例
   - 一份 rumination_report 示例

验收标准：

- 能完整记录一个 session 的事件
- 能从事件生成候选记忆
- 能从候选记忆生成训练样本 JSONL
- 能创建一个 rumination job
- 能注册一个 mock LoRA
- 能跑 mock eval
- 只有 eval 通过的 LoRA 能标记 stable
- Runtime Loader 能给指定 project 返回应加载的 LoRA 列表

不要做空架子。
每个 API 必须能真实调用。
每个表必须能写入和查询。
每个状态必须能流转。
所有关键操作必须有测试。
```

---

## 22. 最终判断

这条路线是目前最适合 Mnemo 的现实方案：

```text
不是纯外挂记忆
不是幻想实时改大模型
而是用工程方式模拟：
经历 → 短期记忆 → 睡眠反刍 → 长期记忆
```

它的优点：

1. 能快速落地。
2. 能利用本地模型和本地算力。
3. 可审计。
4. 可回滚。
5. 可评估。
6. 可逐步自动化。
7. 和 AionUI / MTeam / 智能胚胎方向一致。

最终产品形态应该是：

```text
Mnemo = Agent 的记忆器官
AionUI / MTeam = Agent 的社会环境
LoRA Registry = Agent 的长期人格和技能沉淀
Rumination Job = Agent 的睡眠学习机制
```

一句话：

```text
Mnemo 不应该只是“给 Agent 查资料的知识库”。
Mnemo 应该成为“让 Agent 通过经历持续变成另一个自己的记忆系统”。
```
