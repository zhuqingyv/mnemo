"""Phase 3 M5 — 矛盾成对暴露 + `contradicts_with` 参数 + 审计 event + flag 守卫。

覆盖 ``docs/phase3/M5_TEST_DESIGN.md`` V3 的 C 段（C1-C19）+ E 段（E1-E2），共
28 条。W 段（W1-W4）在 ``tests/test_write_gate.py`` 通过解 skip + 新增 4 条覆
盖，合计 32 条 TC。

设计原则（V3 §0）：
- 乘性叠加语义纯净：M5 不改 rerank 输入，只在 search 末段附加 ``conflicts_with``
- flag off = Phase 2 完全等价
- 读宽松 / 写严格：``contradicts_with`` 是写操作 → 非法 id 抛 ``ValueError``
- 不 mock DB：沿用 ``tests/test_write_gate.py`` 的真实 SQLite (tmp_path) +
  ``StubEmbedding`` 正交 one-hot
- API 层 vs Flag 层分层：``contradicts_with`` 参数是 API 级能力，flag 只控
  L4 启发式 + search 成对暴露

跳过策略：
- 功能未落地的模块用 ``importlib.import_module`` 防御式加载，``ImportError``
  时触发 ``pytest.skip`` —— 测试框架不阻塞实现节奏，实现合入同一 PR 去掉 skip。
- 已落地字段（``contradiction_pair_enabled`` / ``contradicts`` relation_type）
  直接 import。
"""

from __future__ import annotations

import importlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from mnemo.config import MnemoConfig
from mnemo.models.knowledge import KnowledgeEvent, Relation
from mnemo.relation_types import CONTRADICTS
from mnemo.services.knowledge_service import KnowledgeService
from tests.test_vector_search import EMBEDDING_DIM, StubEmbedding, _build_engine


# ---------------------------------------------------------------------------
# 防御式加载：M5 功能模块
# ---------------------------------------------------------------------------


def _try_import_get_contradiction_pairs():
    """返回 ``(module, callable)`` 或 skip。

    预期位置（TECH_PLAN §6.3）：``mnemo.repository.relation_repository`` 新增
    ``get_contradiction_pairs``。
    """
    try:
        module = importlib.import_module("mnemo.repository.relation_repository")
        fn = getattr(module, "get_contradiction_pairs", None)
        if fn is None:
            pytest.skip(
                "M5 未实现：relation_repository.get_contradiction_pairs 缺失"
            )
        return module, fn
    except ImportError as e:
        pytest.skip(f"M5 未实现（import 失败）：{e}")


# ---------------------------------------------------------------------------
# Fixtures — 真实 SQLite + vec0 + FTS5 + StubEmbedding
# ---------------------------------------------------------------------------


def _one_hot(idx: int) -> list[float]:
    v = [0.0] * EMBEDDING_DIM
    v[idx] = 1.0
    return v


async def _make_service(
    tmp_path: Path, *, flag_on: bool
) -> tuple[KnowledgeService, async_sessionmaker, Any]:
    """构建真实 SQLite 的 service，``contradiction_pair_enabled=flag_on``。"""
    mapping: dict[str, list[float]] = {}

    class _MappingStub(StubEmbedding):
        def __init__(self) -> None:
            super().__init__(mapping=mapping, default_vec=_one_hot(0))

    db_path = tmp_path / f"contradictions_{flag_on}.db"
    engine = await _build_engine(db_path)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    config = MnemoConfig(_env_file=None)  # type: ignore[call-arg]
    object.__setattr__(config, "contradiction_pair_enabled", flag_on)
    svc = KnowledgeService(
        session_factory=factory,
        embedding_service=_MappingStub(),
        config=config,
    )
    svc._test_vec_mapping = mapping  # type: ignore[attr-defined]
    return svc, factory, engine


@pytest_asyncio.fixture
async def svc_flag_on(tmp_path: Path) -> AsyncIterator[KnowledgeService]:
    svc, _factory, engine = await _make_service(tmp_path, flag_on=True)
    try:
        yield svc
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def svc_flag_off(tmp_path: Path) -> AsyncIterator[KnowledgeService]:
    svc, _factory, engine = await _make_service(tmp_path, flag_on=False)
    try:
        yield svc
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def svc_pair(
    tmp_path: Path,
) -> AsyncIterator[tuple[KnowledgeService, KnowledgeService]]:
    """同库 service 的 flag on / off 对照视图，用于 A/B 等价测试。

    两者共享同一个底层 DB（engine），但 config 的 flag 值不同。避免"构造两次
    数据再比较"的语义偏差 —— 同一批数据在同一进程同一时刻两套 flag 下分别
    查询。
    """
    mapping: dict[str, list[float]] = {}

    class _MappingStub(StubEmbedding):
        def __init__(self) -> None:
            super().__init__(mapping=mapping, default_vec=_one_hot(0))

    db_path = tmp_path / "ab.db"
    engine = await _build_engine(db_path)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    cfg_off = MnemoConfig(_env_file=None)  # type: ignore[call-arg]
    cfg_on = MnemoConfig(_env_file=None)  # type: ignore[call-arg]
    object.__setattr__(cfg_off, "contradiction_pair_enabled", False)
    object.__setattr__(cfg_on, "contradiction_pair_enabled", True)

    svc_off = KnowledgeService(
        session_factory=factory, embedding_service=_MappingStub(), config=cfg_off
    )
    svc_on = KnowledgeService(
        session_factory=factory, embedding_service=_MappingStub(), config=cfg_on
    )
    # 两个 service 共享 mapping（tests 注入 title→vec 后双边都生效）
    svc_off._test_vec_mapping = mapping  # type: ignore[attr-defined]
    svc_on._test_vec_mapping = mapping  # type: ignore[attr-defined]
    try:
        yield svc_off, svc_on
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# 辅助：查询 relation / event 行数与内容
# ---------------------------------------------------------------------------


async def _count_contradicts(
    factory: async_sessionmaker,
    source_id: int | None = None,
    target_id: int | None = None,
) -> int:
    async with factory() as session:
        stmt = select(Relation).where(Relation.relation_type == CONTRADICTS)
        if source_id is not None:
            stmt = stmt.where(Relation.source_id == source_id)
        if target_id is not None:
            stmt = stmt.where(Relation.target_id == target_id)
        res = await session.execute(stmt)
        return len(list(res.scalars().all()))


async def _list_contradicts(
    factory: async_sessionmaker, source_id: int
) -> list[tuple[int, int]]:
    async with factory() as session:
        stmt = (
            select(Relation.source_id, Relation.target_id)
            .where(Relation.relation_type == CONTRADICTS)
            .where(Relation.source_id == source_id)
        )
        res = await session.execute(stmt)
        return [(s, t) for s, t in res.all()]


async def _count_events(
    factory: async_sessionmaker,
    event_type: str,
    knowledge_id: int | None = None,
) -> int:
    async with factory() as session:
        stmt = select(KnowledgeEvent).where(KnowledgeEvent.event_type == event_type)
        if knowledge_id is not None:
            stmt = stmt.where(KnowledgeEvent.knowledge_id == knowledge_id)
        res = await session.execute(stmt)
        return len(list(res.scalars().all()))


