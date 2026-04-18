#!/usr/bin/env python3
"""Phase 3 automatic regression gate — run after every commit.

Loads the Phase 2 baseline snapshot (``tests/fixtures/baseline_phase2.json``),
re-runs the full 494-scenario real-path evaluation, and enforces three hard
rules:

  1. per-item one-way gate: any baseline-pass scenario that now fails is FAIL
     (applies to the 494 accuracy scenarios, 10 negative cases, 19 non-hypothesis
     intelligence cases, and every eval_e2e case that previously PASSed).
  2. aggregate drop thresholds:
        accuracy     >= baseline - 0.5pp  (Phase 2: 79.6% → gate 79.1%)
        intelligence >= baseline - 0.5pp  (Phase 2: 89.5% → gate 89.0%)
        top3         >= baseline - 0.5pp  (Phase 2: 90.0% → gate 89.5%)
        negative     >= baseline - 0.5pp  (Phase 2: 90.0% → gate 89.5%)
        eval_e2e     >= 95%
  3. with ``--ablation``: same two rules enforced across every 1-of-4 and
     2-of-4 Phase 3 feature-flag combination (10 combos total). Flags that
     have not landed on ``MnemoConfig`` yet are reported as NOT_LANDED and
     do not fail the gate — the script ships before the features.

Exit codes:
    0  — PASS (all enforced rules satisfied)
    1  — FAIL (at least one rule violated)
    2  — setup error (MNEMO_HYBRID unset, baseline missing, Ollama down, ...)

Usage:
    MNEMO_HYBRID=1 .venv/bin/python scripts/phase3_regression_gate.py
    MNEMO_HYBRID=1 .venv/bin/python scripts/phase3_regression_gate.py --verbose
    MNEMO_HYBRID=1 .venv/bin/python scripts/phase3_regression_gate.py --ablation
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from contextlib import contextmanager
from itertools import combinations
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import text  # noqa: E402

from scripts.freeze_phase2_baseline import (  # noqa: E402
    _eval_accuracy_detailed,
    _eval_eval_e2e_detailed,
    _eval_intelligence_detailed,
    _eval_negative_detailed,
    _pct,
)
from scripts.m3_gate_eval import (  # noqa: E402
    NEGATIVE_CASES,
    _load_all_scenarios,
    build_hybrid_service,
)

BASELINE_PATH = REPO_ROOT / "tests" / "fixtures" / "baseline_phase2.json"

# Hard floors per team-lead rule 2. accuracy/intelligence/top3/negative are
# expressed as (baseline_pct - 0.5pp); eval_e2e is a flat 95%.
DROP_TOLERANCE_PP = 0.5
EVAL_E2E_FLOOR_PCT = 95.0

# The four P3a feature flags that gate ablation (team-lead rule 3).
# Kept local instead of importing from tests/conftest.py because this script
# runs without pytest and must not load the test config module.
ABLATION_FLAGS: tuple[str, ...] = (
    "write_gate_enabled",
    "freshness_enabled",
    "state_machine_enabled",
    "feedback_loop_enabled",
)


# --------------------------------------------------------------------------
# rule 1: per-item diff — baseline pass → current fail is FAIL
# --------------------------------------------------------------------------


def _diff_scenarios(
    baseline: list[dict[str, Any]],
    current: list[dict[str, Any]],
    *,
    pass_key: str,
    id_key: str = "id",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (regressions, recoveries) — both keyed by id.

    A regression is a baseline pass item that now fails. A recovery is a
    baseline fail item that now passes (informational only, never blocks).
    """
    cur_by_id = {r[id_key]: r for r in current}
    regressions: list[dict[str, Any]] = []
    recoveries: list[dict[str, Any]] = []
    for base_row in baseline:
        sid = base_row[id_key]
        base_pass = bool(base_row.get(pass_key))
        cur_row = cur_by_id.get(sid)
        if cur_row is None:
            # Item disappeared from the fixture set — treat as regression so
            # someone has to explain it. Better noisy than silent.
            if base_pass:
                regressions.append(
                    {
                        "id": sid,
                        "reason": "missing from current run",
                        "baseline": base_row,
                    }
                )
            continue
        cur_pass = bool(cur_row.get(pass_key))
        if base_pass and not cur_pass:
            regressions.append({"id": sid, "baseline": base_row, "current": cur_row})
        elif not base_pass and cur_pass:
            recoveries.append({"id": sid, "baseline": base_row, "current": cur_row})
    return regressions, recoveries


