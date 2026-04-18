"""M1 单测 + 集成测试：sqlite-vec 虚表、vector_repository、vector_search。

- 真实 SQLite（tmp file），**不 mock 数据库**。
- 单测用 StubEmbedding 把 EmbeddingService 打桩成确定性向量（one-hot / 构造向量），
  直接验证向量存储 + KNN 排序正确性，与 Ollama 解耦。
- 集成测试（@pytest.mark.integration）走真实 Ollama qwen3-embedding:0.6b。
  CI/快速回归用 ``-m "not integration"`` 跳过。
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import sqlite_vec
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mnemo.config import MnemoConfig
from mnemo.db import VECTOR_DIM
from mnemo.models.knowledge import Base, Knowledge, KnowledgeVec
from mnemo.repository import vector_repository as vr
from mnemo.services.embedding_service import EmbeddingService
from mnemo.services.knowledge_service import KnowledgeService


MODEL_NAME = "qwen3-embedding:0.6b"
EMBEDDING_DIM = VECTOR_DIM  # 1024


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _one_hot(idx: int, dim: int = EMBEDDING_DIM) -> list[float]:
    v = [0.0] * dim
    v[idx] = 1.0
    return v


def _unit(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


class StubEmbedding(EmbeddingService):
    """确定性向量生成器，bypass Ollama。

    传入一个 ``mapping[text] -> vec`` 或一个函数 ``fn(text) -> vec``。
    未命中时返回 ``default_vec``，默认全零（但 one-hot(0) 更稳：避免零向量 KNN 报错）。
    """

    def __init__(
        self,
        mapping: dict[str, list[float]] | None = None,
        *,
        default_vec: list[float] | None = None,
    ):
        config = MnemoConfig()
        super().__init__(config=config)
        self._mapping = mapping if mapping is not None else {}
        self._default = default_vec if default_vec is not None else _one_hot(0)

    def prepare_text(self, title: str, summary: str | None = None, content: str | None = None) -> str:
        # 用 title 作为 key，方便映射
        return title

    async def embed(self, text: str) -> list[float]:  # type: ignore[override]
        return self._mapping.get(text, self._default)

    async def embed_batch(self, texts: list[str], batch_size: int = 64):  # type: ignore[override]
        return [await self.embed(t) for t in texts]

    async def warmup(self) -> bool:  # type: ignore[override]
        self.ready = True
        return True


def _load_sqlite_vec_sync(dbapi_conn, _record) -> None:
    """同步加载 sqlite-vec（测试用 aiosqlite 的 _connection 钩子）。

    与 mnemo.db._load_sqlite_vec 一致，但直接 inline，避免跨 engine 副作用。
    """
    aiosqlite_conn = getattr(dbapi_conn, "_connection", None)
    if aiosqlite_conn is None:
        dbapi_conn.enable_load_extension(True)
        try:
            sqlite_vec.load(dbapi_conn)
        finally:
            dbapi_conn.enable_load_extension(False)
        return

    def _do_load(sync_conn):
        sync_conn.enable_load_extension(True)
        try:
            sqlite_vec.load(sync_conn)
        finally:
            sync_conn.enable_load_extension(False)

    dbapi_conn.await_(aiosqlite_conn._execute(_do_load, aiosqlite_conn._conn))


async def _build_engine(db_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    event.listen(engine.sync_engine, "connect", _load_sqlite_vec_sync)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts "
                "USING fts5(title, summary, content, tags, knowledge_id UNINDEXED)"
            )
        )
        await conn.execute(
            text(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_vec_idx "
                f"USING vec0(knowledge_id INTEGER PRIMARY KEY, embedding FLOAT[{EMBEDDING_DIM}])"
            )
        )
        await conn.execute(text("PRAGMA foreign_keys = ON"))
    return engine


# ---------------------------------------------------------------------------
# 单元测试 fixture（注入 StubEmbedding）
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def service_stub(tmp_path: Path) -> AsyncIterator[tuple[KnowledgeService, StubEmbedding, Any]]:
    db_path = tmp_path / "mnemo.db"
    engine = await _build_engine(db_path)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    stub = StubEmbedding()
    # 注意：KnowledgeService 必须显式传 embedding_service 才启用向量路径
    service = KnowledgeService(session_factory=factory, embedding_service=stub)
    try:
        yield service, stub, factory
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# 单元测试（6 条）
# ---------------------------------------------------------------------------


async def test_upsert_vector_roundtrip(service_stub) -> None:
    """1. upsert_vector 写入 + 读回（含重复 upsert 仍唯一）。"""
    service, _stub, factory = service_stub
    vec_a = _unit([0.3, 0.4, 0.5] + [0.0] * (EMBEDDING_DIM - 3))
    vec_b = _unit([0.1, 0.1, 0.1] + [0.0] * (EMBEDDING_DIM - 3))

    async with factory() as session:
        row = Knowledge(title="t1", summary="s", content="c", tags="[]")
        session.add(row)
        await session.flush()
        await vr.upsert_vector(session, row.id, MODEL_NAME, vec_a)
        await session.commit()
        kid = row.id

    async with factory() as session:
        hits = (
            await session.execute(
                select(KnowledgeVec).where(KnowledgeVec.knowledge_id == kid)
            )
        ).scalars().all()
        assert len(hits) == 1
        assert hits[0].model_name == MODEL_NAME

    # 重复 upsert：同 (kid, model) 应更新 vector 而不增加行
    async with factory() as session:
        await vr.upsert_vector(session, kid, MODEL_NAME, vec_b)
        await session.commit()

    async with factory() as session:
        hits = (
            await session.execute(
                select(KnowledgeVec).where(KnowledgeVec.knowledge_id == kid)
            )
        ).scalars().all()
        assert len(hits) == 1  # 仍为 1 条


async def test_vector_search_cosine_ranking(service_stub) -> None:
    """2. vector_search 排序：query 最接近 one_hot(0) 时 kid_a 在首位。"""
    service, _stub, factory = service_stub
    vec_a = _one_hot(0)
    vec_b = _one_hot(1)
    vec_c = _one_hot(2)

    async with factory() as session:
        for title, v in (("A", vec_a), ("B", vec_b), ("C", vec_c)):
            row = Knowledge(title=title, summary="", content="", tags="[]")
            session.add(row)
            await session.flush()
            await vr.upsert_vector(session, row.id, MODEL_NAME, v)
        await session.commit()

    async with factory() as session:
        # 偏向 0 维且第二近 1 维：排序应是 A, B, C
        # distance_threshold=2.0 禁用相关性阈值，这里只验证排序
        query = _unit([0.9, 0.3, 0.05] + [0.0] * (EMBEDDING_DIM - 3))
        hits = await vr.vector_search(session, query, limit=10, distance_threshold=2.0)
        titles = [h.title for h in hits]
        assert titles[:3] == ["A", "B", "C"]


async def test_vector_search_distance_threshold_drops_unrelated(service_stub) -> None:
    """query 与所有存储向量都正交 → 低于默认阈值 0.8 的都被过滤 → 返回空列表。

    这是防止"外星人入侵"类无关 query 从 KNN 拿回污染性 Top-K 结果的核心保护。"""
    _service, _stub, factory = service_stub
    async with factory() as session:
        for title, v in (("A", _one_hot(0)), ("B", _one_hot(1))):
            row = Knowledge(title=title, summary="", content="", tags="[]")
            session.add(row)
            await session.flush()
            await vr.upsert_vector(session, row.id, MODEL_NAME, v)
        await session.commit()

    # query 正交于 A/B（cosine_distance = 1.0 > 0.8 默认阈值）
    query = _one_hot(2)
    async with factory() as session:
        hits = await vr.vector_search(session, query, limit=5)
        assert hits == []
        # 显式放宽阈值，排序能力仍在
        hits_loose = await vr.vector_search(
            session, query, limit=5, distance_threshold=2.0
        )
        assert {h.title for h in hits_loose} == {"A", "B"}


async def test_vector_search_distance_threshold_keeps_related(service_stub) -> None:
    """与存储向量强相关的 query（cosine_distance ≤ 0.8）应被保留。"""
    _service, _stub, factory = service_stub
    async with factory() as session:
        row = Knowledge(title="A", summary="", content="", tags="[]")
        session.add(row)
        await session.flush()
        await vr.upsert_vector(session, row.id, MODEL_NAME, _one_hot(0))
        await session.commit()

    # 接近 one_hot(0)：cosine_distance 约 0.05
    query = _unit([0.95, 0.05] + [0.0] * (EMBEDDING_DIM - 2))
    async with factory() as session:
        hits = await vr.vector_search(session, query, limit=5)
        assert [h.title for h in hits] == ["A"]


async def test_vector_search_candidate_ids_bypasses_vec0(service_stub) -> None:
    """M4 task #5：传入 candidate_ids 时跳过 vec0 KNN，只在给定 id 上算 cosine。

    行为：
    - candidate_ids=[A.id] → 只返回 A（即便 B 更接近 query）
    - candidate_ids=[] → 直接空返回
    - candidate_ids=None → 走 vec0 KNN（默认行为）
    """
    _service, _stub, factory = service_stub
    async with factory() as session:
        # A=one_hot(0), B=one_hot(1); query=one_hot(1) → B 本该赢
        ids = {}
        for title, v in (("A", _one_hot(0)), ("B", _one_hot(1))):
            row = Knowledge(title=title, summary="", content="", tags="[]")
            session.add(row)
            await session.flush()
            await vr.upsert_vector(session, row.id, MODEL_NAME, v)
            ids[title] = row.id
        await session.commit()

    query = _one_hot(1)
    async with factory() as session:
        # 默认：B 排第一
        default_hits = await vr.vector_search(session, query, limit=5)
        assert default_hits[0].title == "B"

        # candidate_ids=[A.id]：强制只看 A，但 A 与 query 正交（cos_d=1.0）
        # 超过默认阈值 0.8 → 被过滤 → 空
        filtered = await vr.vector_search(
            session, query, limit=5, candidate_ids=[ids["A"]]
        )
        assert filtered == []

        # candidate_ids=[A.id] + 放宽阈值：只返回 A
        filtered_loose = await vr.vector_search(
            session,
            query,
            limit=5,
            candidate_ids=[ids["A"]],
            distance_threshold=2.0,
        )
        assert [h.title for h in filtered_loose] == ["A"]

        # candidate_ids=[] → 空
        empty = await vr.vector_search(session, query, limit=5, candidate_ids=[])
        assert empty == []


async def test_vector_search_scope_filter(service_stub) -> None:
    """3. vector_search scope 过滤。"""
    _service, _stub, factory = service_stub
    async with factory() as session:
        for title, scope, proj, v in [
            ("G1", "global", None, _one_hot(0)),
            ("G2", "global", None, _one_hot(1)),
            ("P1", "project", "x", _one_hot(2)),
        ]:
            row = Knowledge(title=title, summary="", content="", tags="[]", scope=scope, project_name=proj)
            session.add(row)
            await session.flush()
            await vr.upsert_vector(session, row.id, MODEL_NAME, v)
        await session.commit()

    query = _unit([0.33] * 3 + [0.0] * (EMBEDDING_DIM - 3))
    async with factory() as session:
        g = await vr.vector_search(session, query, scope="global", limit=10)
        p = await vr.vector_search(session, query, scope="project", project_name="x", limit=10)

    assert {h.title for h in g} == {"G1", "G2"}
    assert {h.title for h in p} == {"P1"}


async def test_rebuild_index_yields_same_results(service_stub) -> None:
    """4. rebuild_index 重建后搜索结果一致。"""
    _service, _stub, factory = service_stub
    vectors = [(f"T{i}", _one_hot(i)) for i in range(5)]
    async with factory() as session:
        for title, v in vectors:
            row = Knowledge(title=title, summary="", content="", tags="[]")
            session.add(row)
            await session.flush()
            await vr.upsert_vector(session, row.id, MODEL_NAME, v)
        await session.commit()

    query = _unit([0.8, 0.6, 0.0] + [0.0] * (EMBEDDING_DIM - 3))

    async with factory() as session:
        before = await vr.vector_search(session, query, limit=5)
    titles_before = [h.title for h in before]

    async with factory() as session:
        count = await vr.rebuild_index(session, model_name=MODEL_NAME)
        await session.commit()
        assert count == 5

    async with factory() as session:
        after = await vr.vector_search(session, query, limit=5)
    titles_after = [h.title for h in after]

    assert titles_before == titles_after
    assert titles_after[:2] == ["T0", "T1"]  # 最接近 query 的两个


async def test_vector_search_empty_index_returns_empty_list(service_stub) -> None:
    """5. 空库搜索返回空列表。"""
    _service, _stub, factory = service_stub
    query = _one_hot(0)
    async with factory() as session:
        hits = await vr.vector_search(session, query, limit=10)
    assert hits == []


async def test_delete_vector_clears_both_stores(service_stub) -> None:
    """6. delete_vector 清 KnowledgeVec + knowledge_vec_idx。"""
    _service, _stub, factory = service_stub
    async with factory() as session:
        row = Knowledge(title="D", summary="", content="", tags="[]")
        session.add(row)
        await session.flush()
        kid = row.id
        await vr.upsert_vector(session, kid, MODEL_NAME, _one_hot(0))
        await session.commit()

    async with factory() as session:
        await vr.delete_vector(session, kid)
        await session.commit()

    async with factory() as session:
        remain = (
            await session.execute(
                select(KnowledgeVec).where(KnowledgeVec.knowledge_id == kid)
            )
        ).scalars().all()
        assert remain == []
        idx_cnt = (
            await session.execute(
                text("SELECT COUNT(*) FROM knowledge_vec_idx WHERE knowledge_id = :k"),
                {"k": kid},
            )
        ).scalar_one()
        assert idx_cnt == 0


async def test_create_knowledge_auto_embeds_via_stub(service_stub) -> None:
    """7. 额外：create_knowledge 触发 _embed_and_store（StubEmbedding 路径）。

    确认 KnowledgeService.create_knowledge 实际调用 embedding + 写 vec_idx。
    不依赖 Ollama，只验证布线正确。
    """
    service, stub, factory = service_stub
    stub._mapping = {  # type: ignore[attr-defined]
        "风格偏好": _unit([1.0, 0.0, 0.0] + [0.0] * (EMBEDDING_DIM - 3)),
        "天气": _unit([0.0, 0.0, 1.0] + [0.0] * (EMBEDDING_DIM - 3)),
    }

    await service.create_knowledge(
        title="风格偏好", summary="", content="user prefers terse", scope="global"
    )
    await service.create_knowledge(
        title="天气", summary="", content="weather stub", scope="global"
    )

    query = _unit([1.0, 0.0, 0.0] + [0.0] * (EMBEDDING_DIM - 3))
    async with factory() as session:
        hits = await vr.vector_search(session, query, limit=5)
    titles = [h.title for h in hits]
    assert titles[0] == "风格偏好"


# ---------------------------------------------------------------------------
# 集成测试 fixture（真实 Ollama + 733 fixture）
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def integration_service(tmp_path_factory) -> AsyncIterator[KnowledgeService]:
    """真实 EmbeddingService + 全量 733 fixture。慢，module 级复用。"""
    import scenario_conftest  # noqa: F401

    tmp_dir = tmp_path_factory.mktemp("mnemo-m1-int")
    db_path = tmp_dir / "mnemo.db"
    engine = await _build_engine(db_path)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    emb = EmbeddingService(config=MnemoConfig())
    # warmup：若 Ollama 不可用，整个 module 跳过
    ok = await emb.warmup()
    if not ok:
        await engine.dispose()
        pytest.skip("Ollama 不可用或 qwen3-embedding:0.6b 未 warmup，跳过集成测试")
    # 显式注入 embedding_service 才会启用向量写入路径
    service = KnowledgeService(session_factory=factory, embedding_service=emb)

    entries = scenario_conftest._load_knowledge_entries()
    inserted, _skipped = await scenario_conftest._insert_all(service, entries)
    # 少部分 fixture 可能因标题重复被跳，放宽到 >= 700
    assert inserted >= 700, f"fixture 加载过少：{inserted}"
    try:
        yield service
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# 集成测试（6 条）
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_integration_create_knowledge_auto_embeds(
    integration_service: KnowledgeService,
) -> None:
    """I1. create_knowledge 自动生成 embedding（真实 Ollama）。

    用专属标题避免与 fixture 冲突。
    """
    title = "M1_integration_probe_风格偏好_xyz"
    await integration_service.create_knowledge(
        title=title, summary="probe", content="用户偏好：直接简洁不要 AI 腔", scope="global"
    )

    factory = integration_service._session_factory
    async with factory() as session:
        row = (
            await session.execute(select(Knowledge).where(Knowledge.title == title))
        ).scalar_one()
        vecs = (
            await session.execute(
                select(KnowledgeVec).where(KnowledgeVec.knowledge_id == row.id)
            )
        ).scalars().all()
    assert len(vecs) == 1
    assert len(vecs[0].vector) == EMBEDDING_DIM * 4  # float32
    assert vecs[0].model_name == "qwen3-embedding:0.6b"


@pytest.mark.integration
async def test_integration_vector_search_returns_semantic_hits(
    integration_service: KnowledgeService,
) -> None:
    """I2. 直接 vector_search 返回语义相关结果。"""
    emb = integration_service._get_embedding()  # type: ignore[attr-defined]
    query_vec = await emb.embed("用户偏好表达方式")
    assert query_vec is not None

    factory = integration_service._session_factory
    async with factory() as session:
        hits = await vr.vector_search(session, query_vec, scope="global", limit=10)

    titles = [h.title for h in hits]
    assert hits, "vector_search 空结果"
    # 期望 Top-10 至少命中一条明显偏好类条目（title 含 "偏好"/"AI 腔"/"直接"/"简洁" 等）
    assert any(
        any(k in t for k in ("偏好", "AI", "直接", "简洁", "风格", "不要"))
        for t in titles[:10]
    ), f"语义检索 Top-10 无明显相关条目: {titles[:10]}"


@pytest.mark.integration
async def test_integration_fts_mode_unchanged_from_phase1(
    integration_service: KnowledgeService,
) -> None:
    """I3. 默认 search (FTS) 行为不变：Phase 1 能命中的关键词仍能命中。"""
    # 直接用 Phase 1 样板查询：强关键词，FTS 必命中
    hits = await integration_service.search("中文分词", limit=10)
    titles = [h["title"] for h in hits]
    assert any("分词" in t or "中文" in t for t in titles), (
        f"FTS 模式下中文分词关键词未命中: {titles}"
    )


@pytest.mark.integration
async def test_integration_733_embed_success_rate(
    integration_service: KnowledgeService,
) -> None:
    """I4. 733 条 fixture 全量 embed 成功率 ≥99%（硬门禁）。

    fixture 已在 module setup 中写入，同步触发 embed。
    这里查实际 knowledge_vec 覆盖率。
    """
    factory = integration_service._session_factory
    async with factory() as session:
        total = (
            await session.execute(
                text("SELECT COUNT(*) FROM knowledge WHERE status = 'active'")
            )
        ).scalar_one()
        with_vec = (
            await session.execute(
                text(
                    "SELECT COUNT(DISTINCT knowledge_id) FROM knowledge_vec "
                    "WHERE model_name = :m"
                ),
                {"m": "qwen3-embedding:0.6b"},
            )
        ).scalar_one()

    assert total > 0
    rate = with_vec / total
    assert rate >= 0.99, f"embed 成功率 {rate:.3f} < 99% ({with_vec}/{total})"


@pytest.mark.integration
async def test_integration_pure_vector_p95_latency(
    integration_service: KnowledgeService,
) -> None:
    """I5. 100 条查询 P95 延迟（embed + vector_search）≤ 1200ms。

    预算：800ms embed + 120ms vector + 余量。
    """
    emb = integration_service._get_embedding()  # type: ignore[attr-defined]
    factory = integration_service._session_factory

    queries = [
        "用户偏好", "中文分词", "风险", "交付规则", "session 隔离",
        "为什么选 SQLite", "测试框架", "FTS5 性能", "embedding 超时", "熔断器",
        "向量检索", "sqlite-vec", "Ollama", "知识库", "MCP",
        "CLI", "markdown 解析", "wikilink", "关系图谱", "authority",
    ]
    rng = random.Random(42)
    pool = list(queries)
    samples = [rng.choice(pool) for _ in range(100)]

    durations: list[float] = []
    for q in samples:
        start = time.monotonic()
        vec = await emb.embed(q)
        if vec is None:
            continue
        async with factory() as session:
            await vr.vector_search(session, vec, limit=20)
        durations.append((time.monotonic() - start) * 1000)

    assert len(durations) >= 90, f"成功样本过少：{len(durations)}"
    durations.sort()
    p95 = durations[int(len(durations) * 0.95) - 1]
    assert p95 <= 1200, f"P95 延迟 {p95:.0f}ms > 1200ms 预算"


@pytest.mark.integration
async def test_integration_eval_e2e_no_regression(
    integration_service: KnowledgeService,
) -> None:
    """I6. EVAL E2E ≥ 93.8% 不回退（用 EVAL_CASES 纯 search 子集保护）。

    M1 只加向量写入路径，默认 search 仍走 FTS，此测试保护 Phase 1 不被 M1
    改动破坏。直接复用 test_intelligence.EVAL_CASES 中 type="search" +
    expected_any 子集（与 Phase 1 EVAL 一致）。
    """
    from test_intelligence import EVAL_CASES

    # 只取纯 FTS search + 有 expected_any 的 case（跳 tag / source / related）
    cases = [
        c for c in EVAL_CASES
        if c.get("type") == "search" and c.get("expected_any")
    ]
    assert len(cases) >= 8, f"EVAL 子集过少：{len(cases)}"

    passed = 0
    failures: list[str] = []
    for c in cases:
        hits = await integration_service.search(
            c["query"], scope=c.get("scope"), limit=20
        )
        titles = [h["title"] for h in hits]
        if any(t in titles for t in c["expected_any"]):
            passed += 1
        else:
            failures.append(f"{c['id']} query={c['query']!r} top5={titles[:5]}")

    rate = passed / len(cases)
    assert rate >= 0.938, (
        f"EVAL E2E 通过率 {rate:.1%} ({passed}/{len(cases)}) < 93.8% 基线；"
        f"失败 case:\n" + "\n".join(failures)
    )


# ---------------------------------------------------------------------------
# topk_cosine_by_scope — Write-gate L2 候选查询
# ---------------------------------------------------------------------------


async def test_topk_cosine_by_scope_returns_similarity_sorted(service_stub) -> None:
    """越接近 query 的向量 cosine 越高，降序排列；scope 外的不进结果。"""
    _service, _stub, factory = service_stub
    query = _unit([0.9, 0.3, 0.05] + [0.0] * (EMBEDDING_DIM - 3))

    async with factory() as session:
        for title, scope, v in (
            ("A-global", "global", _one_hot(0)),          # 最近
            ("B-global", "global", _one_hot(1)),          # 次近
            ("C-session", "session", _one_hot(0)),        # 近但 scope 不匹配
        ):
            row = Knowledge(title=title, summary="", content="", tags="[]", scope=scope)
            session.add(row)
            await session.flush()
            await vr.upsert_vector(session, row.id, MODEL_NAME, v)
        await session.commit()

    async with factory() as session:
        hits = await vr.topk_cosine_by_scope(session, query, "global", k=10)

    titles = [h["title"] for h in hits]
    assert titles == ["A-global", "B-global"]
    assert hits[0]["cosine"] > hits[1]["cosine"]
    assert all(set(h.keys()) == {"id", "title", "cosine"} for h in hits)
    assert -1.0 <= hits[0]["cosine"] <= 1.0


async def test_topk_cosine_by_scope_project_filter(service_stub) -> None:
    _service, _stub, factory = service_stub
    query = _one_hot(0)

    async with factory() as session:
        for title, project in (("p1-hit", "proj1"), ("p2-hit", "proj2")):
            row = Knowledge(
                title=title, summary="", content="", tags="[]",
                scope="project", project_name=project,
            )
            session.add(row)
            await session.flush()
            await vr.upsert_vector(session, row.id, MODEL_NAME, _one_hot(0))
        await session.commit()

    async with factory() as session:
        hits = await vr.topk_cosine_by_scope(
            session, query, "project", project_name="proj1", k=10
        )
    assert [h["title"] for h in hits] == ["p1-hit"]


async def test_topk_cosine_by_scope_excludes_superseded(service_stub) -> None:
    _service, _stub, factory = service_stub
    query = _one_hot(0)

    async with factory() as session:
        old = Knowledge(title="old", summary="", content="", tags="[]", scope="global", status="superseded")
        active = Knowledge(title="active", summary="", content="", tags="[]", scope="global")
        session.add(old)
        session.add(active)
        await session.flush()
        await vr.upsert_vector(session, old.id, MODEL_NAME, _one_hot(0))
        await vr.upsert_vector(session, active.id, MODEL_NAME, _one_hot(0))
        await session.commit()

    async with factory() as session:
        hits = await vr.topk_cosine_by_scope(session, query, "global", k=10)
    assert [h["title"] for h in hits] == ["active"]


async def test_topk_cosine_by_scope_respects_k(service_stub) -> None:
    _service, _stub, factory = service_stub
    query = _unit([1.0, 0.5, 0.25, 0.1] + [0.0] * (EMBEDDING_DIM - 4))

    async with factory() as session:
        for i in range(6):
            row = Knowledge(title=f"t-{i}", summary="", content="", tags="[]", scope="global")
            session.add(row)
            await session.flush()
            await vr.upsert_vector(session, row.id, MODEL_NAME, _one_hot(i))
        await session.commit()

    async with factory() as session:
        hits = await vr.topk_cosine_by_scope(session, query, "global", k=3)
    assert len(hits) == 3


async def test_topk_cosine_by_scope_empty_when_no_scope_match(service_stub) -> None:
    _service, _stub, factory = service_stub
    query = _one_hot(0)

    async with factory() as session:
        row = Knowledge(title="only-session", summary="", content="", tags="[]", scope="session")
        session.add(row)
        await session.flush()
        await vr.upsert_vector(session, row.id, MODEL_NAME, _one_hot(0))
        await session.commit()

    async with factory() as session:
        hits = await vr.topk_cosine_by_scope(session, query, "global", k=10)
    assert hits == []


# ---------------------------------------------------------------------------
# 运行入口：
#   pytest tests/test_vector_search.py -v                    # 全部
#   pytest tests/test_vector_search.py -m "not integration"  # 只跑单测
#   pytest tests/test_vector_search.py -m integration        # 只跑集成
# ---------------------------------------------------------------------------