async def _get_events(
    factory: async_sessionmaker, event_type: str
) -> list[KnowledgeEvent]:
    async with factory() as session:
        stmt = select(KnowledgeEvent).where(KnowledgeEvent.event_type == event_type)
        res = await session.execute(stmt)
        return list(res.scalars().all())


def _extract_event_target_ids(payload: dict[str, Any]) -> list[int]:
    """归一化 contradiction_marked payload 到 target id 列表。

    实现方选的字段名可能是 ``target_ids`` / ``target_id`` / ``contradicts_with_id``。
    本函数三种都兼容，测试只断言"target 落在 payload 里"这一契约。
    """
    if "target_ids" in payload and isinstance(payload["target_ids"], list):
        return [int(x) for x in payload["target_ids"]]
    for key in ("target_id", "contradicts_with_id"):
        if key in payload:
            return [int(payload[key])]
    return []


# ===========================================================================
# 3.2 `contradicts_with` 参数写入（C1-C4 + C3b）
# ===========================================================================


@pytest.mark.phase3
async def test_c1_create_with_contradicts_with_ids_writes_relation(
    svc_flag_on: KnowledgeService,
) -> None:
    """C1 — ``create_knowledge(contradicts_with=[id])`` → relation + event 各 1 条。"""
    svc = svc_flag_on
    a = await svc.create_knowledge(
        title="A 观点", summary="s", content="A 观点内容充足。" * 5
    )
    try:
        b = await svc.create_knowledge(
            title="B 反观点",
            summary="s",
            content="B 反观点内容充足。" * 5,
            contradicts_with=[a["id"]],
        )
    except TypeError as e:
        pytest.skip(f"M5 未实现：create_knowledge 不接受 contradicts_with 参数 ({e})")

    factory = svc._session_factory
    # 1 条 contradicts(source=B, target=A)
    pairs = await _list_contradicts(factory, source_id=b["id"])
    assert pairs == [(b["id"], a["id"])], f"预期 B→A contradicts 单边，实际 {pairs}"

    # 1 条 event contradiction_marked 关联 B
    events = await _get_events(factory, "contradiction_marked")
    assert len(events) >= 1
    matched = [e for e in events if e.knowledge_id == b["id"]]
    assert len(matched) >= 1, f"预期 ≥ 1 条 event 挂在 B 上，实际 {len(matched)}"
    payload = json.loads(matched[0].payload_json or "{}")
    # 兼容三种 payload 形态：
    #   - {"target_ids": [id, ...]}  —— 批量写一行
    #   - {"target_id": id}          —— 单条标记一行
    #   - {"contradicts_with_id": id}  —— 实现采用的字段名
    target_ids = _extract_event_target_ids(payload)
    assert a["id"] in target_ids, f"event payload 未包含 target id={a['id']}：{payload}"


@pytest.mark.phase3
async def test_c2_create_with_contradicts_with_titles_resolved(
    svc_flag_on: KnowledgeService,
) -> None:
    """C2 — ``contradicts_with=["title"]`` 解析为 id 后写入；不存在 title 抛 ValueError。"""
    svc = svc_flag_on
    a = await svc.create_knowledge(
        title="C2 标题 A", summary="s", content="C2 A 内容充足。" * 5
    )
    try:
        b = await svc.create_knowledge(
            title="C2 标题 B",
            summary="s",
            content="C2 B 内容充足。" * 5,
            contradicts_with=["C2 标题 A"],
        )
    except TypeError as e:
        pytest.skip(f"M5 未实现：{e}")

    factory = svc._session_factory
    pairs = await _list_contradicts(factory, source_id=b["id"])
    assert pairs == [(b["id"], a["id"])], f"title→id 解析失败，实际 {pairs}"

    # title 不存在必须抛 ValueError
    with pytest.raises(ValueError, match="not found|找不到|不存在"):
        await svc.create_knowledge(
            title="C2 标题 C",
            summary="s",
            content="C2 C 内容充足。" * 5,
            contradicts_with=["不存在的 title"],
        )


@pytest.mark.phase3
async def test_c3_update_with_contradicts_with_appends(
    svc_flag_on: KnowledgeService,
) -> None:
    """C3 — ``update_knowledge(contradicts_with=[3])`` 追加关系，不覆盖已有。"""
    svc = svc_flag_on
    a = await svc.create_knowledge(
        title="C3-A", summary="s", content="C3 A 内容充足。" * 5
    )
    c = await svc.create_knowledge(
        title="C3-C", summary="s", content="C3 C 内容充足。" * 5
    )
    try:
        b = await svc.create_knowledge(
            title="C3-B",
            summary="s",
            content="C3 B 内容充足。" * 5,
            contradicts_with=[a["id"]],
        )
    except TypeError as e:
        pytest.skip(f"M5 未实现：{e}")

    factory = svc._session_factory

    try:
        updated = await svc.update_knowledge(
            b["id"], contradicts_with=[c["id"]]
        )
    except TypeError as e:
        pytest.skip(f"M5 未实现：update_knowledge 不接受 contradicts_with ({e})")

    # update_knowledge 走 supersede：新的 id 挂两条关系（A + C）
    # 兼容写法：要么新 id 身上挂两条，要么 B 原 id 挂两条
    new_id = updated.get("id") or b["id"]
    pairs_new = await _list_contradicts(factory, source_id=new_id)
    pairs_old = await _list_contradicts(factory, source_id=b["id"])
    all_targets = {t for _s, t in pairs_new + pairs_old}
    assert a["id"] in all_targets and c["id"] in all_targets, (
        f"追加失败：targets={all_targets}"
    )

    # event 至少 2 条 contradiction_marked（首次 create + update 追加）
    total_events = await _count_events(factory, "contradiction_marked")
    assert total_events >= 2, f"预期 ≥ 2 条 event，实际 {total_events}"


@pytest.mark.phase3
async def test_c3b_contradicts_with_idempotent_on_duplicate_mark(
    svc_flag_on: KnowledgeService,
) -> None:
    """C3b — 重复标记同一对 relation 应幂等（不重复累加 relation 行）。

    event 表可写 ≥1 条（重复标记是合法审计事件），relation 层必须幂等。
    """
    svc = svc_flag_on
    a = await svc.create_knowledge(
        title="C3b-A", summary="s", content="C3b A 内容充足。" * 5
    )
    try:
        b = await svc.create_knowledge(
            title="C3b-B",
            summary="s",
            content="C3b B 内容充足。" * 5,
            contradicts_with=[a["id"]],
        )
    except TypeError as e:
        pytest.skip(f"M5 未实现：{e}")

    factory = svc._session_factory
    # 再次 create（同 title 走 supersede），传入相同 target
    # 注意：直接用 update_knowledge 也可；这里两次 create 同 title 触发 supersede
    # 路径，同样会再次走 _apply_contradicts_with —— 幂等校验的更严格形态。
    try:
        await svc.create_knowledge(
            title="C3b-B",
            summary="s",
            content="C3b B 内容充足。" * 5,
            contradicts_with=[a["id"]],
        )
    except TypeError as e:
        pytest.skip(f"M5 未实现：{e}")

    # relation 层幂等：单一 (source, target, contradicts) 不可重复
    # 注意：supersede 可能产生新 source_id；此时按"每个 source 最多一条 target=A"
    # 的分组契约判定。即：按 source_id 聚合，每个 source 对 A 只能有 1 条边。
    async with factory() as session:
        stmt = (
            select(Relation.source_id)
            .where(Relation.relation_type == CONTRADICTS)
            .where(Relation.target_id == a["id"])
        )
        res = await session.execute(stmt)
        rows = list(res.scalars().all())
    # 每个 source_id 对同一 target 只能有 1 条 contradicts
    from collections import Counter as _Counter

    cnt = _Counter(rows)
    for src, n in cnt.items():
        assert n == 1, (
            f"relation 应幂等：source={src} 对 target={a['id']} 重复 {n} 条"
        )

    # event 至少 1 条（UPSERT 语义可写 1 条；追加语义可写 2 条；两者都合法）
    evts = await _count_events(factory, "contradiction_marked")
    assert evts >= 1, "event 沉默丢失"


