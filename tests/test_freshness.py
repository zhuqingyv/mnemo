"""Freshness time-decay unit tests — Phase 3 功能 2 (tech_research.md §4).

Test-first spec for ``mnemo.ranking.freshness`` (module to be implemented
under P3a-M2). Pure math + rerank integration — no DB, no mocks.

Contract under test:

    freshness = exp(-λ × age_days)
    final = rrf × (1 + α·authority) × (β + (1-β)·freshness) × ...

- λ by claim_type:  fact 0.003 / decision 0.007 / procedure 0.015 / hypothesis 0.02
- β floor:          0.3  (最老条目不会掉到 0)
- feature flag:     freshness_enabled; off → freshness 恒 1.0 (中性乘子)
- 入参非法 (负 age / 未知 claim_type) 由实现选择：本测试假定未知 claim_type
  退化为 ``fact`` 分档 (最保守衰减)；负 age 视为 0。

整个文件打 ``@pytest.mark.phase3``：在 ``ranking/freshness.py`` 落地前可用
``pytest -m 'not phase3'`` 跳过，不阻塞 Phase 2 回归门禁。
"""

from __future__ import annotations

import math

import pytest

pytestmark = pytest.mark.phase3


# ---------------------------------------------------------------------------
# Contract constants (tech_research.md §4.2, §11)
# ---------------------------------------------------------------------------

LAMBDA_BY_CLAIM = {
    "fact": 0.003,
    "decision": 0.007,
    "procedure": 0.015,
    "hypothesis": 0.02,
}
HALF_LIFE_DAYS = {ct: math.log(2) / lam for ct, lam in LAMBDA_BY_CLAIM.items()}
BETA = 0.3


def _import_freshness():
    """Lazy import — keeps pytest collection from erroring until module exists."""
    from mnemo.ranking import freshness  # noqa: WPS433

    return freshness


# ---------------------------------------------------------------------------
# 1. age=0 边界：任何 claim_type 的 freshness 都等于 1.0
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("claim_type", list(LAMBDA_BY_CLAIM))
def test_age_zero_returns_one(claim_type: str):
    fr = _import_freshness()
    assert math.isclose(fr.freshness_multiplier(0.0, claim_type), 1.0, abs_tol=1e-9)


# ---------------------------------------------------------------------------
# 2. 半衰期验证：每个 claim_type 在 age = ln(2)/λ 处衰减到 0.5
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("claim_type", list(LAMBDA_BY_CLAIM))
def test_half_life_yields_half(claim_type: str):
    fr = _import_freshness()
    half_life = HALF_LIFE_DAYS[claim_type]
    val = fr.freshness_multiplier(half_life, claim_type)
    assert math.isclose(val, 0.5, abs_tol=1e-6), (
        f"{claim_type}: expected 0.5 at {half_life:.1f}d, got {val}"
    )


# ---------------------------------------------------------------------------
# 3. 相对衰减速度：procedure 衰减必须比 fact 快（30 天同 age 比较）
# ---------------------------------------------------------------------------


def test_procedure_decays_faster_than_fact_at_same_age():
    fr = _import_freshness()
    age = 30.0
    f_fact = fr.freshness_multiplier(age, "fact")
    f_proc = fr.freshness_multiplier(age, "procedure")
    assert f_fact > f_proc, f"fact={f_fact} should > procedure={f_proc} at {age}d"


def test_hypothesis_decays_faster_than_decision():
    fr = _import_freshness()
    age = 30.0
    f_dec = fr.freshness_multiplier(age, "decision")
    f_hyp = fr.freshness_multiplier(age, "hypothesis")
    assert f_dec > f_hyp


# ---------------------------------------------------------------------------
# 4. 数学精度：fact λ=0.003, age=100 → exp(-0.3) ≈ 0.7408
# ---------------------------------------------------------------------------


def test_fact_freshness_100_days_matches_exp_formula():
    fr = _import_freshness()
    val = fr.freshness_multiplier(100.0, "fact")
    expected = math.exp(-0.003 * 100.0)
    assert math.isclose(val, expected, rel_tol=1e-6)


def test_procedure_freshness_365_days_matches_exp_formula():
    fr = _import_freshness()
    val = fr.freshness_multiplier(365.0, "procedure")
    expected = math.exp(-0.015 * 365.0)  # ≈ 0.00416
    assert math.isclose(val, expected, rel_tol=1e-6)


