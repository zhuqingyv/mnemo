"""Phase 3 M7 — 上下文感知排序（context-aware rank）测试实现。

覆盖 ``docs/phase3/test_design_M7_context_aware.md`` v3 全部 14 条 TC：

- TC-01~04 + TC-03b：5 枚举 × 正向 boost 验证（coding / decision / debug / onboarding）
- TC-05：general + 不传 context + flag off 三路等价 Phase 2
- TC-06：非法 task_context 退化 general + WARNING 日志
- TC-07：flag off 跳过 boost（等价 Phase 2）
- TC-08：flag on/off/on 幂等
- TC-09：MCP server.search 接收 context 参数（task 生效，project/recent_titles 为保留字段）
- TC-10：CLI 不暴露 task_context
- TC-11：冒烟版 494 fixture 等价（flag on + 不传 context 与 flag off 逐条字节一致）
- TC-12：10 query × 多 context → top-5 差异率均值 ≥ 40%，每对 ≥ 20%

设计原则（test_design §0.3）：
- **乘性叠加语义纯净**：M7 只在 rerank 链末段乘一个 ``claim_type_boost`` 乘子，
  不改其他信号相对位序
- **flag off = Phase 2 M4 等价**：任何能退化到 ``boost={}`` 的路径都必须字节一致
- **封闭枚举防御**：非法 ``task_context`` 不抛异常，退化到 ``general`` 并写 WARNING
- **不 mock DB**：真实 SQLite + StubEmbedding（``tests/test_vector_search.py`` 已有）
- **config 非 magic number**：boost 断言一律读 ``config.task_context_boosts[...]``

功能未实现时用 ``importlib`` 防御式加载 + ``pytest.skip`` —— 测试框架不阻塞
实现节奏，实现合入同一 PR 去掉 skip 路径。
"""

from __future__ import annotations

import importlib
import inspect
import logging
import math
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from mnemo.config import MnemoConfig
from mnemo.models.knowledge import Knowledge, Relation
from mnemo.ranking.rerank import apply_rerank
from mnemo.services.knowledge_service import KnowledgeService

from tests.test_vector_search import EMBEDDING_DIM, StubEmbedding, _build_engine


pytestmark = pytest.mark.phase3


# ---------------------------------------------------------------------------
# Contract constants — read from config so magic numbers don't leak into tests
# ---------------------------------------------------------------------------


def _fresh_config(**overrides: Any) -> MnemoConfig:
    """Build a deterministic MnemoConfig with optional overrides.

    ``_env_file=None`` keeps a user's shell env from polluting defaults. Every
    test gets its own instance — flags never leak across tests.
    """
    cfg = MnemoConfig(_env_file=None)  # type: ignore[call-arg]
    for name, value in overrides.items():
        object.__setattr__(cfg, name, value)
    return cfg


# ---------------------------------------------------------------------------
# Defensive import — rerank must accept ``claim_type_boost`` after M7 lands
# ---------------------------------------------------------------------------


def _skip_unless_rerank_accepts_claim_type_boost() -> None:
    sig = inspect.signature(apply_rerank)
    if "claim_type_boost" not in sig.parameters:
        pytest.skip(
            "M7 未实现：apply_rerank 不接受 claim_type_boost —— 见 TECH_PLAN §7.2.1"
        )


def _skip_unless_search_accepts_task_context(service: KnowledgeService) -> None:
    sig = inspect.signature(service.search)
    if "task_context" not in sig.parameters:
        pytest.skip(
            "M7 未实现：KnowledgeService.search 不接受 task_context —— 见 TECH_PLAN §7.2.1"
        )


def _skip_unless_mcp_search_accepts_context() -> None:
    try:
        server = importlib.import_module("mnemo.mcp.server")
    except ImportError as e:  # pragma: no cover - always present
        pytest.skip(f"MCP server 模块无法加载: {e}")
    sig = inspect.signature(server.search)
    if "context" not in sig.parameters:
        pytest.skip(
            "M7 未实现：MCP server.search 不接受 context 参数 —— 见 TECH_PLAN §7.2.1"
        )