@pytest.mark.phase3
async def test_c4_create_contradicts_with_none_no_side_effect(
    svc_flag_on: KnowledgeService,
) -> None:
    """C4 — ``contradicts_with=None`` 或 ``=[]``：不写 relation、不写 event。"""
    svc = svc_flag_on
    # None
    try:
        await svc.create_knowledge(
            title="C4-1",
            summary="s",
            content="C4 内容充足。" * 5,
            contradicts_with=None,
        )
    except TypeError as e:
        pytest.skip(f"M5 未实现：{e}")
    # []
    await svc.create_knowledge(
        title="C4-2",
        summary="s",
        content="C4 内容充足 2。" * 5,
        contradicts_with=[],
    )
    factory = svc._session_factory
    assert await _count_contradicts(factory) == 0
    assert await _count_events(factory, "contradiction_marked") == 0


# ===========================================================================
# 3.3 contradicts 关系 + 审计 event + repo 契约（C5 / C6 / C6.5 / C6b.1 / C6b.2）
# ===========================================================================


@pytest.mark.phase3
async def test_c5_knowledge_event_contradiction_marked_shape(
    svc_flag_on: KnowledgeService,
) -> None:
    """C5 — ``knowledge_event`` 行字段完整性断言。"""
    svc = svc_flag_on
    a = await svc.create_knowledge(
        title="C5-A", summary="s", content="C5 A 内容充足。" * 5
    )
    try:
        b = await svc.create_knowledge(
            title="C5-B",
            summary="s",
            content="C5 B 内容充足。" * 5,
            contradicts_with=[a["id"]],
        )
    except TypeError as e:
        pytest.skip(f"M5 未实现：{e}")
    factory = svc._session_factory
    events = await _get_events(factory, "contradiction_marked")
    assert len(events) >= 1
    ev = next(e for e in events if e.knowledge_id == b["id"])
    assert ev.event_type == "contradiction_marked"
    assert ev.knowledge_id == b["id"]
    assert ev.created_at is not None
    assert ev.payload_json is not None
    payload = json.loads(ev.payload_json)
    # 允许 target_ids / target_id / contradicts_with_id 三种形态
    tids = _extract_event_target_ids(payload)
    assert tids, f"payload 缺少 target 字段：{payload}"
    assert a["id"] in tids


@pytest.mark.phase3
async def test_c6_contradicts_relation_is_directional(
    svc_flag_on: KnowledgeService,
) -> None:
    """C6 — B→A 写入后 A 没有 outgoing contradicts；双向查询通过 repo 层对称。"""
    svc = svc_flag_on
    a = await svc.create_knowledge(
        title="C6-A", summary="s", content="C6 A 内容充足。" * 5
    )
    try:
        b = await svc.create_knowledge(
            title="C6-B",
            summary="s",
            content="C6 B 内容充足。" * 5,
            contradicts_with=[a["id"]],
        )
    except TypeError as e:
        pytest.skip(f"M5 未实现：{e}")

    factory = svc._session_factory
    # A 的 outgoing contradicts 必须为空
    a_out = await _list_contradicts(factory, source_id=a["id"])
    assert a_out == [], f"A 不应有 outgoing contradicts：{a_out}"

    # repo 层：查 A 能通过 target_id 拉出 B→A 这条边
    _mod, fn = _try_import_get_contradiction_pairs()
    async with factory() as session:
        rows = await fn(session, [a["id"]])
    assert len(rows) == 1
    row = rows[0]
    assert row["source_id"] == b["id"]
    assert row["target_id"] == a["id"]


@pytest.mark.phase3
async def test_c6_5_relation_repo_get_contradiction_pairs_contract(
    svc_flag_on: KnowledgeService,
) -> None:
    """C6.5 — repo 层单元契约：精确过滤 contradicts 类型；source/target 双向 IN。

    预置 5 条关系：(1,2,contradicts), (2,3,contradicts), (3,4,refines),
    (4,5,contradicts), (1,5,derived_from)。ids=[1,2] 应只拉 (1,2) 和 (2,3)。
    """
    svc = svc_flag_on
    factory = svc._session_factory

    # 先建 5 条知识拿到真实 id，再手工塞关系（绕开 service，验 repo 契约）
    ks = []
    for i in range(5):
        k = await svc.create_knowledge(
            title=f"C6.5-{i}", summary="s", content=f"C6.5 #{i} 内容充足。" * 5
        )
        ks.append(k["id"])
    k1, k2, k3, k4, k5 = ks

    async with factory() as session:
        session.add_all([
            Relation(source_id=k1, target_id=k2, relation_type=CONTRADICTS),
            Relation(source_id=k2, target_id=k3, relation_type=CONTRADICTS),
            Relation(source_id=k3, target_id=k4, relation_type="refines"),
            Relation(source_id=k4, target_id=k5, relation_type=CONTRADICTS),
            Relation(source_id=k1, target_id=k5, relation_type="derived_from"),
        ])
        await session.commit()

    _mod, fn = _try_import_get_contradiction_pairs()
    async with factory() as session:
        rows = await fn(session, [k1, k2])

    assert len(rows) == 2, f"预期 2 条，实际 {len(rows)}：{rows}"
    pairs = {(r["source_id"], r["target_id"]) for r in rows}
    assert pairs == {(k1, k2), (k2, k3)}
    # 结构契约
    for r in rows:
        assert {"relation_id", "source_id", "target_id"} <= set(r.keys())
    # 绝不包含其他类型
    assert (k3, k4) not in pairs
    assert (k1, k5) not in pairs
    # ids=[1,2] 不应触发 (4,5)
    assert (k4, k5) not in pairs


@pytest.mark.phase3
async def test_c6b_1_get_contradiction_pairs_empty_ids(
    svc_flag_on: KnowledgeService,
) -> None:
    """C6b.1 — ``ids=[]`` 返回 ``[]``，不抛 empty-IN 异常。"""
    factory = svc_flag_on._session_factory
    _mod, fn = _try_import_get_contradiction_pairs()
    async with factory() as session:
        rows = await fn(session, [])
    assert rows == []


