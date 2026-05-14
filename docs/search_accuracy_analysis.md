# Mnemo 搜索准确性分析

基于对 `/src` 源码的完整审查。分析范围：检索管道、排名算法、写入门控、反馈回路。

---

## 一、搜索架构总览

```
Query → [FTS5 (BM25) + Vector (KNN)] → RRF 融合 → 质量重排 → 结果
          ↑ jieba 分词          ↑ qwen3-embedding:0.6b
```

四层递进管道，每层有自己的准确性和防退化机制。

---

## 二、检索层 — 关键词 + 语义双保险

### 2.1 FTS5 全文搜索

**实现**：`repository/search_repository.py`

- 写入时 jieba `cut_for_search` 分词后插入 FTS5 虚拟表
- 查询时同样分词 → 去特殊字符 → 引号包裹 AND 组合
- BM25 排序

**准确性评价**：

| 优势 | 局限 |
|------|------|
| 精确术语、函数名、错误码等硬匹配准确 | 同义词、模糊意图无法命中 |
| jieba `cut_for_search` 细粒度切分提升中文召回 | jieba 默认词典对代码驼峰命名、技术缩写不友好 |
| 成熟的 BM25 概率检索模型 | 无任何语义理解能力 |

### 2.2 Vector 语义搜索

**实现**：`repository/vector_repository.py` + `services/embedding_service.py`

- Ollama `qwen3-embedding:0.6b`，1024维 float32
- sqlite-vec `vec0` 虚拟表，brute-force KNN
- **关键设计选择**：不用 vec0 的 L2 距离排序，在 Python 侧额外做 cosine 重打分
  - L2 对未归一化向量阈值不稳定
  - cosine [0, 2] 区间适合 threshold 截断（`vector_repository.py:135-139`）
- 默认 cosine distance ≤ 0.8 过滤
- FTS 零命中时阈值放宽到 0.55，但最终结果仍需过 `vec_only_min_final` (0.017) 门槛
- 当有 FTS 候选 ID 时跳过 vec0（FTS-prefilter 快路径，500K 规模下从 ~330ms 降到 <5ms）

**准确性评价**：

| 优势 | 局限 |
|------|------|
| cosine 重打分确保阈值截断稳定 | 0.6B 参数的 embedding 模型表达能力有限 |
| FTS-prefilter 快路径不影响准确性 | 已知局限：REL-N-06 "kubernetes 集群" 已超过 embedding 天花板 |

---

## 三、融合层 — Reciprocal Rank Fusion (k=60)

**实现**：`repository/rrf_repository.py`

```
score = Σ 1 / (60 + rank_in_channel)
```

- 两路都命中 → `source: "both"`，得分 = 两路贡献之和
- 仅 FTS 命中 → `source: "fts_only"`
- 仅向量命中 → `source: "vec_only"`

**准确性意义**：交叉命中的条目天然获得更高融合分。这过滤掉了单一信号命中的噪声，倾向于"关键词和语义都匹配"的结果。

---

## 四、重排层 — 7 维质量信号（核心差异化能力）

**实现**：`ranking/rerank.py:193-202`

```
final = rrf_score
      * authority_mult          (1.0 ~ 1.3+)
      * contradiction_penalty   (0.7x)
      * scope_penalty           (0.8x)
      * freshness_mult          (0.3 ~ 1.0)
      * stale_mult              (0.3x)
      * verification_mult       (0.7 ~ 1.3)
      * context_boost           (1.0 ~ 1.4)
```

### 4.1 Authority（权威性）

**实现**：`ranking/authority.py`

```
authority = log(1 + 2×supersedes + 1.5×refines + 1.0×derived_from)
authority_mult = 1 + 0.1 × authority
```

只统计 `supersedes`、`refines`、`derived_from` 三种入边。wikilink、related、depends_on 不参与。

**准确性意义**：被大量引用/迭代过的知识天然更可信，模型给出正向加权。

### 4.2 Contradiction（矛盾标记）

任何条目有 `contradicts` 边（入或出），整体分数乘以 **0.7**。

**准确性意义**：存在争议的知识在解决前被降权，避免将矛盾结论排在前面误导用户。

### 4.3 Scope（范围隔离）

无 scope 查询碰到 project 范围条目时乘以 **0.8**（`config.py` 注释：M4 验证中降低此值会损伤召回）。

**准确性意义**：防止跨项目污染。用户不问具体项目时不应让其他项目的内幕高排。

### 4.4 Freshness（时效衰减）

**实现**：`ranking/freshness.py`

按 claim_type 分化衰减速率：

| claim_type | λ (日衰减) | 半衰期约 |
|-----------|-----------|---------|
| fact | 0.003 | 230天 |
| decision | 0.007 | 100天 |
| procedure | 0.015 | 46天 |
| hypothesis | 0.02 | 35天 |

公式：`mult = 0.3 + 0.7 × exp(-λ × days)`，底线 0.3 不会归零。

**准确性意义**："facts stay fresh, hypotheses rot" — 工程直觉准确。配置明确可信。

### 4.5 Stale（陈旧标记）

当条目超过 `no_update_days` **且** `no_access_days`（双条件，单条件不触发），状态变为 stale，硬惩罚 **0.3x**。

| claim_type | 无更新天数 | 无访问天数 |
|-----------|-----------|-----------|
| fact | 180天 | 90天 |
| decision | 120天 | 60天 |
| procedure | 60天 | 30天 |
| hypothesis | 30天 | 14天 |