# ---------------------------------------------------------------------------
# Rerank fixture helper — build a candidate list that only varies in claim_type
# ---------------------------------------------------------------------------


def _fused(kid: int, claim_type: str, *, rrf: float = 0.02) -> dict[str, Any]:
    """Construct a fused entry that neutralizes every other signal:

    - authority 0 → a_mult = 1 + alpha * 0 = 1.0
    - no contradicts (caller may override via relation lookup)
    - status='active' → stale_mult = 1.0
    - freshness_mult pre-seeded to 1.0
    - source='both' → not vec_only, no gate kicks in
    """
    return {
        "id": kid,
        "rrf_score": rrf,
        "fts_rank": 1,
        "vec_rank": 1,
        "source": "both",
        "claim_type": claim_type,
        "status": "active",
        "freshness_mult": 1.0,
    }


def _neutral_rerank(
    fused: list[dict[str, Any]],
    *,
    config: MnemoConfig,
    task_context: str | None,
    contradicts_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Invoke apply_rerank with all non-boost signals neutralized.

    After M7 lands, rerank accepts ``claim_type_boost``: a ``dict`` keyed by
    ``claim_type`` (and the special key ``"contradicts_edge"`` for the debug
    context). When ``task_context`` is None / ``'general'`` / flag off, the
    caller passes ``claim_type_boost=None`` → neutral 1.0 multiplier.
    """
    _skip_unless_rerank_accepts_claim_type_boost()

    if not config.context_aware_rank_enabled or task_context in (None, "general"):
        boost: dict[str, float] | None = None
    else:
        raw = config.task_context_boosts.get(task_context)
        if raw is None:
            # Unknown enum → test harness mirrors the production defensive
            # fallback: degrade to general ({} = no-op).
            boost = {}
        else:
            boost = dict(raw)

    contradicts_ids = contradicts_ids or set()

    return apply_rerank(
        fused,
        authority_lookup=lambda _kid: 0.0,
        contradiction_lookup=lambda _kid: False,
        alpha=0.1,
        freshness_lookup=lambda _kid: 1.0,
        status_lookup=lambda _kid: "active",
        state_machine_enabled=False,  # silence stale path — isolate boost variable
        claim_type_boost=boost,
        contradicts_edge_lookup=lambda kid: kid in contradicts_ids,
    )


# ---------------------------------------------------------------------------
# TC-01  coding context boosts procedure 1.3×, fact 1.1×
# ---------------------------------------------------------------------------


def test_tc01_coding_context_boosts_procedure_over_fact() -> None:
    cfg = _fresh_config(context_aware_rank_enabled=True)
    boosts = cfg.task_context_boosts["coding"]

    fused = [
        _fused(1, claim_type="procedure"),
        _fused(2, claim_type="fact"),
    ]
    out = _neutral_rerank(fused, config=cfg, task_context="coding")
    by_id = {e["id"]: e for e in out}

    # procedure 必排 fact 之前
    assert [e["id"] for e in out] == [1, 2], (
        f"coding: procedure should rank above fact — got {[e['id'] for e in out]}"
    )

    # 数值精确 = base × config boost（不用 magic number）
    base = 0.02  # rrf × authority(1.0) × ... = rrf 本身
    assert math.isclose(
        by_id[1]["final_score"], base * boosts["procedure"], abs_tol=1e-9
    )
    assert math.isclose(
        by_id[2]["final_score"], base * boosts["fact"], abs_tol=1e-9
    )

    # 比值应精确等于 config 比值
    expected_ratio = boosts["procedure"] / boosts["fact"]
    actual_ratio = by_id[1]["final_score"] / by_id[2]["final_score"]
    assert math.isclose(actual_ratio, expected_ratio, abs_tol=1e-9)


# ---------------------------------------------------------------------------
# TC-02  decision context boosts decision 1.3×; undeclared claim_type → 1.0
# ---------------------------------------------------------------------------


def test_tc02_decision_context_boosts_decision_and_defaults_others_to_1() -> None:
    cfg = _fresh_config(context_aware_rank_enabled=True)
    boosts = cfg.task_context_boosts["decision"]

    fused = [
        _fused(1, claim_type="decision"),
        _fused(2, claim_type="procedure"),
        _fused(3, claim_type="hypothesis"),
        _fused(4, claim_type="fact"),
    ]
    out = _neutral_rerank(fused, config=cfg, task_context="decision")
    by_id = {e["id"]: e for e in out}
    base = 0.02

    # decision 最前；procedure/hypothesis 未在 decision 档位声明 → 1.0
    assert out[0]["id"] == 1  # decision
    assert math.isclose(by_id[1]["final_score"], base * boosts["decision"], abs_tol=1e-9)
    assert math.isclose(by_id[4]["final_score"], base * boosts["fact"], abs_tol=1e-9)
    assert math.isclose(by_id[2]["final_score"], base * 1.0, abs_tol=1e-9)
    assert math.isclose(by_id[3]["final_score"], base * 1.0, abs_tol=1e-9)


# ---------------------------------------------------------------------------
# TC-03  debug context: fact / procedure / contradicts_edge 叠加
# ---------------------------------------------------------------------------


def test_tc03_debug_context_stacks_claim_type_and_contradicts_edge() -> None:
    cfg = _fresh_config(context_aware_rank_enabled=True)
    boosts = cfg.task_context_boosts["debug"]

    fused = [
        _fused(1, claim_type="fact"),        # A: fact + contradicts 边
        _fused(2, claim_type="fact"),        # B: fact 无边
        _fused(3, claim_type="procedure"),   # C: procedure 无边
        _fused(4, claim_type="hypothesis"),  # D: hypothesis 无边
    ]
    out = _neutral_rerank(
        fused, config=cfg, task_context="debug", contradicts_ids={1}
    )
    by_id = {e["id"]: e for e in out}
    base = 0.02

    expected_a = base * boosts["fact"] * boosts["contradicts_edge"]
    expected_b = base * boosts["fact"]
    expected_c = base * boosts["procedure"]
    expected_d = base * 1.0

    assert math.isclose(by_id[1]["final_score"], expected_a, abs_tol=1e-9)
    assert math.isclose(by_id[2]["final_score"], expected_b, abs_tol=1e-9)
    assert math.isclose(by_id[3]["final_score"], expected_c, abs_tol=1e-9)
    assert math.isclose(by_id[4]["final_score"], expected_d, abs_tol=1e-9)

    # 排序：A 最前，D 最后；B / C 并列（boost["fact"]==boost["procedure"]==1.2）
    assert out[0]["id"] == 1
    assert out[-1]["id"] == 4

    # 警戒线（m5 review 2026-04-20）：A 不得超过 base × 2.0
    assert by_id[1]["final_score"] <= base * 2.0, (
        "debug boost 过度扰动：contradicts × claim_type 已突破 2× 上限"
    )


# ---------------------------------------------------------------------------
# TC-03b  contradicts_edge isolated (two fact rows) — debug 档位耦合验证
# ---------------------------------------------------------------------------


def test_tc03b_debug_boost_contradicts_edge_isolated() -> None:
    cfg = _fresh_config(context_aware_rank_enabled=True)
    boosts = cfg.task_context_boosts["debug"]

    fused = [
        _fused(1, claim_type="fact"),  # has contradicts edge
        _fused(2, claim_type="fact"),  # no contradicts edge
    ]
    out = _neutral_rerank(
        fused, config=cfg, task_context="debug", contradicts_ids={1}
    )
    by_id = {e["id"]: e for e in out}

    # A 严格高于 B；fact boost 两边等量抵消 → 比值 = contradicts_edge boost
    assert [e["id"] for e in out] == [1, 2]
    ratio = by_id[1]["final_score"] / by_id[2]["final_score"]
    assert math.isclose(ratio, boosts["contradicts_edge"], abs_tol=1e-9)


# ---------------------------------------------------------------------------
# TC-04  onboarding context: fact 1.2 / decision 1.1
# ---------------------------------------------------------------------------


def test_tc04_onboarding_context_boosts_fact_and_decision() -> None:
    cfg = _fresh_config(context_aware_rank_enabled=True)
    boosts = cfg.task_context_boosts["onboarding"]

    fused = [
        _fused(1, claim_type="fact"),
        _fused(2, claim_type="decision"),
        _fused(3, claim_type="procedure"),
        _fused(4, claim_type="hypothesis"),
    ]
    out = _neutral_rerank(fused, config=cfg, task_context="onboarding")
    by_id = {e["id"]: e for e in out}
    base = 0.02

    # fact > decision > procedure ≈ hypothesis
    assert out[0]["id"] == 1
    assert out[1]["id"] == 2
    assert math.isclose(by_id[1]["final_score"], base * boosts["fact"], abs_tol=1e-9)
    assert math.isclose(by_id[2]["final_score"], base * boosts["decision"], abs_tol=1e-9)
    assert math.isclose(by_id[3]["final_score"], base * 1.0, abs_tol=1e-9)
    assert math.isclose(by_id[4]["final_score"], base * 1.0, abs_tol=1e-9)


# ---------------------------------------------------------------------------
# TC-05  general / context=None / flag off — three-way Phase 2 equivalence
# ---------------------------------------------------------------------------


def test_tc05_general_and_none_and_flag_off_are_phase2_equivalent() -> None:
    cfg_on_general = _fresh_config(context_aware_rank_enabled=True)
    cfg_on_none = _fresh_config(context_aware_rank_enabled=True)
    cfg_off = _fresh_config(context_aware_rank_enabled=False)

    fused = [
        _fused(1, claim_type="fact"),
        _fused(2, claim_type="procedure"),
        _fused(3, claim_type="decision"),
        _fused(4, claim_type="hypothesis"),
    ]

    a = _neutral_rerank(fused, config=cfg_on_general, task_context="general")
    b = _neutral_rerank(fused, config=cfg_on_none, task_context=None)
    c = _neutral_rerank(fused, config=cfg_off, task_context="coding")  # flag off wins

    # top-N id 列表完全一致
    ids_a = [e["id"] for e in a]
    assert ids_a == [e["id"] for e in b]
    assert ids_a == [e["id"] for e in c]

    # final_score 相对误差 ≤ 1e-9（浮点 ULP 容忍；m5 review）
    for x, y, z in zip(a, b, c, strict=True):
        assert math.isclose(x["final_score"], y["final_score"], abs_tol=1e-9)
        assert math.isclose(x["final_score"], z["final_score"], abs_tol=1e-9)


# ---------------------------------------------------------------------------
# TC-06  非法 task_context → general 退化 + WARNING 日志
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_value", ["random_garbage_value", "CODING", ""]
)
@pytest.mark.asyncio
async def test_tc06_unknown_task_context_falls_back_with_warning(
    tmp_path: Path, bad_value: str, caplog: pytest.LogCaptureFixture
) -> None:
    """search(task_context=非法值) 必须：① 不抛异常 ② 等价 general ③ 写 WARNING。"""
    db_path = tmp_path / f"tc06_{bad_value or 'empty'}.db"
    engine = await _build_engine(db_path)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    cfg = _fresh_config(context_aware_rank_enabled=True)
    service = KnowledgeService(
        session_factory=factory, embedding_service=StubEmbedding(), config=cfg
    )
    _skip_unless_search_accepts_task_context(service)

    try:
        await service.create_knowledge(
            title=f"tc06 {bad_value or 'empty'} sample",
            summary="s",
            content="tc06 content for defensive enum test." * 3,
            claim_type="fact",
        )

        with caplog.at_level(logging.WARNING):
            # 非法值 —— 不应抛
            hits_bad = await service.search(
                "tc06", mode="fts", task_context=bad_value
            )

        # 对照 general —— 结果必须一致（top-N id 列表）
        hits_general = await service.search(
            "tc06", mode="fts", task_context="general"
        )
        assert [h["id"] for h in hits_bad] == [h["id"] for h in hits_general]

        # 至少一条 WARNING 提及枚举集合
        warnings = [
            rec
            for rec in caplog.records
            if rec.levelno == logging.WARNING and "not in enum" in rec.getMessage()
        ]
        assert warnings, (
            "预期 WARNING 日志含 'not in enum'，未发现 —— "
            f"records={[(r.levelname, r.getMessage()) for r in caplog.records]}"
        )
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# TC-07  flag off 跳过 boost —— 行为中性
# ---------------------------------------------------------------------------


def test_tc07_flag_off_skips_boost_computation() -> None:
    cfg_off = _fresh_config(context_aware_rank_enabled=False)
    cfg_on_general = _fresh_config(context_aware_rank_enabled=True)

    fused = [
        _fused(1, claim_type="procedure"),
        _fused(2, claim_type="fact"),
        _fused(3, claim_type="hypothesis"),
    ]
    # Flag off + task_context="coding" 应完全等价 flag on + general
    out_off = _neutral_rerank(fused, config=cfg_off, task_context="coding")
    out_ref = _neutral_rerank(fused, config=cfg_on_general, task_context="general")

    assert [e["id"] for e in out_off] == [e["id"] for e in out_ref]
    for x, y in zip(out_off, out_ref, strict=True):
        assert math.isclose(x["final_score"], y["final_score"], abs_tol=1e-9)

    # Flag off 下 final_score 必须退化到 base（rrf × 1.0 × ... × 1.0）
    for entry in out_off:
        assert math.isclose(entry["final_score"], 0.02, abs_tol=1e-9)


# ---------------------------------------------------------------------------
# TC-08  flag on/off/on 幂等切换
# ---------------------------------------------------------------------------


def test_tc08_flag_idempotent_on_off_on() -> None:
    """同进程内翻 flag 三次，每次都符合各自语义，无残留状态。"""
    fused = [
        _fused(1, claim_type="procedure"),
        _fused(2, claim_type="fact"),
    ]

    # on
    cfg_on_1 = _fresh_config(context_aware_rank_enabled=True)
    out1 = _neutral_rerank(fused, config=cfg_on_1, task_context="coding")
    base = 0.02
    b_coding = cfg_on_1.task_context_boosts["coding"]
    assert math.isclose(
        {e["id"]: e for e in out1}[1]["final_score"],
        base * b_coding["procedure"],
        abs_tol=1e-9,
    )

    # off — 立即中性
    cfg_off = _fresh_config(context_aware_rank_enabled=False)
    out2 = _neutral_rerank(fused, config=cfg_off, task_context="coding")
    for entry in out2:
        assert math.isclose(entry["final_score"], base, abs_tol=1e-9)

    # on again — 应与首次 on 字节一致
    cfg_on_2 = _fresh_config(context_aware_rank_enabled=True)
    out3 = _neutral_rerank(fused, config=cfg_on_2, task_context="coding")
    for a, b in zip(out1, out3, strict=True):
        assert math.isclose(a["final_score"], b["final_score"], abs_tol=1e-9)
        assert a["id"] == b["id"]


# ---------------------------------------------------------------------------
# TC-09  MCP server.search 接受 context 参数
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def mcp_service(tmp_path: Path) -> AsyncIterator[KnowledgeService]:
    db_path = tmp_path / "mcp.db"
    engine = await _build_engine(db_path)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    cfg = _fresh_config(context_aware_rank_enabled=True)
    svc = KnowledgeService(
        session_factory=factory, embedding_service=StubEmbedding(), config=cfg
    )
    try:
        yield svc
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_tc09_mcp_search_accepts_context_param(
    mcp_service: KnowledgeService,
) -> None:
    """MCP ``search`` 接受 ``context: dict | None``。4 种输入：
    (a) 不传 / (b) {task:coding} / (c) 含保留字段 / (d) 仅 project。

    其中 (a)(d) 等价 general；(b)(c) 等价 task="coding"。
    """
    _skip_unless_mcp_search_accepts_context()

    from mnemo.mcp import server as mcp_server

    mcp_server.set_service(mcp_service)
    # seed 一批数据
    for ct in ("procedure", "fact", "decision", "hypothesis"):
        await mcp_service.create_knowledge(
            title=f"tc09 {ct}",
            summary="s",
            content=f"tc09 {ct} content padding " * 5,
            claim_type=ct,
        )

    r_no_ctx = await mcp_server.search(query="tc09")
    r_coding = await mcp_server.search(query="tc09", context={"task": "coding"})
    r_reserved = await mcp_server.search(
        query="tc09",
        context={
            "task": "coding",
            "project": "mnemo",
            "recent_titles": ["t1", "t2"],
        },
    )
    r_project_only = await mcp_server.search(query="tc09", context={"project": "mnemo"})

    # 契约 1：均为 str（MCP 返回 markdown）；不抛 Pydantic 错
    for r in (r_no_ctx, r_coding, r_reserved, r_project_only):
        assert isinstance(r, str) and len(r) > 0

    # 契约 2：task=coding 生效，其 markdown 与 r_coding 完全一致（保留字段 Phase 3 不参与计算）
    assert r_coding == r_reserved, (
        "context.project / recent_titles 是 Phase 3 保留字段，不应影响结果"
    )

    # 契约 3：未传 task / 仅 project → 等价 general → 与 r_no_ctx 一致
    assert r_no_ctx == r_project_only


# ---------------------------------------------------------------------------
# TC-10  CLI 不暴露 task_context
# ---------------------------------------------------------------------------


def test_tc10_cli_does_not_expose_context_param() -> None:
    """`mnemo search` 不得暴露 --task-context / --context（product §3.6 协议层）。"""
    from typer.testing import CliRunner

    from mnemo.cli.main import app

    runner = CliRunner()
    result_help = runner.invoke(app, ["search", "--help"])
    assert result_help.exit_code == 0
    help_text = result_help.output.lower()
    assert "--task-context" not in help_text, (
        f"CLI 不应暴露 --task-context，发现 help 里出现：{help_text}"
    )
    assert "--context" not in help_text, (
        f"CLI 不应暴露 --context，发现 help 里出现：{help_text}"
    )

    # 非法 flag 调用 → Typer 拒绝（exit != 0）
    result_bad = runner.invoke(app, ["search", "query-x", "--task-context", "coding"])
    assert result_bad.exit_code != 0


# ---------------------------------------------------------------------------
# TC-11  冒烟版 "494 fixture" 等价：flag on + 不传 context ≡ flag off
# ---------------------------------------------------------------------------
# 说明：完整 494 fixture 逐条字节一致由 scripts/phase3_regression_gate.py
# 在 `@pytest.mark.phase3_fixture` 级别触发；本冒烟版在测试进程内构造一批
# 混合 claim_type 的候选，验证 flag on（不传 context）与 flag off 逐条 ==。


@pytest.mark.phase3_fixture
def test_tc11_flag_on_without_context_equals_flag_off() -> None:
    cfg_off = _fresh_config(context_aware_rank_enabled=False)
    cfg_on = _fresh_config(context_aware_rank_enabled=True)

    # 跨 claim_type × 不同 rrf 的混合候选集（模拟 fixture）
    fused = [
        _fused(i, claim_type=ct, rrf=0.0150 + i * 0.0005)
        for i, ct in enumerate(
            [
                "fact",
                "procedure",
                "decision",
                "hypothesis",
                "fact",
                "procedure",
                "decision",
                "hypothesis",
            ],
            start=1,
        )
    ]

    out_off = _neutral_rerank(fused, config=cfg_off, task_context=None)
    out_on_no_ctx = _neutral_rerank(fused, config=cfg_on, task_context=None)

    # 逐条字节一致
    assert [e["id"] for e in out_off] == [e["id"] for e in out_on_no_ctx]
    for x, y in zip(out_off, out_on_no_ctx, strict=True):
        assert math.isclose(x["final_score"], y["final_score"], abs_tol=1e-9)
        assert x["id"] == y["id"]


# ---------------------------------------------------------------------------
# TC-12  10 query × 5 context → top-5 差异率均值 ≥ 40%，每对 ≥ 20%
# ---------------------------------------------------------------------------


CONTEXTS = ("coding", "decision", "debug", "onboarding", "general")


def _top5_jaccard_diff(a: list[int], b: list[int]) -> float:
    """对称差 / 并集 —— 两个 id 列表的差异率。"""
    sa, sb = set(a), set(b)
    union = sa | sb
    if not union:
        return 0.0
    return len(sa ^ sb) / len(union)


def _build_fixture_pool(seed: int) -> list[dict[str, Any]]:
    """构造 ≥ 20 条跨 claim_type 分布的候选池。

    RRF 分数差异有限（0.015~0.025），留出足够空间让 context boost 翻转排名。
    """
    import random

    rng = random.Random(seed)
    claim_types = ["fact", "procedure", "decision", "hypothesis"]
    pool: list[dict[str, Any]] = []
    for i in range(20):
        ct = claim_types[i % 4]
        pool.append(_fused(i + 1, claim_type=ct, rrf=0.015 + rng.uniform(0, 0.01)))
    return pool


def test_tc12_10_queries_different_context_differ_rate_ge_40pct() -> None:
    cfg = _fresh_config(context_aware_rank_enabled=True)

    per_query_pair_rates: list[list[float]] = []  # 10 query × 10 对差异率
    for q in range(10):
        pool = _build_fixture_pool(seed=1000 + q)
        # 部分条目带 contradicts 边（给 debug 档位独立贡献）
        contradicts_ids = {pool[0]["id"], pool[5]["id"]}

        top5_by_ctx: dict[str, list[int]] = {}
        for ctx in CONTEXTS:
            out = _neutral_rerank(
                pool, config=cfg, task_context=ctx, contradicts_ids=contradicts_ids
            )
            top5_by_ctx[ctx] = [e["id"] for e in out[:5]]

        # 同一 context 重复调用 → 结果必须完全一致（确定性）
        out_again = _neutral_rerank(
            pool, config=cfg, task_context="coding", contradicts_ids=contradicts_ids
        )
        assert [e["id"] for e in out_again[:5]] == top5_by_ctx["coding"]

        # C(5,2) = 10 对
        pair_rates: list[float] = []
        for i in range(len(CONTEXTS)):
            for j in range(i + 1, len(CONTEXTS)):
                rate = _top5_jaccard_diff(
                    top5_by_ctx[CONTEXTS[i]], top5_by_ctx[CONTEXTS[j]]
                )
                pair_rates.append(rate)
        per_query_pair_rates.append(pair_rates)

    # 全局均值 ≥ 40%（product §3.6 硬指标）
    all_rates = [r for per_q in per_query_pair_rates for r in per_q]
    mean_rate = sum(all_rates) / len(all_rates)
    assert mean_rate >= 0.40, (
        f"差异率均值 {mean_rate:.3f} < 40% —— boost 未显著改变排序"
    )

    # 每对（跨所有 query）均值 ≥ 20%（m5 review 防单对退化）
    num_pairs = 10
    per_pair_means = [
        sum(per_q[p] for per_q in per_query_pair_rates) / len(per_query_pair_rates)
        for p in range(num_pairs)
    ]
    min_pair = min(per_pair_means)
    assert min_pair >= 0.20, (
        f"存在 context 对差异率均值 {min_pair:.3f} < 20% —— "
        f"per_pair_means={per_pair_means}"
    )