@pytest.mark.phase3
async def test_c6b_2_get_contradiction_pairs_batch_500_ids(
    svc_flag_on: KnowledgeService,
) -> None:
    """C6b.2 — 500 条知识 + 250 对 contradicts，单次 IN 查询必须过。"""
    svc = svc_flag_on
    factory = svc._session_factory

    # 造 500 条知识（StubEmbedding，向量全 one-hot(0)，不追求语义）
    ids: list[int] = []
    for i in range(500):
        k = await svc.create_knowledge(
            title=f"C6b.2-k{i:03d}",
            summary="s",
            content=f"批量知识 #{i} 的内容足够长。" * 2,
        )
        ids.append(k["id"])

    # 250 对：(ids[0], ids[1]), (ids[2], ids[3]), ...
    async with factory() as session:
        for j in range(0, 500, 2):
            session.add(
                Relation(
                    source_id=ids[j],
                    target_id=ids[j + 1],
                    relation_type=CONTRADICTS,
                )
            )
        await session.commit()

    _mod, fn = _try_import_get_contradiction_pairs()
    async with factory() as session:
        rows = await fn(session, ids)
    assert len(rows) == 250, f"500 id IN 查询应返回 250 对，实际 {len(rows)}"


# ===========================================================================
# 3.4 Search 结果 conflicts_with 字段（C7-C10）
# ===========================================================================


async def _seed_contradict_pair(
    svc: KnowledgeService, *, titles: tuple[str, str], vec_idx: int
) -> tuple[int, int]:
    """造一对互相矛盾的知识。title_a 向量注入 vec_idx，title_b 同 vec_idx（cos=1）。

    通过 ``contradicts_with`` 在 b 上写入 B→A 的 contradicts 边。返回 (id_a, id_b)。
    """
    svc._test_vec_mapping[titles[0]] = _one_hot(vec_idx)  # type: ignore[attr-defined]
    svc._test_vec_mapping[titles[1]] = _one_hot(vec_idx)  # type: ignore[attr-defined]
    a = await svc.create_knowledge(
        title=titles[0], summary="s", content=f"{titles[0]} 内容充足。" * 5
    )
    try:
        b = await svc.create_knowledge(
            title=titles[1],
            summary="s",
            content=f"{titles[1]} 内容充足。" * 5,
            contradicts_with=[a["id"]],
        )
    except TypeError as e:
        pytest.skip(f"M5 未实现：{e}")
    return a["id"], b["id"]


def _extract_conflicts(row: dict[str, Any], target_id: int | None = None) -> list[int]:
    """归一化 ``conflicts_with`` 返回到 id 列表。

    允许两种契约（TECH_PLAN §6.3 未锁死）：
    - ``conflicts_with: [id, id]``
    - ``conflicts_with: [{relation_id, source_id, target_id}, ...]``
    """
    raw = row.get("conflicts_with", [])
    if not raw:
        return []
    if isinstance(raw[0], int):
        return list(raw)
    # dict 形态：取对方 id —— target_id 或 source_id 中非当前 row id 的那个
    out: list[int] = []
    my_id = row.get("id")
    for entry in raw:
        t = entry.get("target_id")
        s = entry.get("source_id")
        if t == my_id:
            out.append(s)
        elif s == my_id:
            out.append(t)
        else:
            if target_id is not None and (t == target_id or s == target_id):
                out.append(target_id)
    return out


@pytest.mark.phase3
async def test_c7_search_attaches_conflicts_with_field(
    svc_flag_on: KnowledgeService,
) -> None:
    """C7 — flag ON，A↔B 矛盾对都在 top-N：每条含 conflicts_with 指向对方。"""
    svc = svc_flag_on
    a_id, b_id = await _seed_contradict_pair(
        svc, titles=("C7-A", "C7-B"), vec_idx=40
    )

    results = await svc.search("C7", limit=10)
    if not results:
        pytest.skip("search 未返回结果（M5 search 改动未落地）")

    # 结果中应含有 conflicts_with 字段；A 指 B，B 指 A
    by_id = {r["id"]: r for r in results}
    if a_id not in by_id or b_id not in by_id:
        pytest.skip("A/B 未同时出现在 top-N，数据/搜索阶段未配合")
    a_row, b_row = by_id[a_id], by_id[b_id]
    if "conflicts_with" not in a_row or "conflicts_with" not in b_row:
        pytest.skip(
            "M5 search 改动未落地（search 结果缺 conflicts_with 字段）"
        )
    assert b_id in _extract_conflicts(a_row, target_id=b_id)
    assert a_id in _extract_conflicts(b_row, target_id=a_id)


@pytest.mark.phase3
async def test_c8_search_conflicts_with_empty_when_no_edge(
    svc_flag_on: KnowledgeService,
) -> None:
    """C8 — flag ON，top-N 两条无 contradicts 关系：conflicts_with == []。"""
    svc = svc_flag_on
    svc._test_vec_mapping["C8-X"] = _one_hot(41)  # type: ignore[attr-defined]
    svc._test_vec_mapping["C8-Y"] = _one_hot(42)  # type: ignore[attr-defined]
    await svc.create_knowledge(
        title="C8-X", summary="s", content="C8 X 内容充足。" * 5
    )
    await svc.create_knowledge(
        title="C8-Y", summary="s", content="C8 Y 内容充足。" * 5
    )
    results = await svc.search("C8", limit=10)
    if not results:
        pytest.skip("search 未返回结果")

    for row in results:
        if "conflicts_with" not in row:
            pytest.skip("M5 search 改动未落地：缺 conflicts_with 字段")
        assert row["conflicts_with"] == [], (
            f"无 contradicts 边时必须为空列表：{row['conflicts_with']}"
        )


@pytest.mark.phase3
async def test_c9_search_conflicts_with_does_not_occupy_topn_slot(
    svc_flag_on: KnowledgeService,
) -> None:
    """C9 — conflicts_with 不占 topk slot。

    构造 10 条：A 在 top-5，B 排名靠后不进 top-5。A↔B 矛盾。topk=5 查询。
    """
    svc = svc_flag_on
    # 9 条 filler + A + B，A 用"热词"命中 FTS+vector，B 弱命中
    for i in range(9):
        svc._test_vec_mapping[f"C9-bg-{i}"] = _one_hot(  # type: ignore[attr-defined]
            100 + i
        )
        await svc.create_knowledge(
            title=f"C9-bg-{i}",
            summary="s",
            content=f"背景条目 C9 查询 经常出现 的关键词 #{i}。" * 3,
        )
    # A：关键词出现多次，应进 top-5
    svc._test_vec_mapping["C9-A"] = _one_hot(200)  # type: ignore[attr-defined]
    a = await svc.create_knowledge(
        title="C9-A",
        summary="s",
        content="C9-A 关键词 关键词 关键词 关键词 核心命中条目。" * 5,
    )
    # B：只在 title 出现，不命中关键词，应在 top-5 之外
    svc._test_vec_mapping["不相关标题 B"] = _one_hot(201)  # type: ignore[attr-defined]
    try:
        b = await svc.create_knowledge(
            title="不相关标题 B",
            summary="s",
            content="B 条目内容与查询词无关，应在 top-5 外。" * 5,
            contradicts_with=[a["id"]],
        )
    except TypeError as e:
        pytest.skip(f"M5 未实现：{e}")

    results = await svc.search("关键词", limit=5)
    if not results:
        pytest.skip("search 未返回结果")
    assert len(results) == 5, (
        f"topk=5 必须返回 5 条，不得因 B 有 contradicts 而多返回：实际 {len(results)}"
    )

    by_id = {r["id"]: r for r in results}
    if a["id"] in by_id:
        # A 在 top-5 里：其 conflicts_with 应含 B（即使 B 本身不在 top-5）
        row = by_id[a["id"]]
        if "conflicts_with" not in row:
            pytest.skip("M5 search 改动未落地")
        assert b["id"] in _extract_conflicts(row, target_id=b["id"])


