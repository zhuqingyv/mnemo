# Search Offset Pagination

## 动机

MCP `search()` 工具支持 `limit` 参数控制返回条数，但不支持翻页。agent 搜索命中 20+ 条结果时无法查看"下一页"，只能改 query 缩小范围。加 `offset` 参数让 agent 可以分页浏览完整结果集。

## 方案

`offset` 参数从 MCP 层 → `KnowledgeService.search()` → `_run_hybrid_search()` → `fts_search()` / `vector_search()` 透传，最终在结果切片处生效。默认 0，向后兼容。

## 改动面（5 个文件，~30 行）

### 1. `search_repository.py` — `fts_search()` 加 SQL OFFSET

```python
async def fts_search(..., offset: int = 0):
    sql += " ORDER BY bm25(knowledge_fts) LIMIT :limit OFFSET :offset"
    params["offset"] = offset
```

### 2. `vector_repository.py` — `vector_search()` 加 offset 切片

```python
async def vector_search(..., offset: int = 0):
    return rows_sorted[offset : offset + limit]
```

同一处也处理 `candidate_ids` 快速路径（FTS-prefilter 旁路）。

### 3. `knowledge_service.py` — `_run_hybrid_search()` + `search()` 透传 offset

```python
async def search(..., offset: int = 0):          # 新增参数
    ...
    final_hits = await _run_hybrid_search(..., offset=offset)

async def _run_hybrid_search(..., offset: int = 0):
    ...
    final_hits = reranked[offset : offset + limit]  # 原来是 [:limit]
```

`fts` 和 `vector` 模式同理，把 `offset` 透传给 `fts_search()` / `vector_search()`。

### 4. `server.py` — MCP `search()` 加 `offset` 参数

```python
async def search(query, ..., limit=20, offset=0):
```

docstring 加一行：`offset` 配合 `limit` 翻页，offset=0 是第一页，offset=20 是第二页。

### 5. 测试

- 单测：`fts_search` SQL 包含 `OFFSET :offset`
- 集成：seed 3 条，`search(limit=1, offset=1)` 返回第二条

## 不改的东西

- `search_by_tag()` 不加 offset（低频功能，用不到先不加）
- `_apply_sort_by()` 排序后切片逻辑不变（sort_by=relevance 时 offset 在 rerank 后生效，sort_by=time/feedback 时 offset 在 `sort_by()` 后生效）
- RRF `k=60` 不变（offset 只在最终切片，不走 RRF 的 k）