**准确性意义**：自动清理不再被查阅或维护的知识，且通过 feedback 连续三次 misleading → stale 的机制实现了"用脚投票"的降权。

### 4.6 Verification（反馈验证环）

**实现**：`repository/feedback_repository.py:244-262`

```
if helpful + misleading < 3:
    return 1.0        # 样本不足，中性先验
signal = helpful - 2.0 × misleading   # misleading 权重 2 倍
return 0.7 + 0.6 × sigmoid(signal)    # [0.7, 1.3]
```

关键特性：
- 3 样本底线避免小样本过拟合
- misleading 权重是 helpful 的 2 倍（惩罚比表扬敏感）
- sigmoid 饱和保证极值不失控
- 30 天窗口，24h 去重锁防刷
- 通过 `knowledge_event` 持久化，不丢

**准确性意义**：这是 mnemo 搜索准确性的**长期增长引擎**。随着使用次数增加，使用反馈重新加权搜索结果的能力逐步增强。无反馈积累时此乘数恒为 1.0，但不会引入负面影响。

### 4.7 Context（上下文感知）

**实现**：`config.py:task_context_boosts`，默认关闭

将 `task_context` 参数映射到 `claim_type` 加权：

| task_context | procedure | fact | decision | contradicts_edge |
|-------------|-----------|------|----------|-------------------|
| coding | 1.3x | 1.1x | - | - |
| debug | 1.2x | 1.2x | - | 1.4x |
| decision | - | 1.1x | 1.3x | - |
| onboarding | - | 1.2x | 1.1x | - |

**准确性意义**：根据不同任务场景动态调整关注点。debug 时优先展示已知矛盾（1.4x），coding 时优先展示操作步骤（1.3x）。

### 4.8 vec_only 门限（防退化最后防线）

**实现**：`ranking/rerank.py:218-221`

当所有结果都是 `vec_only`（FTS 零命中）且 top `final_score` < 0.017 时，**返回空列表**。

**准确性意义**：对完全不在知识库涵盖范围内的查询，宁可返回空结果也不返回似是而非的内容。0.017 是通过 9/10 负样本正确拒绝率调出来的（见 config.py:56-58 注释）。

---

## 五、写入质量管控（间接保障搜索准确性）

**实现**：`services/write_gate_service.py`，四层 L0-L4 门控：

| 层级 | 检查 | 阈值/机制 | 效果 |
|------|------|----------|------|
| L0 | SHA256 内容完全重复 | 精确匹配 | → supersede |
| L1 | 标题相似（Levenshtein + Jaccard） | 0.85 / 0.7 | 提示 |
| L2 | 语义相似（embedding cosine） | 0.92 | → review |
| L3 | 证据强度 | <50字符 / fact无来源 | 提示 |
| L4 | 极性冲突（否定词 vs 断言词） | 规则匹配 | → review |

**准确性意义**：搜索结果的质量上限由存储内容决定。L2 的 0.92 阈值极为严格，防止知识碎片化。L4 精确度仅 30-50%（代码自述），但作为提示级检查足够。

决策树：
```
L0 命中 → supersede
L4 命中 → review
L2 命中 → review
其余 → create
```

---

## 六、仍存在的准确性问题

### 6.1 Embedding 模型偏小

`qwen3-embedding:0.6b` 只有 6 亿参数。现代生产级 embedding 模型（如 `bge-m3`、`gte-Qwen2-7B`）通常是 3B-7B 参数。代码中有已知局限性标注：REL-N-06 "kubernetes 集群" 已超过当前模型的语义表达能力，被接受为已知上限。

### 6.2 Feedback 冷启动

无反馈积累时 `verification_mult = 1.0`，重排层损失一个重要信号。系统可用但不能自我优化。

### 6.3 jieba 词典对代码术语不友好

FTS5 分词依赖 jieba 默认词典，对 `SSR`、`CSR`、`DBIx` 等编程缩写和驼峰命名会切成乱码或单字，影响关键词召回。

### 6.4 RRF k 值固定

k=60 是文献推荐值，未根据结果数量或分布做自适应调参。极端稀疏/稠密场景下可能不是最优。

### 6.5 矛盾检测依赖显式标记

`contradiction_penalty` 仅对已建立的 `contradicts` 边生效。无人标记的矛盾不会进入系统感知范围。L4 极性检测精确度仅 30-50%，只能作为提示。

### 6.6 跨语言搜索能力弱

FTS5 和 embedding 模型设计上以中文为主，英文搜索可以工作（jieba 对英文按空格分词），但中英混合查询和多语言场景没有特殊优化。

---

## 七、总体评价

| 维度 | 评价 | 依据 |
|------|------|------|
| **基础检索** | 良好 | FTS + Vector 双路互补，兼顾精确和语义 |
| **排序质量** | 优秀 | 7 维重排远超单个 BM25/余弦排序 |
| **自我进化** | 良好 | feedback loop + streak stale + edge propagation |
| **中文支持** | 良好 | jieba cut_for_search + 中文 embedding |
| **防退化** | 优秀 | vec_only guard / scope penalty / stale penalty / write gate 四层 |
| **已知短板** | embedding 偏小(0.6B) / feedback 冷启动 / jieba 对代码术语不友好 |

**结论**：Mnemo 的搜索准确性是工程上认真设计的，核心价值在重排层的多维质量信号和写入门控。向量搜索部分受限于 0.6B 的 embedding 模型，这是当前最大的天花板，可通过升级到更大的 embedding 模型（如 `bge-m3`）获得显著提升。