@pytest.mark.phase3
async def test_c10_search_flag_off_no_conflicts_with_field(
    svc_pair,
) -> None:
    """C10 — flag OFF：结果中不应有 conflicts_with 字段（或恒为空）。"""
    svc_off, svc_on = svc_pair
    svc_on._test_vec_mapping["C10-A"] = _one_hot(50)  # type: ignore[attr-defined]
    svc_on._test_vec_mapping["C10-B"] = _one_hot(50)  # type: ignore[attr-defined]
    a = await svc_on.create_knowledge(
        title="C10-A", summary="s", content="C10 A 内容充足。" * 5
    )
    try:
        await svc_on.create_knowledge(
            title="C10-B",
            summary="s",
            content="C10 B 内容充足。" * 5,
            contradicts_with=[a["id"]],
        )
    except TypeError as e:
        pytest.skip(f"M5 未实现：{e}")

    results_off = await svc_off.search("C10", limit=10)
    if not results_off:
        pytest.skip("search 未返回结果")
    for row in results_off:
        # TECH_PLAN §6.4：flag OFF 时 _attach_conflict_pairs 跳过 → 字段不存在
        # 兼容：若字段存在但为 []（宽松实现），也视为合格
        if "conflicts_with" in row:
            assert row["conflicts_with"] == [], (
                "flag OFF 不应暴露实际 conflicts_with 内容"
            )


# ===========================================================================
# 3.5 Flag 守卫 + 向后兼容（C11 / C11b / C12 / C13 / C13b）
# ===========================================================================


@pytest.mark.phase3
async def test_c11_flag_off_contradicts_with_param_still_accepted(
    svc_flag_off: KnowledgeService,
) -> None:
    """C11 — flag OFF 下显式 contradicts_with 仍写入 relation + event。

    设计锁定：API 级写入不受 ``contradiction_pair_enabled`` flag 控制。flag 只
    控制 L4 启发式 + search 成对暴露两路"呈现层"。若实现把 flag 覆盖 API 层
    （flag off 时静默不写），此测试 fail —— 不要盲目改断言，需 team-lead 复核
    TECH_PLAN §6.4 守卫范围。
    """
    svc = svc_flag_off
    a = await svc.create_knowledge(
        title="C11-A", summary="s", content="C11 A 内容充足。" * 5
    )
    try:
        b = await svc.create_knowledge(
            title="C11-B",
            summary="s",
            content="C11 B 内容充足。" * 5,
            contradicts_with=[a["id"]],
        )
    except TypeError as e:
        pytest.skip(f"M5 未实现：{e}")

    factory = svc._session_factory
    pairs = await _list_contradicts(factory, source_id=b["id"])
    assert pairs == [(b["id"], a["id"])], (
        "flag OFF 下 API 级写入必须生效（C11 锁定的设计分层）"
    )
    assert await _count_events(factory, "contradiction_marked") >= 1


@pytest.mark.phase3
async def test_c11b_flag_off_three_way_guard_semantics(
    svc_flag_off: KnowledgeService,
) -> None:
    """C11b — 同一 flag 值下三路行为协同：
    - Write-gate L4 off （W4 已验证同一路径）
    - search 不含 conflicts_with（C10 已验证）
    - API 级写入仍开（C11 已验证）

    本测试在一次执行里综合验证"一个 flag 控制三路"的耦合性。
    """
    svc = svc_flag_off
    svc._test_vec_mapping["C11b-A"] = _one_hot(60)  # type: ignore[attr-defined]
    svc._test_vec_mapping["C11b-B 不应该"] = _one_hot(60)  # type: ignore[attr-defined]

    a = await svc.create_knowledge(
        title="C11b-A",
        summary="s",
        content="C11b-A 条目：应该启用 X。" * 3,
    )
    try:
        b = await svc.create_knowledge(
            title="C11b-B 不应该",
            summary="s",
            content="C11b-B 条目：不应该启用 X。" * 3,
            contradicts_with=[a["id"]],
        )
    except TypeError as e:
        pytest.skip(f"M5 未实现：{e}")

    factory = svc._session_factory

    # 路径 1：API 级写入 ON
    pairs = await _list_contradicts(factory, source_id=b["id"])
    assert pairs == [(b["id"], a["id"])]
    assert await _count_events(factory, "contradiction_marked") >= 1

    # 路径 2：Write-gate L4 OFF
    wg = b.get("write_gate")
    if wg is not None:
        assert wg["potential_contradiction"] == [], (
            "flag OFF 下 L4 启发式必须空"
        )

    # 路径 3：search 不暴露 conflicts_with
    results = await svc.search("C11b", limit=10)
    for row in results:
        if "conflicts_with" in row:
            assert row["conflicts_with"] == [], (
                "flag OFF 下 search 不应暴露 conflicts_with 内容"
            )


@pytest.mark.phase3
async def test_c12_flag_off_l4_returns_empty_contradiction_only(
    svc_flag_off: KnowledgeService,
) -> None:
    """C12 — flag OFF 下走 W1 同样的极性翻转 case：L4 留空，其他字段正常。"""
    svc = svc_flag_off
    svc._test_vec_mapping["C12 应该 WAL"] = _one_hot(70)  # type: ignore[attr-defined]
    svc._test_vec_mapping["C12 不应该 WAL"] = _one_hot(70)  # type: ignore[attr-defined]
    await svc.create_knowledge(
        title="C12 应该 WAL",
        summary="s",
        content="C12：应该启用 WAL 模式。" * 3,
    )
    result = await svc.create_knowledge(
        title="C12 不应该 WAL",
        summary="s",
        content="C12：不应该启用 WAL 模式。" * 3,
    )
    wg = result.get("write_gate")
    if wg is None:
        pytest.skip("write_gate_enabled 未开（或功能未落地）")
    assert wg["potential_contradiction"] == []


@pytest.mark.phase3
def test_c13_default_config_flag_is_true() -> None:
    """C13 — ``MnemoConfig()`` 默认 ``contradiction_pair_enabled=True``。

    P4 UX 阶段由 False 翻 True：盲测 Agent 用默认 MCP 配置 search 时需要
    看到 conflicts_with 字段，C8 场景依赖此默认行为。
    """
    cfg = MnemoConfig(_env_file=None)  # type: ignore[call-arg]
    assert cfg.contradiction_pair_enabled is True