# ---------------------------------------------------------------------------
# 5. β 保底：rerank 乘子 = β + (1-β)·freshness，最老条目 → β
# ---------------------------------------------------------------------------


def test_rerank_floor_multiplier_bottoms_at_beta():
    fr = _import_freshness()
    # age 非常大 → freshness → 0 → 乘子 → β
    mult = fr.rerank_freshness_multiplier(age_days=10_000.0, claim_type="hypothesis", beta=BETA)
    assert math.isclose(mult, BETA, abs_tol=1e-6)


def test_rerank_multiplier_at_age_zero_is_one():
    fr = _import_freshness()
    mult = fr.rerank_freshness_multiplier(age_days=0.0, claim_type="fact", beta=BETA)
    assert math.isclose(mult, 1.0, abs_tol=1e-9)


def test_rerank_multiplier_interpolates_between_beta_and_one():
    fr = _import_freshness()
    # 半衰期处 freshness=0.5 → 乘子 = 0.3 + 0.7·0.5 = 0.65
    half_life = HALF_LIFE_DAYS["decision"]
    mult = fr.rerank_freshness_multiplier(age_days=half_life, claim_type="decision", beta=BETA)
    assert math.isclose(mult, BETA + (1 - BETA) * 0.5, abs_tol=1e-6)


# ---------------------------------------------------------------------------
# 6. 与 rerank 集成：freshness 可以翻转相邻排名
# ---------------------------------------------------------------------------


def test_freshness_flips_adjacent_rrf_ranks():
    """rrf 小但 freshness 新的条目应压过 rrf 大但 freshness 旧的条目，
    当两者的 rrf 差距 < freshness 差距时。"""
    fr = _import_freshness()
    from mnemo.ranking.rerank import apply_rerank

    # id=1 rrf=0.0154, age=365d fact → fresh ≈ 0.4066 → mult ≈ 0.3 + 0.7·0.4066 ≈ 0.5846
    # id=2 rrf=0.0150, age=0d any   → fresh = 1.0    → mult = 1.0
    # final(1) = 0.0154 × 0.5846 ≈ 0.00900
    # final(2) = 0.0150 × 1.0    = 0.01500 → 2 胜
    fused = [
        {"id": 1, "rrf_score": 0.0154, "fts_rank": 1, "vec_rank": 1, "source": "both"},
        {"id": 2, "rrf_score": 0.0150, "fts_rank": 2, "vec_rank": 2, "source": "both"},
    ]
    freshness_lookup = {
        1: fr.rerank_freshness_multiplier(365.0, "fact", BETA),
        2: fr.rerank_freshness_multiplier(0.0, "fact", BETA),
    }
    out = apply_rerank(
        fused,
        authority_lookup=lambda _kid: 0.0,
        contradiction_lookup=lambda _kid: False,
        freshness_lookup=freshness_lookup.get,
    )
    assert [e["id"] for e in out] == [2, 1]
    assert math.isclose(out[0]["final_score"], 0.0150 * 1.0)


# ---------------------------------------------------------------------------
# 7. feature flag off → freshness 乘子恒为 1.0，排序回归 Phase 2
# ---------------------------------------------------------------------------


def test_feature_flag_off_yields_neutral_multiplier_from_rerank():
    """freshness_lookup=None（未传）→ rerank 不应用 freshness → 行为与 Phase 2 一致。"""
    from mnemo.ranking.rerank import apply_rerank

    fused = [
        {"id": 1, "rrf_score": 0.0154, "fts_rank": 1, "vec_rank": 1, "source": "both"},
        {"id": 2, "rrf_score": 0.0150, "fts_rank": 2, "vec_rank": 2, "source": "both"},
    ]
    out = apply_rerank(
        fused,
        authority_lookup=lambda _kid: 0.0,
        contradiction_lookup=lambda _kid: False,
        freshness_lookup=None,
    )
    # 未应用 freshness → final == rrf_score → 排序不变
    assert [e["id"] for e in out] == [1, 2]
    for entry in out:
        assert math.isclose(entry["final_score"], entry["rrf_score"])


