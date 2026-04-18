# mnemo 使用方最佳实践提示词

三段式交付：CLAUDE.md 补充段 / spawn 模板 / 全局 memory 文件。
使用方复制粘贴即可生效，不依赖 mnemo MCP instructions 本身。

---

## 【一】全局 CLAUDE.md 补充段落

> 追加到 `~/.claude/CLAUDE.md`。要短、要具体、要把第一步钉死。

```markdown
## mnemo 工作流（所有任务强制）

你有一个共享大脑 mnemo，前面的 agent 把经验留在了那里。你的所有工作围绕它展开。

- **第一步永远是 search** — 接到任何任务、bug、疑问，第一个动作就是 `mnemo__search`。不搜就回答 = 你的回答大概率是错的，前人踩过的坑你会再踩一遍。
- **任何未知优先查 mnemo** — 模块怎么用、接口什么形状、用户以前怎么说过、上次怎么决策的，先搜再问。能搜到就不要凭记忆瞎猜，也不要直接问用户。
- **能查到更多信息就先查** — 只要多一份上下文能让回答更准，就值得先 search 一次。宁可多搜一次，不要凭半个上下文回答。
- **用过就 feedback** — 用 search 结果答了题或做了事，必须 `feedback_knowledge`（helpful / misleading / outdated）。不反馈 = 这条知识永远学不会排序。
- **任务结束必须反哺** — 收工前自检：这次有没有发现非显然事实、用户有没有说什么新东西、有没有踩坑？有就 `create_knowledge` 存回去，让下个 agent 不再重走一遍。
- **只存事实，不存观点** — 代码约定、接口行为、决策及其 Why、踩坑经过、外部系统事实，才存。主观猜测、临时状态、一次性调试变量，不存。
- **用户说的话就是事实** — 用户的偏好、判断、纠正、评价，一律按事实原话记录，不要解读、不要概括、不要省略。用户判断优先级高于技术结论。**目的是让未来 agent 不再犯同样的错、回答得更准，不是为了记录本身。**
- **零 mnemo 调用 = 流程错了** — 一次任务做完没调过 mnemo，要么是你忘了 search，要么是你没反哺。两种都是问题。
```

---

## 【二】spawn prompt 模板（leader 派人时直接复制）

> leader 在每次 spawn 新 agent 的 prompt 尾部贴上这一段。与其它 `[铁律]` 并列。

```
[铁律] mnemo 工作流（无例外，所有任务都走一遍）：
1. 开始前先 mnemo__search —— 搜任务关键词、模块名、错误信息。搜不到再动手，搜到了先看再动。
2. 用了 mnemo 返回的知识就立刻 feedback_knowledge —— helpful / misleading / outdated 三选一，不反馈系统永远学不会。
3. 发现新事实就 create_knowledge —— 非显然的架构/行为/约束/踩坑，任务结束前必须写回。零写入意味着下一个 agent 要重走你这段路。
4. 用户强调的要求/偏好标记为事实记录 —— 偏好、评价、纠正、判断原话 create_knowledge 存下，用户的话覆盖技术结论。**目的是让未来 agent 回答更准、不重复犯错，不是为了记录本身。**
```

---

## 【三】全局 memory 文件

> 写到 `~/.claude/memory/mnemo_workflow.md`。auto memory 格式，frontmatter 必填。
> 同时要在 `~/.claude/memory/MEMORY.md` 索引里加一行指针。

### 文件内容：`~/.claude/memory/mnemo_workflow.md`

```markdown
---
name: mnemo 工作流四步法
description: 所有项目所有 agent 使用 mnemo 共享大脑的强制工作流 —— search 优先、用完反馈、收工反哺、用户话即事实
type: feedback
---

所有任务围绕 mnemo 展开。mnemo 是团队共享大脑，前一个 agent 的经验都在那里。

**Why:** 单 agent 会话上下文不跨 agent 传递，不用 mnemo 就意味着每个新 agent 从零起步、重复踩坑、重复问用户。mnemo 是唯一跨 agent 的记忆载体。

**How to apply:**

1. **接任何任务的第一个动作是 `mnemo__search`** —— 搜任务关键词、模块名、错误信息、用户此前的表述。上下文不完整就必须搜，不搜就回答 = 你在瞎猜。搜不到空结果是有价值的信号：你是第一人，你欠下一个 agent 一次写入。

2. **任何未知优先查 mnemo** —— 需要多一份上下文才能回答得更准时，先搜。不要凭半个上下文作答，也不要第一反应就去问用户 —— 先搜 mnemo 再说。

3. **用过就 feedback_knowledge** —— search 返回的知识真的用来做事或答题了，立刻调 feedback_knowledge：helpful（直接帮上了）/ misleading（误导了）/ outdated（过时了）。不反馈排序永远学不会。

4. **任务结束前反哺** —— 收工前自检三问：发现非显然事实了吗？用户说了新东西吗？踩坑了吗？任一为是就 `create_knowledge`。一次任务零 mnemo 调用 = 流程错了。

5. **只存事实，不存观点** —— 代码约定、接口行为、决策及 Why、踩坑、外部约定，存。主观猜测、临时状态、一次性调试值，不存。

6. **用户的话是最高优先级事实，原话记录** —— 用户的偏好、判断、纠正、评价都按事实处理，原话引用，不解读不概括不省略。用户判断压倒技术结论。用户重复说过的话优先级更高，即使已有相似条目也要记。**目的是让未来 agent 不再犯同样的错、回答得更准，不是为了记录本身。**

**触发点速查：**
- 开始任务 → search
- 用了知识 → feedback
- 发现新事实 / 用户发言 / 踩坑 → create_knowledge
- 发现旧条目错了 → feedback(misleading|outdated) + update_knowledge
- 收工前检查清单 → 有新发现就先写再回话
```

### 索引追加：`~/.claude/memory/MEMORY.md`

在文件末尾加一行：

```markdown
- [mnemo 工作流四步法](mnemo_workflow.md) — search 优先、用完反馈、收工反哺、用户话即事实
```

---

## 部署顺序建议

1. 先写全局 memory 文件（影响面最大，跨所有项目所有 agent）
2. 再补全局 CLAUDE.md 段落（作为显式指令，优先级高于 memory）
3. leader 的 spawn prompt 模板同步更新（`[铁律]` 是硬约束，覆盖率 100%）

三层叠加后，即使任一层失效（长对话压缩、新 agent 不继承、相关性过滤），另外两层仍能兜底。