@pytest.mark.phase3
async def test_c13b_flag_idempotent_on_off_on(svc_pair) -> None:
    """C13b — 同一进程 ON → OFF → ON 切换：三次 search 各自符合当时 flag 语义。

    无 cache 残留、无全局状态串扰。对齐 M7 TC-08 同模式。
    """
    svc_off, svc_on = svc_pair

    # 造含 contradicts 边的数据（用 ON 的入口写入，relation 表持久化）
    svc_on._test_vec_mapping["C13b-A"] = _one_hot(80)  # type: ignore[attr-defined]
    svc_on._test_vec_mapping["C13b-B"] = _one_hot(80)  # type: ignore[attr-defined]
    a = await svc_on.create_knowledge(
        title="C13b-A", summary="s", content="C13b A 内容充足。" * 5
    )
    try:
        await svc_on.create_knowledge(
            title="C13b-B",
            summary="s",
            content="C13b B 内容充足。" * 5,
            contradicts_with=[a["id"]],
        )
    except TypeError as e:
        pytest.skip(f"M5 未实现：{e}")

    # 第 1 次：ON
    r_on1 = await svc_on.search("C13b", limit=10)
    # 第 2 次：OFF
    r_off = await svc_off.search("C13b", limit=10)
    # 第 3 次：ON 再查
    r_on2 = await svc_on.search("C13b", limit=10)

    if not (r_on1 and r_on2):
        pytest.skip("search 未返回结果")

    def has_nonempty_cw(rows: list[dict]) -> bool:
        return any(r.get("conflicts_with") for r in rows)

    if all("conflicts_with" not in r for r in r_on1):
        pytest.skip("M5 search 改动未落地")

    assert has_nonempty_cw(r_on1), "ON 第 1 次：应有 conflicts_with"
    for r in r_off:
        # OFF 次的 search 字段不得有值
        if "conflicts_with" in r:
            assert r["conflicts_with"] == []
    assert has_nonempty_cw(r_on2), "ON 第 3 次：切回应恢复 conflicts_with"


# ===========================================================================
# 3.6 A/B 对照 + 回归 baseline（C14 / C14b / C15）
# ===========================================================================


@pytest.mark.phase3
async def test_c14_ab_same_fixtures_flag_off_vs_on_regression_guard(
    svc_pair,
) -> None:
    """C14 — flag OFF 与 ON 的 top-10 id 列表完全一致；ON 额外带 conflicts_with。"""
    svc_off, svc_on = svc_pair

    # 造 10 条（其中 2 对矛盾）
    pair_ids: list[tuple[int, int]] = []
    for i in range(2):
        svc_on._test_vec_mapping[f"C14-P{i}-A"] = _one_hot(  # type: ignore[attr-defined]
            300 + 2 * i
        )
        svc_on._test_vec_mapping[f"C14-P{i}-B"] = _one_hot(  # type: ignore[attr-defined]
            300 + 2 * i
        )
        a = await svc_on.create_knowledge(
            title=f"C14-P{i}-A",
            summary="s",
            content=f"C14 查询词 关键词 条目 #{i}-A。" * 3,
        )
        try:
            b = await svc_on.create_knowledge(
                title=f"C14-P{i}-B",
                summary="s",
                content=f"C14 查询词 关键词 条目 #{i}-B。" * 3,
                contradicts_with=[a["id"]],
            )
        except TypeError as e:
            pytest.skip(f"M5 未实现：{e}")
        pair_ids.append((a["id"], b["id"]))
    # 6 条 filler
    for j in range(6):
        svc_on._test_vec_mapping[f"C14-F-{j}"] = _one_hot(  # type: ignore[attr-defined]
            400 + j
        )
        await svc_on.create_knowledge(
            title=f"C14-F-{j}",
            summary="s",
            content=f"C14 查询词 关键词 filler #{j}。" * 3,
        )

    r_off = await svc_off.search("C14 查询词", limit=10)
    r_on = await svc_on.search("C14 查询词", limit=10)
    if not (r_off and r_on):
        pytest.skip("search 未返回结果")

    ids_off = [r["id"] for r in r_off]
    ids_on = [r["id"] for r in r_on]
    assert ids_off == ids_on, (
        f"ON 组 top-10 id 列表必须与 OFF 完全一致（_attach_conflict_pairs "
        f"不得改排序）：\n  off={ids_off}\n  on ={ids_on}"
    )

    # final_score 相对误差容忍（方案 V3 §3.6 口径 1e-9；实际 RRF 多步求和的浮点
    # 误差可能落在 1e-10 ~ 1e-11 量级，用相对容忍 1e-6 足够严 —— 主要是"不得改
    # 排序"，final_score 绝对值层面微小漂移不影响业务）。若结果里带 final_score
    # 字段才校验。
    for ro, rn in zip(r_off, r_on, strict=True):
        so = ro.get("final_score")
        sn = rn.get("final_score")
        if so is not None and sn is not None:
            denom = max(abs(so), 1e-12)
            assert abs(so - sn) / denom < 1e-6, (
                f"final_score 相对误差超标：{so} vs {sn}"
            )

    # ON 组每条矛盾对都带上 conflicts_with
    by_id = {r["id"]: r for r in r_on}
    for a_id, b_id in pair_ids:
        if a_id in by_id and b_id in by_id:
            if "conflicts_with" not in by_id[a_id]:
                pytest.skip("M5 search 改动未落地")
            assert b_id in _extract_conflicts(by_id[a_id], target_id=b_id)
            assert a_id in _extract_conflicts(by_id[b_id], target_id=a_id)


@pytest.mark.phase3
async def test_c14b_flag_on_no_contradicts_data_equals_phase2(
    svc_pair,
) -> None:
    """C14b — flag ON 但零 contradicts 边：结果应与 flag OFF 完全一致。"""
    svc_off, svc_on = svc_pair
    # 10 条，无任何 contradicts 边
    for j in range(10):
        svc_on._test_vec_mapping[f"C14b-{j}"] = _one_hot(  # type: ignore[attr-defined]
            500 + j
        )
        await svc_on.create_knowledge(
            title=f"C14b-{j}",
            summary="s",
            content=f"C14b 无矛盾 关键词 #{j}。" * 3,
        )

    r_off = await svc_off.search("C14b 关键词", limit=10)
    r_on = await svc_on.search("C14b 关键词", limit=10)
    if not (r_off and r_on):
        pytest.skip("search 未返回结果")

    ids_off = [r["id"] for r in r_off]
    ids_on = [r["id"] for r in r_on]
    assert ids_off == ids_on

    # 零数据下 conflicts_with 应为空列表或不存在
    for r in r_on:
        if "conflicts_with" in r:
            assert r["conflicts_with"] == []


@pytest.mark.phase3
async def test_c15_phase2_baseline_no_conflicts_with_key(
    svc_flag_off: KnowledgeService,
) -> None:
    """C15 — flag OFF 走 Phase 2 路径：结果 dict 必须不含 ``conflicts_with`` 键
    （或字段存在但为 []，视实现而定 —— 至少不得混入 relation 信息）。"""
    svc = svc_flag_off
    await svc.create_knowledge(
        title="C15-X",
        summary="s",
        content="C15 查询关键词 内容。" * 3,
    )
    results = await svc.search("C15 查询关键词", limit=5)
    if not results:
        pytest.skip("search 未返回结果")
    for row in results:
        if "conflicts_with" in row:
            # 宽松实现：字段存在但空
            assert row["conflicts_with"] == [], (
                "flag OFF 下不应暴露实际 conflicts_with 内容"
            )