def _diff_eval_e2e(
    baseline: list[dict[str, Any]], current: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """eval_e2e stores ``status`` in {PASS, FAIL, SKIP}. Pass = status == PASS."""
    cur_by_id = {r["id"]: r for r in current}
    regressions: list[dict[str, Any]] = []
    recoveries: list[dict[str, Any]] = []
    for base_row in baseline:
        sid = base_row["id"]
        base_pass = base_row.get("status") == "PASS"
        cur_row = cur_by_id.get(sid)
        if cur_row is None:
            if base_pass:
                regressions.append(
                    {"id": sid, "reason": "missing", "baseline": base_row}
                )
            continue
        cur_pass = cur_row.get("status") == "PASS"
        if base_pass and not cur_pass:
            regressions.append({"id": sid, "baseline": base_row, "current": cur_row})
        elif not base_pass and cur_pass:
            recoveries.append({"id": sid, "baseline": base_row, "current": cur_row})
    return regressions, recoveries


# --------------------------------------------------------------------------
# rule 2: aggregate drop check
# --------------------------------------------------------------------------


def _check_aggregate(
    metric: str, baseline_pct: float, current_pct: float, floor_pct: float
) -> tuple[bool, str]:
    """Return (ok, human-readable line)."""
    delta = round(current_pct - baseline_pct, 2)
    ok = current_pct >= floor_pct - 1e-9
    tag = "OK" if ok else "FAIL"
    return ok, (
        f"  {metric:<12s} baseline={baseline_pct:>5.1f}% "
        f"current={current_pct:>5.1f}% delta={delta:>+5.2f}pp "
        f"floor={floor_pct:.1f}% [{tag}]"
    )


# --------------------------------------------------------------------------
# evaluate one configuration (no flag overrides = baseline config)
# --------------------------------------------------------------------------


async def _evaluate_once(service, scenarios) -> dict[str, Any]:
    """Run the full 4-part eval on an already-built service and return a
    payload shaped like baseline_phase2.json's top level (minus metadata).

    Does NOT build a new service — caller owns service lifecycle so we can
    reuse a single loaded DB across ablation combos.
    """
    scen_rows, acc_pass, top3_pass, per_cat, top3_total = (
        await _eval_accuracy_detailed(service, scenarios)
    )
    acc_total = len(scen_rows)

    neg_rows, neg_pass = await _eval_negative_detailed(service)
    neg_total = len(NEGATIVE_CASES)

    int_rows, int_pass, int_total = await _eval_intelligence_detailed(service)

    ev_rows, ev_pass, ev_effective, ev_skipped, ev_total = (
        await _eval_eval_e2e_detailed(service)
    )

    return {
        "summary": {
            "accuracy": {"pass": acc_pass, "total": acc_total, "pct": _pct(acc_pass, acc_total)},
            "top3": {"pass": top3_pass, "total": top3_total, "pct": _pct(top3_pass, top3_total)},
            "negative": {"pass": neg_pass, "total": neg_total, "pct": _pct(neg_pass, neg_total)},
            "intelligence": {
                "pass": int_pass,
                "total": int_total,
                "pct": _pct(int_pass, int_total),
            },
            "eval_e2e": {
                "pass": ev_pass,
                "effective": ev_effective,
                "skipped": ev_skipped,
                "total": ev_total,
                "pct": _pct(ev_pass, ev_effective),
            },
        },
        "scenarios": scen_rows,
        "negative": neg_rows,
        "intelligence": int_rows,
        "eval_e2e": ev_rows,
    }


# --------------------------------------------------------------------------
# rule 1 + rule 2 applied to one (baseline, current) pair
# --------------------------------------------------------------------------


def _judge(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    label: str,
    verbose: bool,
) -> tuple[bool, list[str]]:
    """Return (passed, log_lines). Logs are printed by the caller so they can
    be nested under an ablation-combo header.
    """
    lines: list[str] = [f"[{label}] judging current run vs baseline..."]

    # ---------- rule 1: per-item one-way gate ----------
    scen_reg, scen_rec = _diff_scenarios(
        baseline["scenarios"], current["scenarios"], pass_key="accuracy_pass"
    )
    top3_base = [s for s in baseline["scenarios"] if s.get("top3_eligible")]
    top3_reg, top3_rec = _diff_scenarios(top3_base, current["scenarios"], pass_key="top3_pass")
    neg_reg, neg_rec = _diff_scenarios(
        baseline["negative"], current["negative"], pass_key="pass"
    )
    # Hypothesis intelligence rows are excluded from pass-rate denominator in
    # baseline; apply the same filter here so recoveries/regressions on
    # hypothesis cases never count.
    int_base = [r for r in baseline["intelligence"] if not r.get("hypothesis")]
    int_reg, int_rec = _diff_scenarios(int_base, current["intelligence"], pass_key="pass")
    ev_reg, ev_rec = _diff_eval_e2e(baseline["eval_e2e"], current["eval_e2e"])

    regression_groups: list[tuple[str, list[dict[str, Any]]]] = [
        ("accuracy", scen_reg),
        ("top3", top3_reg),
        ("negative", neg_reg),
        ("intelligence", int_reg),
        ("eval_e2e", ev_reg),
    ]
    rule1_ok = all(len(regs) == 0 for _, regs in regression_groups)

    total_regressions = sum(len(regs) for _, regs in regression_groups)
    total_recoveries = (
        len(scen_rec) + len(top3_rec) + len(neg_rec) + len(int_rec) + len(ev_rec)
    )
    lines.append(
        f"  rule 1 (per-item one-way gate): regressions={total_regressions} "
        f"recoveries={total_recoveries} -> {'PASS' if rule1_ok else 'FAIL'}"
    )
    if not rule1_ok or verbose:
        for group, regs in regression_groups:
            if not regs:
                continue
            lines.append(f"    [{group}] {len(regs)} regression(s):")
            for r in regs[: (None if verbose else 20)]:
                lines.append(f"      - {r['id']}")
            if not verbose and len(regs) > 20:
                lines.append(f"      ... and {len(regs) - 20} more (use --verbose)")
    if verbose and total_recoveries > 0:
        lines.append(f"    recoveries (informational, do not affect gate):")
        for group, recs in [
            ("accuracy", scen_rec),
            ("top3", top3_rec),
            ("negative", neg_rec),
            ("intelligence", int_rec),
            ("eval_e2e", ev_rec),
        ]:
            for r in recs:
                lines.append(f"      + [{group}] {r['id']}")

    # ---------- rule 2: aggregate drop floors ----------
    b = baseline["summary"]
    c = current["summary"]
    checks: list[tuple[bool, str]] = []
    checks.append(
        _check_aggregate(
            "accuracy", b["accuracy"]["pct"], c["accuracy"]["pct"],
            b["accuracy"]["pct"] - DROP_TOLERANCE_PP,
        )
    )
    checks.append(
        _check_aggregate(
            "intelligence", b["intelligence"]["pct"], c["intelligence"]["pct"],
            b["intelligence"]["pct"] - DROP_TOLERANCE_PP,
        )
    )
    checks.append(
        _check_aggregate(
            "top3", b["top3"]["pct"], c["top3"]["pct"],
            b["top3"]["pct"] - DROP_TOLERANCE_PP,
        )
    )
    checks.append(
        _check_aggregate(
            "negative", b["negative"]["pct"], c["negative"]["pct"],
            b["negative"]["pct"] - DROP_TOLERANCE_PP,
        )
    )
    checks.append(
        _check_aggregate(
            "eval_e2e", b["eval_e2e"]["pct"], c["eval_e2e"]["pct"],
            EVAL_E2E_FLOOR_PCT,
        )
    )
    rule2_ok = all(ok for ok, _ in checks)
    lines.append(
        f"  rule 2 (aggregate drop floors): -> {'PASS' if rule2_ok else 'FAIL'}"
    )
    for _, line in checks:
        lines.append(line)

    return (rule1_ok and rule2_ok), lines


# --------------------------------------------------------------------------
# ablation helpers
# --------------------------------------------------------------------------


def _ablation_combos() -> list[tuple[str, ...]]:
    """C(4,1) + C(4,2) = 4 + 6 = 10 combinations of flags to turn OFF.

    An empty combo means "default config" and is handled separately as the
    baseline run, so it's not included here.
    """
    out: list[tuple[str, ...]] = []
    for size in (1, 2):
        out.extend(tuple(c) for c in combinations(ABLATION_FLAGS, size))
    return out


@contextmanager
def _toggle_flags_off(config, flags: tuple[str, ...]) -> Iterator[list[str]]:
    """Turn the listed flags OFF on ``config`` for the duration of the block.

    Returns the list of flags that were actually applied (landed on the
    pydantic model); the rest are reported back so the caller can record
    them as NOT_LANDED in the output without failing the gate.
    """
    model_fields = set(type(config).model_fields)
    applied: list[str] = []
    originals: dict[str, Any] = {}
    for name in flags:
        if name not in model_fields:
            continue
        originals[name] = getattr(config, name)
        setattr(config, name, False)
        applied.append(name)
    try:
        yield applied
    finally:
        for name, prev in originals.items():
            setattr(config, name, prev)


# --------------------------------------------------------------------------
# rule 4: orphan detection — FTS / vec rows whose knowledge row is gone
# --------------------------------------------------------------------------


async def _check_orphans(engine) -> tuple[bool, list[str]]:
    """Return (ok, log_lines). Any orphan row fails the gate.

    A FTS or knowledge_vec row whose rowid does not map back to a live
    ``knowledge`` row means a delete path skipped the secondary-index
    cleanup. Left alone, the next insert collides on rowid and
    create_knowledge raises. Guard it here so no commit can ship with
    dangling index rows.
    """
    lines: list[str] = ["[orphan] scanning knowledge_fts / knowledge_vec..."]
    async with engine.connect() as conn:
        fts_count = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM knowledge_fts f "
                    "LEFT JOIN knowledge k ON k.id = f.rowid "
                    "WHERE k.id IS NULL"
                )
            )
        ).scalar_one()
        vec_count = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM knowledge_vec v "
                    "LEFT JOIN knowledge k ON k.id = v.rowid "
                    "WHERE k.id IS NULL"
                )
            )
        ).scalar_one()
    ok = fts_count == 0 and vec_count == 0
    lines.append(
        f"  knowledge_fts orphans={fts_count} "
        f"knowledge_vec orphans={vec_count} -> "
        f"{'PASS' if ok else 'FAIL'}"
    )
    return ok, lines


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Phase 3 automatic regression gate (vs Phase 2 baseline)."
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="print every regression and every recovery, not just counts",
    )
    p.add_argument(
        "--ablation",
        action="store_true",
        help=(
            "in addition to the default run, sweep C(4,1)+C(4,2)=10 "
            "Phase-3 feature-flag OFF combinations and enforce rules 1+2 "
            "on each. Combos that reference flags not yet declared on "
            "MnemoConfig are reported as NOT_LANDED (non-blocking)."
        ),
    )
    return p.parse_args()


