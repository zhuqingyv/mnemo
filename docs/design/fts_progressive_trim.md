# FTS5 Progressive Token Trim

## Problem

FTS5 combines jieba tokens with implicit AND.

```
Query:  "蓝牙 BLE 开发 工具"
Tokens: "蓝牙" "BLE" "开发" "工具"
FTS5:   "蓝牙" AND "BLE" AND "开发" AND "工具" → 0 results
```

No single knowledge entry contains all four tokens. One cold token kills the entire query.

## Solution: Right-to-left token trimming

When FTS5 returns zero hits, progressively drop the *rightmost* token and retry.

```
    "蓝牙" "BLE" "开发" "工具" → 0 条
    "蓝牙" "BLE" "开发"       → 0 条
    "蓝牙" "BLE"              → 10 条 ✓ → return results
```

Rationale:
- Chinese queries put core terms first, modifiers last
- Right-to-left trim drops `"开发"` `"工具"` — the least discriminative tokens — before touching `"蓝牙"` `"BLE"`
- Preserves AND semantics (precision) for the surviving tokens
- Preserves jieba segmentation (Chinese compatibility)
- No change to existing happy path (n > 0 results on first try)

### When to stop trimming

Stop at 2 tokens. If 2 tokens still return 0, fall through to the existing vector-only path (subject to vec_only_min_final gate).

### Where it fires

In `fts_search()` — `src/mnemo/repository/search_repository.py`. After `_sanitize_query()` produces a token list, loop from right to left.

### Fallback interaction

This replaces the current `search_auto_fallback_enabled` logic in `knowledge_service.py`. Instead of a single retry with a shortened query, FTS5 internally trims until it succeeds or hits the 2-token floor.

---

## MCP `search` tool description — Agent communication

The tool description must teach agents the search mechanics so they can write better queries.

### Key points agents need to know

1. **Word order matters** — core terms first, modifiers last. Trim drops from the right.
2. **More terms = higher precision, not higher recall** — extra terms will be trimmed if they cause zero hits, but the system only trims down. Too many terms might leave nothing.
3. **Try one comprehensive query** — don't pre-trim manually. Let the system trim internally.

### Proposed description update (diff)

Replace the current "没命中" section with:

```
    用完之后：
    - 命中了有用结果 → 直接回答用户，并对用到的那条调用
      feedback_knowledge(signal="helpful")，让排序学到正向信号。
    - 命中了但跟当下场景不符 / 已过时 → 对那条调用
      feedback_knowledge(signal="misleading" 或 "outdated")。
    - 摘要不够判断 → 用 get_knowledge 取完整内容。
    - 没命中 → 视任务类型决定是解决它再 create_knowledge，还是放弃搜索直接做。

    query 写法（重要）：
    - 词序有先后权重。核心词放前，修饰/泛化词放后。
      例："蓝牙 BLE 死锁 连接超时" 比 "BLE 连接" 更好 — 前面的词优先保留。
    - 系统在搜不到时会自动从右侧逐词裁剪并重试，你不必手动缩短。
    - 把能想到的相关词都写进 query，越交叉越好，系统会尽力返回最近似的匹配。
    - 一个 query 就行，不要拆成多个 search 调用来试探。
```

### Why this wording

| Phrase | Intent |
|--------|--------|
| "词序有先后权重" | Agent understands order is not neutral |
| "核心词放前" | Agent writes `"蓝牙 BLE"` not `"开发 工具 蓝牙"` |
| "自动从右侧逐词裁剪" | Agent knows trim logic, can exploit it |
| "越交叉越好" | Agent puts diverse tokens in, system prunes |
| "不要拆成多个 search 调用" | Prevents agent from running 5 parallel searches |
