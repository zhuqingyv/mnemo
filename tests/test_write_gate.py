"""Phase 3 Write-gate 预写测试（功能未落地，全部 ``@pytest.mark.phase3`` 跳过）。

设计依据：docs/phase3/tech_research.md §3（五层信号 L0-L4 + recommended_action
+ 返回结构 + 性能预算 + feature flag）。

约束复盘：
- 项目红线"不 mock 测试"：全部用真实 SQLite（in-memory 或 tmp_path）+ StubEmbedding
  (deterministic，非 unittest.mock)。
- 现状 ``create_knowledge`` 已有 ``duplicate_warning`` 字段（content_hash 碰撞），
  Phase 3 会把它扩展为 ``write_gate`` 结构。测试按 Phase 3 形状断言，以便功能
  实现后直接去掉 skip 启用。
- 测试跳过策略：模块级 ``pytest.skip(allow_module_level=True)`` 会让 ruff/语法检查
  仍过；个别函数级 skip 依靠 ``@pytest.mark.phase3`` + 启动前 reason 统一。采用
  函数级以便未来逐条恢复。
- 跳过 reason 统一为 ``"Phase 3 Write-gate 未实现（tech_research.md §3）"``，
  CI 过滤用 ``-m 'not phase3'``。

功能对齐后打开方式：
- 实现 ``services/write_gate_service.py`` + ``create_knowledge`` 返回 ``write_gate``
- 去掉各 ``@pytest.mark.phase3`` 或运行 ``pytest -m phase3`` 验证。
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from mnemo.config import MnemoConfig
from mnemo.services.knowledge_service import KnowledgeService
from tests.test_vector_search import EMBEDDING_DIM, StubEmbedding, _build_engine


PHASE3_REASON = "Phase 3 Write-gate 未实现（tech_research.md §3）"


# ---------------------------------------------------------------------------
# Fixtures — 真实 SQLite + vec0 + FTS5 + StubEmbedding
# ---------------------------------------------------------------------------


def _one_hot(idx: int) -> list[float]:
    v = [0.0] * EMBEDDING_DIM
    v[idx] = 1.0
    return v


@pytest_asyncio.fixture
async def svc_with_embedding(tmp_path: Path) -> AsyncIterator[KnowledgeService]:
    """默认 fixture：带确定性向量的 service，每条 title 一个正交 one-hot。

    L2 语义相似度测试依赖"能控制任意两条的 cos 距离"，one-hot 映射最方便：
    相同向量 → cos=1.0；正交 → cos=0.0；混合 → 可计算预期值。
    """
    mapping: dict[str, list[float]] = {}

    class _MappingStub(StubEmbedding):
        def __init__(self) -> None:
            super().__init__(mapping=mapping, default_vec=_one_hot(0))

    db_path = tmp_path / "write_gate.db"
    engine = await _build_engine(db_path)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    svc = KnowledgeService(session_factory=factory, embedding_service=_MappingStub())
    # 以属性挂上 mapping 方便每个测试注入 title → vector
    svc._test_vec_mapping = mapping  # type: ignore[attr-defined]
    try:
        yield svc
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def svc_flag_off(tmp_path: Path) -> AsyncIterator[KnowledgeService]:
    """feature flag off 情景：``write_gate_enabled=False`` 走 Phase 2 路径。

    Phase 3 会新增 ``MnemoConfig.write_gate_enabled`` 字段；当前 MnemoConfig 还
    没有该字段，所以测试实际启动时会被 phase3 marker 跳过（ConfigError 也无从
    谈起）。功能落地时此 fixture 直接复用。
    """
    db_path = tmp_path / "write_gate_off.db"
    engine = await _build_engine(db_path)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # 未来 MnemoConfig 会接受 write_gate_enabled=False；此处用环境变量或 kwargs
    config = MnemoConfig()
    # 通过属性赋值方式，让功能实现方自行选定注入形式（pydantic 或 config flag
    # 都可以兼容）
    object.__setattr__(config, "write_gate_enabled", False)
    svc = KnowledgeService(
        session_factory=factory,
        embedding_service=StubEmbedding(),
        config=config,
    )
    try:
        yield svc
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# 辅助断言
# ---------------------------------------------------------------------------


def _assert_write_gate_shape(result: dict[str, Any]) -> None:
    """Write-gate 返回结构完整性（tech_research.md §3.3）。"""
    assert "write_gate" in result, "create_knowledge 返回应含 write_gate 字段"
    wg = result["write_gate"]
    expected_keys = {
        "exact_duplicate",
        "title_similar",
        "semantic_similar",
        "evidence_weak",
        "potential_contradiction",
        "recommended_action",
    }
    assert expected_keys <= set(wg.keys()), (
        f"write_gate 缺少字段：{expected_keys - set(wg.keys())}"
    )
    # 类型契约
    assert wg["exact_duplicate"] is None or isinstance(wg["exact_duplicate"], dict)
    assert isinstance(wg["title_similar"], list)
    assert isinstance(wg["semantic_similar"], list)
    assert wg["evidence_weak"] is None or isinstance(wg["evidence_weak"], dict)
    assert isinstance(wg["potential_contradiction"], list)
    assert wg["recommended_action"] in {"create", "supersede", "review"}


# ---------------------------------------------------------------------------
# L0：content_hash 精确碰撞
# ---------------------------------------------------------------------------


@pytest.mark.phase3
async def test_l0_exact_duplicate_positive(svc_with_embedding: KnowledgeService) -> None:
    """相同 content 不同 title：L0 必须命中 exact_duplicate 候选。"""
    svc = svc_with_embedding
    content = "SQLite 的 FTS5 是一个全文检索虚拟表，适用于中文分词。" * 3
    first = await svc.create_knowledge(
        title="FTS5 基础", summary="x", content=content
    )
    second = await svc.create_knowledge(
        title="FTS5 同内容不同标题", summary="y", content=content
    )
    _assert_write_gate_shape(second)
    dup = second["write_gate"]["exact_duplicate"]
    assert dup is not None, "相同 content_hash 必须命中 L0"
    assert dup["id"] == first["id"]
    assert dup["title"] == first["title"]


@pytest.mark.phase3
async def test_l0_exact_duplicate_negative(svc_with_embedding: KnowledgeService) -> None:
    """完全不同的内容：L0 不得误报。"""
    svc = svc_with_embedding
    await svc.create_knowledge(
        title="A", summary="sa", content="apple banana cherry " * 5
    )
    result = await svc.create_knowledge(
        title="B", summary="sb", content="zebra yak xylophone " * 5
    )
    _assert_write_gate_shape(result)
    assert result["write_gate"]["exact_duplicate"] is None


# ---------------------------------------------------------------------------
# L1：title 相似度（Levenshtein ≥ 0.85 OR jieba Jaccard ≥ 0.7）
# ---------------------------------------------------------------------------


@pytest.mark.phase3
async def test_l1_title_levenshtein_hit(svc_with_embedding: KnowledgeService) -> None:
    """title 仅差 1 个字符：Levenshtein normalized ≥ 0.85，L1 命中。"""
    svc = svc_with_embedding
    base = await svc.create_knowledge(
        title="SQLite FTS5 基础教程", summary="x", content="body 1 " * 20
    )
    # "SQLite FTS5 基础教学" vs "SQLite FTS5 基础教程"：仅末字不同，11 字共有 10
    # → 1 - (1/11) ≈ 0.909 ≥ 0.85
    result = await svc.create_knowledge(
        title="SQLite FTS5 基础教学", summary="y", content="body 2 " * 20
    )
    _assert_write_gate_shape(result)
    hits = result["write_gate"]["title_similar"]
    assert any(h["id"] == base["id"] for h in hits), (
        "近似标题应进 title_similar 候选"
    )
    # 每项结构契约
    for h in hits:
        assert {"id", "title", "score"} <= set(h.keys())
        assert 0.0 <= h["score"] <= 1.0


@pytest.mark.phase3
async def test_l1_title_jaccard_hit(svc_with_embedding: KnowledgeService) -> None:
    """词重排但词集基本相同：Levenshtein 低，jieba Jaccard ≥ 0.7 仍命中。

    "Python 异步 编程 入门" vs "入门 Python 异步 编程"：词集完全相同但字符串
    编辑距离大；jieba 分词 Jaccard = 4/4 = 1.0 ≥ 0.7。
    """
    svc = svc_with_embedding
    base = await svc.create_knowledge(
        title="Python 异步编程入门指南", summary="x", content="a " * 30
    )
    result = await svc.create_knowledge(
        title="入门指南：Python 异步编程", summary="y", content="b " * 30
    )
    _assert_write_gate_shape(result)
    hits = result["write_gate"]["title_similar"]
    assert any(h["id"] == base["id"] for h in hits), (
        "词集重合的标题应通过 Jaccard 通道命中"
    )


@pytest.mark.phase3
async def test_l1_title_dissimilar_no_hit(svc_with_embedding: KnowledgeService) -> None:
    """完全不相关的 title：L1 必须空。"""
    svc = svc_with_embedding
    await svc.create_knowledge(title="如何泡咖啡", summary="x", content="水温 " * 30)
    result = await svc.create_knowledge(
        title="Kubernetes 集群调度", summary="y", content="节点 " * 30
    )
    _assert_write_gate_shape(result)
    # 允许结果为空，严格禁止包含不相关的旧条目
    assert result["write_gate"]["title_similar"] == [] or all(
        h["score"] < 0.7 for h in result["write_gate"]["title_similar"]
    )


# ---------------------------------------------------------------------------
# L2：语义相似度（cosine ≥ 0.92）
# ---------------------------------------------------------------------------


@pytest.mark.phase3
async def test_l2_semantic_hit(svc_with_embedding: KnowledgeService) -> None:
    """向量完全相同（cos=1.0）：L2 必须命中 semantic_similar。"""
    svc = svc_with_embedding
    # 两个 title 共享相同 one-hot 向量
    svc._test_vec_mapping["语义 A"] = _one_hot(3)  # type: ignore[attr-defined]
    svc._test_vec_mapping["语义 B"] = _one_hot(3)  # type: ignore[attr-defined]

    a = await svc.create_knowledge(title="语义 A", summary="x", content="c1 " * 30)
    result = await svc.create_knowledge(
        title="语义 B", summary="y", content="c2 " * 30
    )
    _assert_write_gate_shape(result)
    sims = result["write_gate"]["semantic_similar"]
    assert any(s["id"] == a["id"] for s in sims), "cos=1.0 必须命中 L2"
    for s in sims:
        assert {"id", "title", "cosine"} <= set(s.keys())
        assert 0.92 <= s["cosine"] <= 1.0 + 1e-6


@pytest.mark.phase3
async def test_l2_semantic_miss_below_threshold(
    svc_with_embedding: KnowledgeService,
) -> None:
    """正交向量（cos=0.0）：L2 不得误报。"""
    svc = svc_with_embedding
    svc._test_vec_mapping["正交 A"] = _one_hot(1)  # type: ignore[attr-defined]
    svc._test_vec_mapping["正交 B"] = _one_hot(2)  # type: ignore[attr-defined]

    await svc.create_knowledge(title="正交 A", summary="x", content="a " * 30)
    result = await svc.create_knowledge(
        title="正交 B", summary="y", content="b " * 30
    )
    _assert_write_gate_shape(result)
    assert result["write_gate"]["semantic_similar"] == []


# ---------------------------------------------------------------------------
# L3：最小证据检查
# ---------------------------------------------------------------------------


@pytest.mark.phase3
async def test_l3_evidence_too_short(svc_with_embedding: KnowledgeService) -> None:
    """content < 50 字符：L3 必须报 evidence_weak。"""
    svc = svc_with_embedding
    result = await svc.create_knowledge(
        title="短内容", summary="s", content="太短了"
    )
    _assert_write_gate_shape(result)
    weak = result["write_gate"]["evidence_weak"]
    assert weak is not None and "content" in weak.get("reason", "")


@pytest.mark.phase3
async def test_l3_fact_without_source(svc_with_embedding: KnowledgeService) -> None:
    """claim_type=fact 但 source 为空：L3 必须报 no_source_for_fact。"""
    svc = svc_with_embedding
    result = await svc.create_knowledge(
        title="事实条目",
        summary="s",
        content="这是一条足够长的事实类声明，描述了某个具体的测量结果。" * 2,
        claim_type="fact",
        source=None,
    )
    _assert_write_gate_shape(result)
    weak = result["write_gate"]["evidence_weak"]
    assert weak is not None
    assert "source" in weak.get("reason", "") or "fact" in weak.get("reason", "")


@pytest.mark.phase3
async def test_l3_evidence_ok(svc_with_embedding: KnowledgeService) -> None:
    """内容充分、fact 带 source：L3 不得报 weak。"""
    svc = svc_with_embedding
    result = await svc.create_knowledge(
        title="完备条目",
        summary="s",
        content="这是一条内容充足的说明，包含多个具体的场景描述和上下文。" * 3,
        claim_type="fact",
        source="https://example.org/paper",
    )
    _assert_write_gate_shape(result)
    assert result["write_gate"]["evidence_weak"] is None


# ---------------------------------------------------------------------------
# L4：潜在矛盾（语义近 + 否定极性翻转）
# ---------------------------------------------------------------------------


@pytest.mark.phase3
async def test_l4_negation_polarity_flip(svc_with_embedding: KnowledgeService) -> None:
    """W1 — 语义近（cos=1.0）+ 一条否定、一条肯定：L4 必须进 potential_contradiction。

    M5 测试设计方案 V3 §3.1 W1：解除现有 skip。断言每项 ``reason`` 包含极性词
    描述（"polarity" 英文或 "否定" 中文），证明启发式识别出极性翻转信号。
    """
    svc = svc_with_embedding
    # 默认 ``contradiction_pair_enabled=False``，需要切到 on 才跑 L4。
    object.__setattr__(svc._config, "contradiction_pair_enabled", True)
    svc._test_vec_mapping["SQLite 应该用 WAL"] = _one_hot(7)  # type: ignore[attr-defined]
    svc._test_vec_mapping["SQLite 不应该用 WAL"] = _one_hot(7)  # type: ignore[attr-defined]

    base = await svc.create_knowledge(
        title="SQLite 应该用 WAL",
        summary="s",
        content="在多读少写场景下 SQLite 应该启用 WAL 模式以提升并发读性能。" * 2,
    )
    result = await svc.create_knowledge(
        title="SQLite 不应该用 WAL",
        summary="s",
        content="在写密集且单进程场景下 SQLite 不应该启用 WAL，它会增加 IO 开销。" * 2,
    )
    _assert_write_gate_shape(result)
    conflicts = result["write_gate"]["potential_contradiction"]
    assert any(c["id"] == base["id"] for c in conflicts), (
        "语义近 + 否定极性翻转必须命中 L4"
    )
    for c in conflicts:
        assert {"id", "title", "reason"} <= set(c.keys())
        reason = c["reason"]
        # 允许英文 "polarity" / "negation" 或中文 "否定" / "极性"
        assert (
            "polarity" in reason
            or "negation" in reason
            or "否定" in reason
            or "极性" in reason
        ), f"reason 应描述极性翻转信号：{reason!r}"


@pytest.mark.phase3
async def test_l4_no_polarity_flip_no_hit(
    svc_with_embedding: KnowledgeService,
) -> None:
    """W2 — 语义近但两条都是肯定陈述（无极性翻转）：L4 不得误报。

    M5 测试设计方案 V3 §3.1 W2：解除现有 skip。允许 ``semantic_similar`` 命中
    （L2 正常工作），但 L4 必须留空。
    """
    svc = svc_with_embedding
    object.__setattr__(svc._config, "contradiction_pair_enabled", True)
    svc._test_vec_mapping["SQLite 支持 WAL"] = _one_hot(8)  # type: ignore[attr-defined]
    svc._test_vec_mapping["SQLite 可以启用 WAL"] = _one_hot(8)  # type: ignore[attr-defined]

    await svc.create_knowledge(
        title="SQLite 支持 WAL",
        summary="s",
        content="SQLite 支持 WAL 模式来改善并发。" * 3,
    )
    result = await svc.create_knowledge(
        title="SQLite 可以启用 WAL",
        summary="s",
        content="SQLite 可以启用 WAL 模式获得更好的读写并发体验。" * 3,
    )
    _assert_write_gate_shape(result)
    # 可能进 semantic_similar（正常），但不得进 potential_contradiction
    assert result["write_gate"]["potential_contradiction"] == []


@pytest.mark.phase3
async def test_l4_below_semantic_threshold_no_hit(
    svc_with_embedding: KnowledgeService,
) -> None:
    """W3 — 都含否定极性词但语义距离远（正交向量）：L4 不得命中。

    M5 测试设计方案 V3 §3.1 W3（新增边界）：验证 L4 的 gate 条件"语义近 + 极性
    翻转"两者必须同时成立，不能只看极性词。tech_research §5.3 明确 cos ≥ 0.85
    才进 L4 候选。
    """
    svc = svc_with_embedding
    object.__setattr__(svc._config, "contradiction_pair_enabled", True)
    # 两条都含否定词，但向量正交（cos=0），L4 的 top-3 候选筛出这条
    svc._test_vec_mapping["A 不该启用 WAL"] = _one_hot(20)  # type: ignore[attr-defined]
    svc._test_vec_mapping["B 不要用 Postgres"] = _one_hot(21)  # type: ignore[attr-defined]

    await svc.create_knowledge(
        title="A 不该启用 WAL",
        summary="s",
        content="场景 A 下 SQLite 不应该启用 WAL，会有写竞争。" * 3,
    )
    result = await svc.create_knowledge(
        title="B 不要用 Postgres",
        summary="s",
        content="场景 B 下 Postgres 不要用作主存储，部署成本过高。" * 3,
    )
    _assert_write_gate_shape(result)
    assert result["write_gate"]["potential_contradiction"] == []


@pytest.mark.phase3
async def test_l4_flag_off_skips_heuristic(
    svc_with_embedding: KnowledgeService,
) -> None:
    """W4 — flag OFF 下构造 W1 同样的极性翻转条件：L4 必须留空。

    M5 测试设计方案 V3 §3.1 W4（新增 flag 守卫）：TECH_PLAN §6.4 明确 flag OFF
    时不跑启发式；其他 L0-L3 字段仍正常返回（flag 是子开关而非全局开关）。
    """
    svc = svc_with_embedding
    # 默认已经是 False，显式断言一下保证清晰
    object.__setattr__(svc._config, "contradiction_pair_enabled", False)
    svc._test_vec_mapping["W4 应该用 WAL"] = _one_hot(22)  # type: ignore[attr-defined]
    svc._test_vec_mapping["W4 不应该用 WAL"] = _one_hot(22)  # type: ignore[attr-defined]

    await svc.create_knowledge(
        title="W4 应该用 WAL",
        summary="s",
        content="在多读场景下 W4 应该启用 WAL 模式以提升并发读性能。" * 2,
    )
    result = await svc.create_knowledge(
        title="W4 不应该用 WAL",
        summary="s",
        content="在写密集场景下 W4 不应该启用 WAL，它会增加 IO 开销。" * 2,
    )
    _assert_write_gate_shape(result)
    # L4 跳过启发式，potential_contradiction 必须为空
    assert result["write_gate"]["potential_contradiction"] == []
    # L2 仍正常工作（cos=1.0 → 进 semantic_similar），验证 flag 只关 L4 子路径
    sims = result["write_gate"]["semantic_similar"]
    assert len(sims) >= 1, "flag OFF 不应影响 L2 语义相似度通道"


# ---------------------------------------------------------------------------
# recommended_action 正确性
# ---------------------------------------------------------------------------


@pytest.mark.phase3
async def test_recommended_action_create_clean(
    svc_with_embedding: KnowledgeService,
) -> None:
    """全绿（无任何层命中）：recommended_action=create。"""
    svc = svc_with_embedding
    result = await svc.create_knowledge(
        title="独立唯一标题 alpha",
        summary="s",
        content="完全独立且充分的内容描述，包含具体场景示例。" * 3,
    )
    _assert_write_gate_shape(result)
    assert result["write_gate"]["recommended_action"] == "create"


@pytest.mark.phase3
async def test_recommended_action_supersede_on_exact_dup(
    svc_with_embedding: KnowledgeService,
) -> None:
    """L0 命中（精确重复）且 title 近似：推荐 supersede（版本链升级）。

    注意：当前 ``create_knowledge`` 对"同 title + 同 scope"自动走 supersede 分支
    （见 services/knowledge_service.py:319）；Phase 3 要求 write_gate 即使在
    supersede 发生时也返回 recommended_action 值反映动作本身。
    """
    svc = svc_with_embedding
    content = "相同 content + 近似 title 的场景 " * 5
    await svc.create_knowledge(title="重复候选 A", summary="x", content=content)
    # title 不完全相同（避免自动 supersede 路径），仅 L0 命中
    result = await svc.create_knowledge(
        title="重复候选 A'", summary="y", content=content
    )
    _assert_write_gate_shape(result)
    assert result["write_gate"]["recommended_action"] in {"supersede", "review"}
    # 关键：L0 必须命中作为推荐 supersede 的依据
    assert result["write_gate"]["exact_duplicate"] is not None


@pytest.mark.phase3
async def test_recommended_action_review_on_contradiction(
    svc_with_embedding: KnowledgeService,
) -> None:
    """L4 命中（潜在矛盾）：推荐 review。

    M5 测试设计方案 V3 §3.1 说明：此用例随 W1/W2 一同解 skip（不新增编号，沿
    用原名）。
    """
    svc = svc_with_embedding
    object.__setattr__(svc._config, "contradiction_pair_enabled", True)
    svc._test_vec_mapping["建议使用 TLS 1.3"] = _one_hot(11)  # type: ignore[attr-defined]
    svc._test_vec_mapping["禁止使用 TLS 1.3"] = _one_hot(11)  # type: ignore[attr-defined]

    await svc.create_knowledge(
        title="建议使用 TLS 1.3",
        summary="s",
        content="生产环境应该启用 TLS 1.3 获得更好的安全性和性能。" * 3,
    )
    result = await svc.create_knowledge(
        title="禁止使用 TLS 1.3",
        summary="s",
        content="由于兼容性问题生产环境禁止启用 TLS 1.3，必须降级到 1.2。" * 3,
    )
    _assert_write_gate_shape(result)
    assert result["write_gate"]["recommended_action"] == "review"


# ---------------------------------------------------------------------------
# 返回结构完整性（独立用例，即使全绿也要结构齐全）
# ---------------------------------------------------------------------------


@pytest.mark.phase3
async def test_write_gate_shape_on_clean_insert(
    svc_with_embedding: KnowledgeService,
) -> None:
    """空库插入：write_gate 必须存在且所有字段按契约赋值（空列表/None）。"""
    svc = svc_with_embedding
    result = await svc.create_knowledge(
        title="清洁条目",
        summary="s",
        content="充分独立的内容描述，不会命中任何层。" * 4,
    )
    _assert_write_gate_shape(result)
    wg = result["write_gate"]
    assert wg["exact_duplicate"] is None
    assert wg["title_similar"] == []
    assert wg["semantic_similar"] == []
    assert wg["evidence_weak"] is None
    assert wg["potential_contradiction"] == []
    assert wg["recommended_action"] == "create"


# ---------------------------------------------------------------------------
# 性能约束：全链路 ≤ 100ms
# ---------------------------------------------------------------------------


@pytest.mark.phase3
async def test_write_gate_latency_under_100ms(
    svc_with_embedding: KnowledgeService,
) -> None:
    """tech_research.md §3.6：全链路 ≤ 100ms（小库）。

    预填 100 条数据后测新增耗时。不 mock embedding，用 StubEmbedding（本地计算，
    延迟忽略不计）。若该测试在慢 CI 上偶尔飘，应改为 warm-up 后取中位数。
    """
    svc = svc_with_embedding
    for i in range(100):
        svc._test_vec_mapping[f"bg-{i}"] = _one_hot(  # type: ignore[attr-defined]
            100 + (i % (EMBEDDING_DIM - 100))
        )
        await svc.create_knowledge(
            title=f"bg-{i}",
            summary="s",
            content=f"背景条目编号 {i} 提供 L1/L2 检索所需的背景噪声。" * 2,
        )

    svc._test_vec_mapping["perf target"] = _one_hot(50)  # type: ignore[attr-defined]
    t0 = time.perf_counter()
    result = await svc.create_knowledge(
        title="perf target",
        summary="s",
        content="性能验证条目，应该在 100ms 内完成 write-gate 全链路。" * 3,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    _assert_write_gate_shape(result)
    assert elapsed_ms <= 100, f"write-gate 全链路超预算：{elapsed_ms:.1f}ms > 100ms"


# ---------------------------------------------------------------------------
# Feature flag：on / off 各覆盖
# ---------------------------------------------------------------------------


@pytest.mark.phase3
async def test_flag_on_returns_write_gate(
    svc_with_embedding: KnowledgeService,
) -> None:
    """默认 flag on（tech_research.md §11 默认 True）：返回含 write_gate。"""
    svc = svc_with_embedding
    result = await svc.create_knowledge(
        title="flag on 测试",
        summary="s",
        content="验证 flag 打开时 write_gate 字段存在。" * 3,
    )
    assert "write_gate" in result


@pytest.mark.phase3
async def test_flag_off_skips_write_gate(svc_flag_off: KnowledgeService) -> None:
    """flag off：走 Phase 2 路径，不得返回 write_gate；旧 duplicate_warning
    字段保留语义（如果 content_hash 碰撞则存在）。"""
    svc = svc_flag_off
    content = "flag off 场景的内容，需要保留 Phase 2 行为。" * 4
    await svc.create_knowledge(title="phase2-A", summary="s", content=content)
    result = await svc.create_knowledge(title="phase2-B", summary="s", content=content)
    assert "write_gate" not in result, (
        "flag off 时必须走 Phase 2 路径，不得注入 write_gate"
    )
    # Phase 2 原行为：content_hash 碰撞写入 duplicate_warning
    assert "duplicate_warning" in result
    assert result["duplicate_warning"]["title"] == "phase2-A"