def test_feature_flag_off_at_module_returns_one(monkeypatch):
    """config.freshness_enabled=False 时，freshness_multiplier_for 助手
    （封装了 flag 读取）必须返回 1.0，即使 age 和 claim_type 正常。"""
    fr = _import_freshness()

    monkeypatch.setenv("MNEMO_FRESHNESS_ENABLED", "false")
    from mnemo.config import MnemoConfig

    cfg = MnemoConfig()
    assert cfg.freshness_enabled is False

    val = fr.freshness_multiplier_for(age_days=100.0, claim_type="fact", config=cfg)
    assert val == 1.0


# ---------------------------------------------------------------------------
# 8. 边界/防御：负 age 视为 0；未知 claim_type 退化为 fact
# ---------------------------------------------------------------------------


def test_negative_age_treated_as_zero():
    fr = _import_freshness()
    # 时钟偏差或并发写入可能让 age_days 短暂为负。不应崩溃。
    val = fr.freshness_multiplier(-5.0, "fact")
    assert math.isclose(val, 1.0, abs_tol=1e-9)


def test_unknown_claim_type_falls_back_to_fact():
    fr = _import_freshness()
    val_unknown = fr.freshness_multiplier(100.0, "mystery_type")
    val_fact = fr.freshness_multiplier(100.0, "fact")
    assert math.isclose(val_unknown, val_fact, rel_tol=1e-9)


def test_none_claim_type_falls_back_to_fact():
    """claim_type 可为 None（Knowledge.claim_type 是 nullable）— 必须安全。"""
    fr = _import_freshness()
    val_none = fr.freshness_multiplier(100.0, None)
    val_fact = fr.freshness_multiplier(100.0, "fact")
    assert math.isclose(val_none, val_fact, rel_tol=1e-9)


# ---------------------------------------------------------------------------
# 9. 乘性叠加：freshness × authority × contradiction 量纲自洽
# ---------------------------------------------------------------------------


def test_freshness_stacks_multiplicatively_with_authority_and_contradiction():
    fr = _import_freshness()
    from mnemo.ranking.rerank import apply_rerank, CONTRADICTION_PENALTY

    fused = [{"id": 1, "rrf_score": 0.02, "fts_rank": 1, "vec_rank": 1, "source": "both"}]
    # authority=2 → a_mult = 1.2
    # contradiction=True → 0.7
    # age=half_life(decision) → freshness=0.5 → mult = 0.65
    # final = 0.02 * 1.2 * 0.7 * 0.65 = 0.01092
    fresh_mult = fr.rerank_freshness_multiplier(HALF_LIFE_DAYS["decision"], "decision", BETA)
    out = apply_rerank(
        fused,
        authority_lookup=lambda _kid: 2.0,
        contradiction_lookup=lambda _kid: True,
        alpha=0.1,
        freshness_lookup=lambda _kid: fresh_mult,
    )
    expected = 0.02 * 1.2 * CONTRADICTION_PENALTY * 0.65
    assert math.isclose(out[0]["final_score"], expected, rel_tol=1e-6)
    assert math.isclose(out[0]["freshness_mult"], 0.65, rel_tol=1e-6)


# ---------------------------------------------------------------------------
# 10. 配置驱动：freshness_lambda_by_claim_type / freshness_floor_beta 可调
# ---------------------------------------------------------------------------


def test_config_overrides_lambda_and_beta(monkeypatch):
    """运行时可通过 MnemoConfig 覆盖 λ 和 β，不用改代码。"""
    fr = _import_freshness()
    from mnemo.config import MnemoConfig

    cfg = MnemoConfig()
    # 默认值与 tech_research §4.2 对齐
    assert cfg.freshness_lambda_by_claim_type["fact"] == 0.003
    assert cfg.freshness_lambda_by_claim_type["decision"] == 0.007
    assert cfg.freshness_lambda_by_claim_type["procedure"] == 0.015
    assert cfg.freshness_lambda_by_claim_type["hypothesis"] == 0.02
    assert math.isclose(cfg.freshness_floor_beta, 0.3, abs_tol=1e-9)

    # 实现必须从 config 读取 λ / β，而不是硬编码
    val = fr.freshness_multiplier_for(age_days=100.0, claim_type="fact", config=cfg)
    assert math.isclose(val, math.exp(-0.003 * 100.0), rel_tol=1e-6)