def _load_baseline() -> dict[str, Any]:
    if not BASELINE_PATH.exists():
        print(f"ERROR: baseline not found at {BASELINE_PATH}", file=sys.stderr)
        print(
            "       run scripts/freeze_phase2_baseline.py first.",
            file=sys.stderr,
        )
        sys.exit(2)
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


async def _amain(args: argparse.Namespace) -> int:
    if os.environ.get("MNEMO_HYBRID") != "1":
        print("ERROR: MNEMO_HYBRID=1 must be set.", file=sys.stderr)
        return 2

    baseline = _load_baseline()
    print(
        f"Loaded baseline: phase={baseline['phase']} "
        f"generated_at={baseline['generated_at']} "
        f"scenarios={baseline['fixture_counts']['scenarios']}"
    )

    t_build = time.time()
    service, engine, config = await build_hybrid_service()
    scenarios = _load_all_scenarios()
    print(
        f"Built service in {time.time() - t_build:.1f}s, "
        f"loaded {len(scenarios)} scenarios"
    )

    overall_fail = False
    try:
        # ---- default-config run (always executed) ----
        t_run = time.time()
        print("\n" + "=" * 60)
        print("default-config run (all Phase 3 flags at their defaults)")
        print("=" * 60)
        current = await _evaluate_once(service, scenarios)
        print(f"  evaluated in {time.time() - t_run:.1f}s")
        passed, lines = _judge(baseline, current, label="default", verbose=args.verbose)
        for ln in lines:
            print(ln)
        if not passed:
            overall_fail = True

        # rule 4: orphan scan — must run against the engine that just served
        # the eval so we catch any index leak produced by the run itself.
        orphan_ok, orphan_lines = await _check_orphans(engine)
        for ln in orphan_lines:
            print(ln)
        if not orphan_ok:
            overall_fail = True

        # ---- ablation sweep ----
        if args.ablation:
            combos = _ablation_combos()
            print("\n" + "=" * 60)
            print(f"ablation sweep: {len(combos)} flag-OFF combinations")
            print("=" * 60)
            model_fields = set(type(config).model_fields)
            not_landed_combos: list[tuple[str, ...]] = []
            for idx, combo in enumerate(combos, 1):
                missing = [f for f in combo if f not in model_fields]
                if missing:
                    not_landed_combos.append(combo)
                    print(
                        f"\n[ablation {idx}/{len(combos)}] OFF={combo} "
                        f"-> NOT_LANDED (missing from MnemoConfig: {missing}); "
                        "skipping re-run"
                    )
                    continue
                print(
                    f"\n[ablation {idx}/{len(combos)}] OFF={combo}"
                )
                t_abl = time.time()
                with _toggle_flags_off(config, combo) as applied:
                    assert applied == list(combo)
                    abl_current = await _evaluate_once(service, scenarios)
                print(f"  evaluated in {time.time() - t_abl:.1f}s")
                label = "abl:" + ",".join(combo)
                abl_ok, abl_lines = _judge(
                    baseline, abl_current, label=label, verbose=args.verbose
                )
                for ln in abl_lines:
                    print(ln)
                if not abl_ok:
                    overall_fail = True
            if not_landed_combos:
                print(
                    f"\nNote: {len(not_landed_combos)}/{len(combos)} ablation "
                    "combos skipped because Phase 3 flags have not landed on "
                    "MnemoConfig yet. These will activate automatically once "
                    "the flags are declared."
                )
    finally:
        await engine.dispose()

    print("\n" + "=" * 60)
    print("OVERALL: " + ("FAIL" if overall_fail else "PASS"))
    print("=" * 60)
    return 1 if overall_fail else 0


def main() -> int:
    args = _parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
