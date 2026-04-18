#!/usr/bin/env python3
"""Phase 2 baseline snapshot — freeze per-scenario pass/fail + aggregate metrics.

Reuses ``scripts.m3_gate_eval.build_hybrid_service`` so we hit the exact same
real-path evaluation as the M3/M4 gates: load 494 fixture scenarios, run
accuracy / Top-3 / negative / intelligence / EVAL E2E, record per-item outcomes,
and write ``tests/fixtures/baseline_phase2.json``.

Run:
    MNEMO_HYBRID=1 .venv/bin/python scripts/freeze_phase2_baseline.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from scripts.m3_gate_eval import (  # noqa: E402
    EVAL_CASES,
    INTELLIGENCE_CASES,
    NEGATIVE_CASES,
    _dispatch,
    _load_all_scenarios,
    _run_eval_case,
    _run_int_case,
    build_hybrid_service,
)

OUT_PATH = REPO_ROOT / "tests" / "fixtures" / "baseline_phase2.json"


def _scenario_id(cat: str, idx: int, sc: dict[str, Any]) -> str:
    """Stable id: category + index + short query hash.

    Scenarios don't carry explicit ids. Index alone is too brittle if someone
    reorders a file; include a hash of the query so the id survives shuffles.
    """
    q = sc.get("query")
    qt = sc.get("query_type", "search")
    q_str = json.dumps(q, ensure_ascii=False, sort_keys=True) if not isinstance(q, str) else q
    h = hashlib.sha1(f"{qt}|{q_str}".encode("utf-8")).hexdigest()[:8]
    return f"{cat}:{idx:03d}:{h}"


async def _eval_accuracy_detailed(
    service, scenarios: list[tuple[str, dict[str, Any]]]
) -> tuple[list[dict[str, Any]], int, int, dict[str, tuple[int, int]]]:
    """Per-scenario accuracy + top3 outcomes."""
    results: list[dict[str, Any]] = []
    acc_pass = 0
    top3_denom = 0
    top3_pass = 0
    per_cat = defaultdict(lambda: [0, 0])
    for idx, (cat, sc) in enumerate(scenarios):
        per_cat[cat][1] += 1
        sid = _scenario_id(cat, idx, sc)
        t0 = time.monotonic()
        error: str | None = None
        try:
            rows = await _dispatch(service, sc)
        except Exception as exc:
            rows = []
            error = f"{type(exc).__name__}: {exc}"
        latency_ms = (time.monotonic() - t0) * 1000.0
        titles = [r.get("title") for r in rows]
        expected = sc.get("expected_hits") or []
        not_expected = sc.get("expected_not_hits") or []
        missing = [t for t in expected if t not in titles]
        unexpected = [t for t in not_expected if t in titles]
        acc_ok = error is None and not missing and not unexpected
        if acc_ok:
            acc_pass += 1
            per_cat[cat][0] += 1

        top3_eligible = sc.get("query_type") == "search" and bool(expected)
        top3_ok: bool | None = None
        if top3_eligible:
            top3_denom += 1
            top3_titles = titles[:3]
            top3_ok = any(t in top3_titles for t in expected)
            if top3_ok:
                top3_pass += 1

        results.append(
            {
                "id": sid,
                "category": cat,
                "index": idx,
                "query_type": sc.get("query_type", "search"),
                "query": sc.get("query"),
                "scope": sc.get("scope"),
                "project_name": sc.get("project_name"),
                "expected_hits": expected,
                "expected_not_hits": not_expected,
                "observed_titles": titles[:10],
                "accuracy_pass": acc_ok,
                "accuracy_missing": missing,
                "accuracy_unexpected": unexpected,
                "top3_eligible": top3_eligible,
                "top3_pass": top3_ok,
                "latency_ms": round(latency_ms, 1),
                "error": error,
            }
        )
    per_cat_out = {k: (v[0], v[1]) for k, v in per_cat.items()}
    return results, acc_pass, top3_pass, per_cat_out, top3_denom


async def _eval_negative_detailed(service) -> tuple[list[dict[str, Any]], int]:
    out: list[dict[str, Any]] = []
    passed = 0
    for case in NEGATIVE_CASES:
        t0 = time.monotonic()
        rows = await service.search(
            case["query"],
            scope=case["scope"],
            project_name=case["project_name"],
            limit=20,
        )
        latency_ms = (time.monotonic() - t0) * 1000.0
        titles = [r.get("title", "") for r in rows]
        ok = True
        reasons: list[str] = []
        if case.get("max_results") is not None and len(rows) > case["max_results"]:
            ok = False
            reasons.append(f"got {len(rows)} results, max {case['max_results']}")
        for forbidden in case.get("forbidden", []):
            if forbidden in titles:
                ok = False
                reasons.append(f"forbidden title present: {forbidden}")
        for substr in case.get("forbidden_substrings", []):
            if any(substr in t for t in titles):
                ok = False
                reasons.append(f"forbidden substring present: {substr}")
        if ok:
            passed += 1
        out.append(
            {
                "id": case["id"],
                "query": case["query"],
                "scope": case["scope"],
                "project_name": case["project_name"],
                "n_results": len(rows),
                "top_titles": titles[:5],
                "pass": ok,
                "reasons": reasons,
                "latency_ms": round(latency_ms, 1),
            }
        )
    return out, passed


async def _eval_intelligence_detailed(service) -> tuple[list[dict[str, Any]], int, int]:
    latencies: list[float] = []
    out: list[dict[str, Any]] = []
    for cid, q, qt, sc, exp, flag in INTELLIGENCE_CASES:
        t0 = time.monotonic()
        ok = await _run_int_case(cid, q, qt, sc, exp, flag, service, latencies)
        latency_ms = (time.monotonic() - t0) * 1000.0
        out.append(
            {
                "id": cid,
                "query": q if isinstance(q, str) else list(q),
                "query_type": qt,
                "scope": sc,
                "flag": flag,
                "pass": bool(ok),
                "hypothesis": flag == "hypothesis",
                "latency_ms": round(latency_ms, 1),
            }
        )
    denom = [r for r in out if not r["hypothesis"]]
    passed = sum(1 for r in denom if r["pass"])
    return out, passed, len(denom)


async def _eval_eval_e2e_detailed(
    service,
) -> tuple[list[dict[str, Any]], int, int, int, int]:
    latencies: list[float] = []
    out: list[dict[str, Any]] = []
    passed = skipped = 0
    for case in EVAL_CASES:
        t0 = time.monotonic()
        status, titles = await _run_eval_case(case, service, latencies)
        latency_ms = (time.monotonic() - t0) * 1000.0
        if status == "SKIP":
            skipped += 1
        elif status == "PASS":
            passed += 1
        out.append(
            {
                "id": case["id"],
                "group": case.get("group"),
                "query": case["query"],
                "type": case["type"],
                "scope": case.get("scope"),
                "status": status,
                "observed_titles": titles[:5],
                "latency_ms": round(latency_ms, 1),
            }
        )
    total = len(EVAL_CASES)
    effective = total - skipped
    return out, passed, effective, skipped, total


def _pct(p: int, t: int) -> float:
    return round(100.0 * p / t, 1) if t else 0.0


async def main() -> int:
    if os.environ.get("MNEMO_HYBRID") != "1":
        print("ERROR: MNEMO_HYBRID=1 must be set.")
        return 2

    t_start = time.time()
    service, engine, _config = await build_hybrid_service()
    scenarios = _load_all_scenarios()
    print(f"\nLoaded {len(scenarios)} scenarios")

    try:
        print("\n[1/4] accuracy + top3 (per-scenario)...")
        scen_rows, acc_pass, top3_pass, per_cat, top3_total = (
            await _eval_accuracy_detailed(service, scenarios)
        )
        acc_total = len(scen_rows)

        print("[2/4] negative...")
        neg_rows, neg_pass = await _eval_negative_detailed(service)
        neg_total = len(NEGATIVE_CASES)

        print("[3/4] intelligence...")
        int_rows, int_pass, int_total = await _eval_intelligence_detailed(service)

        print("[4/4] eval e2e...")
        ev_rows, ev_pass, ev_effective, ev_skipped, ev_total = (
            await _eval_eval_e2e_detailed(service)
        )
    finally:
        await engine.dispose()

    summary = {
        "accuracy": {
            "pass": acc_pass,
            "total": acc_total,
            "pct": _pct(acc_pass, acc_total),
        },
        "top3": {
            "pass": top3_pass,
            "total": top3_total,
            "pct": _pct(top3_pass, top3_total),
        },
        "negative": {
            "pass": neg_pass,
            "total": neg_total,
            "pct": _pct(neg_pass, neg_total),
        },
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
    }

    per_cat_out = {
        cat: {"pass": p, "total": t, "pct": _pct(p, t)}
        for cat, (p, t) in sorted(per_cat.items())
    }

    elapsed = time.time() - t_start
    payload = {
        "phase": "phase2",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "generator": "scripts/freeze_phase2_baseline.py",
        "env": {
            "MNEMO_HYBRID": os.environ.get("MNEMO_HYBRID"),
            "python": sys.version.split()[0],
        },
        "fixture_counts": {
            "scenarios": len(scenarios),
            "negative_cases": neg_total,
            "intelligence_cases": len(INTELLIGENCE_CASES),
            "eval_cases": ev_total,
        },
        "summary": summary,
        "per_category_accuracy": per_cat_out,
        "scenarios": scen_rows,
        "negative": neg_rows,
        "intelligence": int_rows,
        "eval_e2e": ev_rows,
        "runtime_seconds": round(elapsed, 1),
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 60)
    print("Phase 2 baseline snapshot")
    print("=" * 60)
    print(f"  accuracy    : {acc_pass}/{acc_total} = {summary['accuracy']['pct']}%")
    print(f"  intelligence: {int_pass}/{int_total} = {summary['intelligence']['pct']}%")
    print(f"  top3        : {top3_pass}/{top3_total} = {summary['top3']['pct']}%")
    print(f"  negative    : {neg_pass}/{neg_total} = {summary['negative']['pct']}%")
    print(
        f"  eval_e2e    : {ev_pass}/{ev_effective} = {summary['eval_e2e']['pct']}% "
        f"(skipped {ev_skipped}/{ev_total})"
    )
    print(f"  elapsed     : {elapsed:.1f}s")
    print(f"\nWrote {OUT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
