# Mnemo 内置 LLM 使用指南设计

## 多模型协作流水线

```
用户消息
    │
    ▼
┌─────────────────┐
│ 提取器 (1.5B)    │  "提取搜索关键词，只输出关键词，逗号分隔"
│ 关键词: 用完即丢  │  输入: 用户原始消息
│ 不持久化          │  输出: keyword1, keyword2, keyword3
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Mnemo 搜索系统   │  search(query=关键词, limit=3)
│ FTS5+向量+图谱   │  从 knowledge 表检索
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 响应器 (1.5B)    │  "根据搜索知识和用户问题生成回答"
│ 上下文 ≤ 4K-8K   │  输入: 用户问题 + 搜索到的知识内容
└────────┬────────┘
         │
         ▼
       用户看到回答
```

## 关键约束

| 约束 | 值 | 影响 |
|------|-----|------|
| LLM 参数 | 1.5B | 推理能力有限，需要明确指令 |
| 上下文窗口 | ~4K-8K tokens | 每条知识+用户问题+system prompt 必须在此范围内 |
| 对话能力 | 无状态/单轮 | 每次查询独立，不依赖之前对话 |

## 知识条目设计原则

1. **一条知识 = 一个答案**: 每条独立回答一个完整问题，≤500 字中文
2. **关键词丰富**: title + tags + summary 覆盖中英文同义词和场景词
3. **wikilink + auto_related 双类边**: 精确引用 + 向量相似度自动关联

## 知识条目清单（25条，ID 1267-1291）

### A. Identity 身份声明 (1)
| ID | 标题 |
|----|------|
| 1267 | Mnemo 使用指南助手 — 身份声明 |

### B. Concepts 核心概念 (5)
| ID | 标题 |
|----|------|
| 1268 | Mnemo 是什么 — AI agent 共享大脑 |
| 1269 | MCP 注入是什么 — 给 agent 装上记忆工具 |
| 1270 | 全局提示词注入 — 教会 agent 什么时候用记忆 |
| 1271 | Mnemo 的数据隐私和安全 — 数据完全在本地 |
| 1272 | Public Memory 和 Private Memory — scope 三级隔离 |

### C. Install 安装 (5)
| ID | 标题 |
|----|------|
| 1273 | macOS 安装 Mnemo — 一条命令搞定 |
| 1274 | Windows 安装 Mnemo — PowerShell 一键安装 |
| 1275 | Linux 安装 Mnemo — 一条 curl 命令 |
| 1276 | 如何验证 Mnemo 安装成功 — 三步检查 |
| 1277 | 手动安装 Mnemo — 自动脚本失败时的恢复方案 |

### D. Client Setup 客户端接入 (6)
| ID | 标题 |
|----|------|
| 1278 | Claude Code 接入 Mnemo — 三步配置 |
| 1279 | Cursor 接入 Mnemo — 三步配置 |
| 1280 | CodeBuddy 接入 Mnemo — 三步配置 |
| 1281 | Windsurf 接入 Mnemo — 三步配置 |
| 1282 | Gemini CLI 接入 Mnemo — 三步配置 |
| 1283 | GitHub Copilot 接入 Mnemo — 三步配置 |

### E. Troubleshooting 问题排查 (8)
| ID | 标题 |
|----|------|
| 1284 | 安装脚本执行失败 — 排查清单 |
| 1285 | MCP 工具没有出现 — 排查清单 |
| 1286 | Agent 没有读取记忆 — 排查清单 |
| 1287 | 配置写入了但不生效 — 排查清单 |
| 1288 | 全局提示词没生效 — 排查清单 |
| 1289 | 客户端日志排查 — 看懂 MCP 错误信息 |
| 1290 | 多客户端同时使用 Mnemo — 注意事项 |
| 1291 | Mnemo 升级方法 — 重新运行安装脚本 |

## 提取器 Prompt

```
你是一个关键词提取器。从用户消息中提取用于搜索的查询关键词。

规则：
- 提取 3-8 个关键词，用逗号分隔
- 同时提取中文和英文关键词
- 提取名词和动词，忽略语气词
- 原始用户消息可能混合中英文，提取时保持原样

用户消息: {user_message}
关键词:
```

## 响应器 Prompt

```
你是 Mnemo 的使用指南助手。根据搜索到的知识回答用户问题。

约束：
- 用中文回答，简洁直接
- 如果搜索知识包含了用户问题的答案，直接引用
- 如果搜索知识不相关，如实告知并建议用户到 GitHub issue 求助
- 不要编造知识中不存在的内容
- 不要执行命令或打开链接

用户问题: {user_question}

搜索到的知识:
{knowledge_content}

回答:
```

## 老系统清理

以下死代码将被移除，全部替换为 AI 流水线：

- `data/guide_knowledge/*.json` — 转前端建议输入模板
- `src/mnemo/guide/knowledge_pack.py` — 删除
- `src/mnemo/guide/router.py` — 删除
- `src/mnemo/guide/install_templates.py` — 删除
- `src/mnemo/guide/types.py` — KnowledgeCard 删除，保留 GuideResponse
