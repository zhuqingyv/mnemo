# Mnemo 门禁与测试数据报告

> 生成时间：2026-05-14 | 来源：本地构建 `mnemo-darwin-arm64` 后实测

---

## 一、门禁体系

### Phase 2 回归基线

| 门禁维度 | 通过/总数 | 通过率 | 说明 |
|----------|----------|--------|------|
| **accuracy** | 392/494 | **79.4%** | 搜索准确率（预期命中） |
| **top3** | 396/440 | **90.0%** | Top-3 召回率 |
| **negative** | 9/10 | **90.0%** | 负向过滤（不应命中） |
| **intelligence** | 17/19 | **89.5%** | 智能关联 |
| **eval_e2e** | 16/16 | **100.0%** | 端到端评估 |

门禁规则：单项 baseline-pass 场景回退即 FAIL，整体下降阈值 0.5pp。

### 门禁脚本

- `scripts/phase3_regression_gate.py` — 提交后回归门禁
- `scripts/freeze_phase2_baseline.py` — 冻结 baseline

### 门禁知识条目

delivery_rules 中共 32 条门禁相关规则，包括：
- "write-gate 写入门禁" — 提交前硬门禁
- "交付前必过 delivery-gate"
- "有证据才能说完成"

---

## 二、向量 / 嵌入模型

- **嵌入维度**：1024
- **测试用 Stub**：`StubEmbedding`（确定性伪造向量，绕过外部依赖）
- **生产模型**：Ollama `qwen3-embedding:0.6b`（仅 integration 测试启用）
- **存储引擎**：SQLite `vec0` 虚拟表 + KNN 查询

---

## 三、测试数据规模

| 类型 | 记录数 | 文件大小 |
|------|--------|----------|
| 知识条目 | **733** | ~630 KB |
| 搜索场景 | **494** | ~170 KB |
| 门禁基线 | 1 | 542 KB |
| Guide 知识 | 41 | ~68 KB |

### 知识条目分类明细

| 类别 | 记录数 | 说明 |
|------|--------|------|
| api_specs | 107 | API 规范 |
| architecture_decisions | 95 | 架构决策 |
| status_snapshots | 89 | 状态快照 |
| code_reviews | 82 | 代码审查 |
| tech_surveys | 69 | 技术调研 |
| commands | 61 | 命令模板 |
| test_cases | 61 | 测试用例 |
| team_rules | 51 | 团队规则 |
| pitfalls | 33 | 踩坑复盘 |
| delivery_rules | 32 | 交付门禁 |
| user_preferences | 28 | 用户偏好 |
| env_constraints | 25 | 环境约束 |

---

## 四、技术架构

- **数据库**：SQLite + FTS5（全文索引）+ vec0（向量索引）
- **二元搜索**：关键词 + 语义向量融合排序（hybrid mode）
- **知识图谱**：relation 表 + 权威度评分
- **测试框架**：pytest，DB 均为运行时临时创建，无持久化依赖