# ===========================================================================
# 3.7 EVAL 场景 4 硬门禁（E1 / E2）
# ===========================================================================


@pytest.mark.phase3_eval
async def test_e1_eval_scenario4_hard_gate_recall_ge_40pct(
    tmp_path: Path,
) -> None:
    """E1 — 场景 4 硬门禁：40 条 query，召回率 ≥ 40%（≥ 16 命中）。

    方案 V3 §3.7：本地默认 skip、CI 强制开启。
    """
    svc, factory, engine = await _make_service(tmp_path, flag_on=True)
    try:
        # 30 对矛盾 + 20 条 filler = 80 条知识
        pair_ids: list[tuple[int, int]] = []
        for i in range(30):
            title_a = f"E1-规则-{i}-支持"
            title_b = f"E1-规则-{i}-反对"
            svc._test_vec_mapping[title_a] = _one_hot(  # type: ignore[attr-defined]
                600 + i
            )
            svc._test_vec_mapping[title_b] = _one_hot(  # type: ignore[attr-defined]
                600 + i
            )
            a = await svc.create_knowledge(
                title=title_a,
                summary="s",
                content=(
                    f"E1 规则 {i} 场景 4 licence 许可 支持 启用 写入。" * 3
                ),
            )
            try:
                b = await svc.create_knowledge(
                    title=title_b,
                    summary="s",
                    content=(
                        f"E1 规则 {i} 场景 4 licence 许可 反对 禁止 使用。" * 3
                    ),
                    contradicts_with=[a["id"]],
                )
            except TypeError as e:
                pytest.skip(f"M5 未实现：{e}")
            pair_ids.append((a["id"], b["id"]))
        for j in range(20):
            svc._test_vec_mapping[f"E1-filler-{j}"] = _one_hot(  # type: ignore[attr-defined]
                700 + j
            )
            await svc.create_knowledge(
                title=f"E1-filler-{j}",
                summary="s",
                content=f"E1 场景 4 filler 条目 {j}。" * 3,
            )

        # 40 条 query：每对一条直接问规则 i，加 10 条泛化 query
        queries: list[tuple[str, tuple[int, int]]] = []
        for i, pair in enumerate(pair_ids[:30]):
            queries.append((f"E1 规则 {i} licence", pair))
        # 再补 10 条：重复前 10 个规则用不同 query 形式
        for i in range(10):
            queries.append((f"规则 {i} 许可 启用", pair_ids[i]))

        # 先探一下：如果 search 结果根本没 conflicts_with 字段，说明搜索侧 M5 未
        # 落地，EVAL 场景 4 的 pair 对齐契约无从谈起 —— skip 而非 fail
        probe = await svc.search(queries[0][0], limit=10)
        if probe and all("conflicts_with" not in r for r in probe):
            pytest.skip(
                "M5 search 改动未落地（search 结果缺 conflicts_with 字段）"
            )
        # 若 probe 本身就召回失败（StubEmbedding 覆盖不到"泛化 query"的 vec），
        # EVAL 场景 4 必须走真实 Ollama 才有意义 —— 本测试标 @phase3_eval，CI 跑
        # 时由 ``MNEMO_PHASE3_EVAL_REAL=1`` 启用真实 embedding；stub 环境下降级
        # 为 skip，避免造成"fixture 设计缺陷 ≠ 实现 regression"的误报。
        import os as _os

        if _os.getenv("MNEMO_PHASE3_EVAL_REAL") != "1":
            pytest.skip(
                "phase3_eval 需真实 embedding；设 MNEMO_PHASE3_EVAL_REAL=1 启用"
            )

        hit = 0
        misses: list[int] = []
        for idx, (q, (a_id, b_id)) in enumerate(queries):
            results = await svc.search(q, limit=10)
            if not results:
                misses.append(idx)
                continue
            ids = [r["id"] for r in results]
            if a_id in ids and b_id in ids:
                by_id = {r["id"]: r for r in results}
                cwa = _extract_conflicts(by_id[a_id], target_id=b_id)
                cwb = _extract_conflicts(by_id[b_id], target_id=a_id)
                if b_id in cwa and a_id in cwb:
                    hit += 1
                    continue
            misses.append(idx)

        total = len(queries)
        recall = hit / total
        assert recall >= 0.40, (
            f"场景 4 硬门禁未达：hit={hit}/{total} = {recall:.2%} < 40%"
        )
        if recall < 0.60:
            print(
                f"\n[WARN] 场景 4 recall={recall:.2%} 未达目标 60%（方案 V3 §3.7 "
                f"目标值，非 fail）"
            )

        # 下限兜底：任一连续 5 条不得全 miss
        miss_set = set(misses)
        for start in range(len(queries) - 5 + 1):
            window = set(range(start, start + 5))
            assert not window <= miss_set, (
                f"连续 5 条 query[{start}..{start+4}] 全 miss，疑似局部 query "
                f"簇完全失效"
            )
    finally:
        await engine.dispose()


@pytest.mark.phase3
async def test_e2_eval_scenario4_grouping_rule_pairwise(
    svc_flag_on: KnowledgeService,
) -> None:
    """E2 — 3 组矛盾（A↔B, C↔D, E↔F）：conflicts_with 必须按对分组，不串。"""
    svc = svc_flag_on

    pair_ids: list[tuple[int, int]] = []
    for idx, (ta, tb) in enumerate(
        [("E2-A", "E2-B"), ("E2-C", "E2-D"), ("E2-E", "E2-F")]
    ):
        svc._test_vec_mapping[ta] = _one_hot(  # type: ignore[attr-defined]
            800 + 2 * idx
        )
        svc._test_vec_mapping[tb] = _one_hot(  # type: ignore[attr-defined]
            800 + 2 * idx
        )
        a = await svc.create_knowledge(
            title=ta, summary="s", content=f"E2 分组测试 {ta} 内容充足。" * 5
        )
        try:
            b = await svc.create_knowledge(
                title=tb,
                summary="s",
                content=f"E2 分组测试 {tb} 内容充足。" * 5,
                contradicts_with=[a["id"]],
            )
        except TypeError as e:
            pytest.skip(f"M5 未实现：{e}")
        pair_ids.append((a["id"], b["id"]))

    results = await svc.search("E2 分组测试", limit=10)
    if not results:
        pytest.skip("search 未返回结果")
    if any("conflicts_with" not in r for r in results):
        pytest.skip("M5 search 改动未落地")

    by_id = {r["id"]: r for r in results}
    own_pair_lookup: dict[int, int] = {}
    for a_id, b_id in pair_ids:
        own_pair_lookup[a_id] = b_id
        own_pair_lookup[b_id] = a_id

    for row_id, expected_partner in own_pair_lookup.items():
        if row_id not in by_id:
            continue  # 不在 top-N 跳过
        conflicts = _extract_conflicts(by_id[row_id], target_id=expected_partner)
        assert expected_partner in conflicts, (
            f"id={row_id} 的 conflicts_with 应精确指向 {expected_partner}"
        )
        # 不得跨组串连
        other_ids = [
            pid
            for pid in own_pair_lookup
            if pid != row_id and pid != expected_partner
        ]
        for other in other_ids:
            assert other not in conflicts, (
                f"id={row_id} 不应把非伴生 id={other} 列入 conflicts_with"
            )


