# mnemo 使用规范（写入 CLAUDE.md）

mnemo 是 Agent 的长期知识库，通过 MCP 连接。

本文档只讲"什么时候用、怎么判断、铁则是什么"。tool 描述讲"做什么、参数含义、返回什么"（见 TOOL_DESCRIPTION_DESIGN.md），工作流文档讲"多工具时序和闭环"（见 AGENT_MNEMO_WORKFLOW.md）。三份不重复。

---

## 两条铁则（违反即未按流程工作）

**R1. 任务起点必须 search**
接到新任务、新需求、新 bug 时，先 `search` 再动手。豁免：
- 用户明示"不用查记忆"
- 非任务闲聊（问候、确认、纯格式转换）
- 上一步 search 已经覆盖该上下文

**R2. 用过的知识必须 feedback**
经 `search` / `get_knowledge` 命中并**实际影响了决策或代码**的条目，交付前调用 `feedback_knowledge`。
- 只是瞟一眼标题没用 → 不用 feedback
- "影响了输出"才算"用过"

---

## 1. 查询

**必须查（R1 正向清单）：**
- 接到新任务、新需求、新 bug
- 遇到不确定的技术选型、API 用法、报错
- 用户提到"之前"/"上次"/"以前做过"
- 要动陌生模块或陌生配置前

**不该查（R1 豁免）：**
- 简单对话、纯格式操作、无状态计算
- 用户已给明确指令且不需要背景
- 用户明示"不用查"

**搜索策略：**
- 先宽后窄：1-2 个关键词试水，有结果再加 `tags` / `scope` 收窄
- 标 `task_context`（`coding` / `debug` / `decision` / `onboarding` / `general`），让 rerank 对齐场景
- 先 `scope=project` 再 `scope=global`，避免一上来全库搜稀释相关性

---

## 2. 写入

**值得 create（以 AGENT_MNEMO_WORKFLOW.md T5 表为权威口径）：**
- 踩坑 + 解法
- 决策依据（为什么选 A 不选 B）
- 环境/配置的非显然约束
- 用户明确纠正的错误做法

**不该 create：**
- 代码/git log 能直接读到的事实
- 一次性、不会再遇到的场景
- 用户没确认的猜测、未验证的想法
- 纯情绪吐槽

**写入流程：**
1. 先 `search` 查重
2. `create_knowledge` 后读返回里的 `write_gate.recommended_action`
   - `supersede` → 改用 `update_knowledge(id=被推荐id)`
   - `review` → 比较差异，增强 source 或合并进老条
   - `create` → 正常入库
3. 内容过时修订 → `update_knowledge`；彻底废弃 → `archive_knowledge(reason=...)`；不要用 `delete_knowledge` 代替归档

**scope 默认：**
- 个人临时踩坑、未验证 → `session`
- 本项目通用经验（默认档）→ `project`
- 跨项目通用结论、已多次验证 → `global`
- 升级路径：session → 复用验证通过后 → `update_knowledge(scope=project/global)`
- 新经验默认落 `project`，不要未验证就灌 `global`

---

## 3. 反馈

触发：**被采用并影响了输出**的条目。读过没用的不 feedback。

三档 signal：
- **helpful** —— 采用后推进了任务
- **misleading** —— 误导方向 / 让一开始走错
- **outdated** —— API 变了、依赖升了、方案废弃了

`reason` 字段：`helpful` 可省；`misleading` / `outdated` **必须**带具体原因（"哪里误导了"、"哪个 API 改了"），写不出具体原因就不要投——没有 reason 的负信号无法审计也无法驱动 update。

**判定"被采用"的三种信号（满足其一即算用过，交付前就能自判）：**
- 回答里引用了该条的结论
- 按它的 procedure 执行了
- 用它的 Why 解释了决策

**结果反推信号（事后补）：**
- 用户说"不对"/"那改了" → 检查刚用过的知识，对应 `misleading` / `outdated`
- 按搜索结果操作后一次过 → `helpful`
- 用户否定的是你的推理而非知识本身 → 知识仍 `helpful`，不要一被否定就全打 `misleading`

---

## 4. 可观测性

用 mnemo 时在回复里简短提一句：
- "查了知识库，找到 3 条相关经验" ✓
- "知识库里没相关记录，按通用方案处理" ✓
- "那条记为 outdated 了，API 已经改" ✓

不要长篇解释 mnemo 内部机制（rerank / write_gate / verification_mult 这些词不出现在给用户的回复里）。

---

## 5. 禁止项

- 不要每句话都查知识库
- 不要把用户的临时想法当知识写入
- 不要忽略 `write_gate` 的 `supersede` / `review` 建议
- 不要用户明示"不用查"时强行查
- 不要把敏感信息（密钥、凭证、个人数据）写入 mnemo
- 不要用 `delete_knowledge` 代替 `archive_knowledge`（会丢失历史反馈数据）