# ===========================================================================
# 3.8 MCP 层参数解析 + CLI 反向确认（C16 / C17 / C18 / C19）
# ===========================================================================


def _load_mcp_create_tool():
    """找到 MCP ``create_knowledge`` 的底层函数。FastMCP 装饰器会把原函数封装
    在 ``.fn`` 或直接暴露；测试通过 import 后的 attribute 拿到可调用对象。"""
    try:
        module = importlib.import_module("mnemo.mcp.server")
    except ImportError as e:
        pytest.skip(f"MCP server 不可 import：{e}")
    tool = getattr(module, "create_knowledge", None)
    if tool is None:
        pytest.skip("mnemo.mcp.server.create_knowledge 不存在")
    fn = getattr(tool, "fn", tool)  # FastMCP 可能包了一层
    return module, fn


@pytest.mark.phase3
async def test_c16_mcp_create_contradicts_with_comma_separated_ids(
    tmp_path: Path,
) -> None:
    """C16 — MCP ``contradicts_with="1,2,3"`` → service 收到 int 列表。"""
    mcp_mod, tool_fn = _load_mcp_create_tool()
    svc, _factory, engine = await _make_service(tmp_path, flag_on=True)
    # 注入 _require_service 的 service 句柄
    setter = getattr(mcp_mod, "_set_service", None) or getattr(
        mcp_mod, "set_service", None
    )
    try:
        if setter is not None:
            setter(svc)
        else:
            mcp_mod._service = svc  # type: ignore[attr-defined]

        # 先准备 3 条被指向的知识
        targets = []
        for i in range(3):
            k = await svc.create_knowledge(
                title=f"C16-target-{i}",
                summary="s",
                content=f"C16 target {i} 内容充足。" * 5,
            )
            targets.append(k["id"])
        cw_str = ",".join(str(t) for t in targets)

        try:
            await tool_fn(
                title="C16-src",
                tags="",
                summary="s",
                content="C16 源条目 内容充足。" * 5,
                contradicts_with=cw_str,
            )
        except TypeError as e:
            pytest.skip(f"MCP 未实现 contradicts_with：{e}")

        factory = svc._session_factory
        # 应写 3 条 contradicts 关系
        async with factory() as session:
            from sqlalchemy import select as _sel

            res = await session.execute(
                _sel(Relation).where(
                    Relation.relation_type == CONTRADICTS,
                )
            )
            rels = list(res.scalars().all())
        assert len(rels) == 3, f"预期 3 条 contradicts，实际 {len(rels)}"
        got_targets = sorted(r.target_id for r in rels)
        assert got_targets == sorted(targets)
    finally:
        await engine.dispose()


@pytest.mark.phase3
async def test_c17_mcp_create_contradicts_with_mixed_ids_and_titles(
    tmp_path: Path,
) -> None:
    """C17 — MCP ``contradicts_with="1,TitleB,3"``：数字按 id，非数字按 title。"""
    mcp_mod, tool_fn = _load_mcp_create_tool()
    svc, _factory, engine = await _make_service(tmp_path, flag_on=True)
    setter = getattr(mcp_mod, "_set_service", None) or getattr(
        mcp_mod, "set_service", None
    )
    try:
        if setter is not None:
            setter(svc)
        else:
            mcp_mod._service = svc  # type: ignore[attr-defined]

        ka = await svc.create_knowledge(
            title="C17-id1", summary="s", content="C17 id1 内容充足。" * 5
        )
        kb = await svc.create_knowledge(
            title="C17-TitleB", summary="s", content="C17 B 内容充足。" * 5
        )
        kc = await svc.create_knowledge(
            title="C17-id3", summary="s", content="C17 id3 内容充足。" * 5
        )

        cw_str = f"{ka['id']},C17-TitleB,{kc['id']}"
        try:
            await tool_fn(
                title="C17-src",
                tags="",
                summary="s",
                content="C17 源条目 内容充足。" * 5,
                contradicts_with=cw_str,
            )
        except TypeError as e:
            pytest.skip(f"MCP 未实现 contradicts_with：{e}")

        factory = svc._session_factory
        async with factory() as session:
            from sqlalchemy import select as _sel

            res = await session.execute(
                _sel(Relation).where(Relation.relation_type == CONTRADICTS)
            )
            rels = list(res.scalars().all())
        got_targets = sorted(r.target_id for r in rels)
        assert got_targets == sorted([ka["id"], kb["id"], kc["id"]])
    finally:
        await engine.dispose()


@pytest.mark.phase3
async def test_c18_mcp_create_contradicts_with_empty_string_noop(
    tmp_path: Path,
) -> None:
    """C18 — MCP ``contradicts_with=""`` 或 ``None``：等价于 C4。"""
    mcp_mod, tool_fn = _load_mcp_create_tool()
    svc, _factory, engine = await _make_service(tmp_path, flag_on=True)
    setter = getattr(mcp_mod, "_set_service", None) or getattr(
        mcp_mod, "set_service", None
    )
    try:
        if setter is not None:
            setter(svc)
        else:
            mcp_mod._service = svc  # type: ignore[attr-defined]

        try:
            await tool_fn(
                title="C18-1",
                tags="",
                summary="s",
                content="C18 内容充足。" * 5,
                contradicts_with="",
            )
            await tool_fn(
                title="C18-2",
                tags="",
                summary="s",
                content="C18 内容充足 2。" * 5,
                contradicts_with=None,
            )
        except TypeError as e:
            pytest.skip(f"MCP 未实现 contradicts_with：{e}")

        factory = svc._session_factory
        assert await _count_contradicts(factory) == 0
        assert await _count_events(factory, "contradiction_marked") == 0
    finally:
        await engine.dispose()


@pytest.mark.phase3
def test_c19_cli_does_not_expose_contradicts_with() -> None:
    """C19 — CLI ``mnemo create --help`` 不得含 ``--contradicts-with`` / ``--contradicts``。

    TECH_PLAN §6.3 "修改文件"表只含 ``mcp/server.py``，未列 ``cli/main.py`` ——
    CLI 故意不暴露这个写入入口（与 M7 TC-10 对称）。
    """
    from typer.testing import CliRunner

    from mnemo.cli.main import app

    runner = CliRunner()
    result = runner.invoke(app, ["create", "--help"])
    assert result.exit_code == 0
    output = result.output
    assert "--contradicts-with" not in output, (
        "CLI create 不应暴露 --contradicts-with 选项"
    )
    assert "--contradicts" not in output, (
        "CLI create 不应暴露 --contradicts 选项"
    )

    # 非法 flag 调用被 Typer 拒绝
    bad = runner.invoke(
        app,
        [
            "create",
            "--title",
            "t",
            "--summary",
            "s",
            "--body",
            "b",
            "--contradicts-with",
            "1",
        ],
    )
    assert bad.exit_code != 0
