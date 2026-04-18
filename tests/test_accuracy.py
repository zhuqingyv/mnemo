"""Accuracy scenario tests — drives 494 scenarios across 12 categories.

Imports fixtures from ``scenario_conftest`` explicitly (not a conftest.py) so
the scenario suite does not disturb the 138 existing unit tests.

Judgement rule (see TEST_CASE_DESIGN.md §1.1 / §7.1):
- single scenario PASS = all expected_hits present AND no expected_not_hits
- soft per-case: record pass/fail, never assert inside the parametrized body
- hard gate: aggregate pass rate >= 60% asserted in ``test_accuracy_gate``
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import pytest

from tests.scenario_conftest import (  # noqa: F401 — re-exported fixtures
    all_scenarios,
    scenario_service,
    scenario_stats,
)


# Session-wide results bucket: mutated by each parametrized case, drained by
# the aggregate gate. Keyed by category → list[dict].
_RESULTS: dict[str, list[dict[str, Any]]] = defaultdict(list)


def _flatten_scenarios() -> list[tuple[str, int, dict[str, Any]]]:
    """Load scenarios at collection time so pytest can parametrize them.

    Returns tuples of ``(category, index, scenario)``. Loading directly from
    disk (instead of via the session fixture) is necessary because
    ``parametrize`` runs at collection time, before fixtures are available.
    """
    import json
    from pathlib import Path

    scenarios_dir = (
        Path(__file__).resolve().parent / "fixtures" / "scenarios"
    )
    items: list[tuple[str, int, dict[str, Any]]] = []
    for path in sorted(scenarios_dir.glob("*_scenarios.json")):
        category = (
            path.stem[: -len("_scenarios")]
            if path.stem.endswith("_scenarios")
            else path.stem
        )
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            continue
        for idx, scenario in enumerate(data):
            if isinstance(scenario, dict):
                items.append((category, idx, scenario))
    return items


_ALL_SCENARIOS = _flatten_scenarios()


def _case_id(item: tuple[str, int, dict[str, Any]]) -> str:
    category, idx, scenario = item
    desc = scenario.get("description") or scenario.get("query") or ""
    # pytest ids should be short and ASCII-safe-ish; keep category + index
    # + truncated query so the report is readable.
    query = scenario.get("query", "")
    return f"{category}#{idx:03d}::{query[:30]}"


async def _dispatch(service, scenario: dict[str, Any]) -> list[dict[str, Any]]:
    qt = scenario.get("query_type", "search")
    q = scenario.get("query", "")
    scope = scenario.get("scope")
    if qt == "search":
        return await service.search(q, scope=scope, limit=20)
    if qt == "tag-search":
        return await service.search_by_tag([q], scope=scope, limit=50)
    if qt == "related":
        return await service.get_related(q, depth=2)
    raise ValueError(f"unknown query_type: {qt}")


async def _judge(service, scenario: dict[str, Any]) -> dict[str, Any]:
    try:
        results = await _dispatch(service, scenario)
    except Exception as exc:  # noqa: BLE001 — one bad scenario shouldn't abort
        return {
            "pass": False,
            "error": repr(exc),
            "expected_hits": scenario.get("expected_hits", []),
            "missing_titles": scenario.get("expected_hits", []),
            "unexpected_titles": [],
            "top5": [],
        }

    titles = [r.get("title") for r in results]
    expected = scenario.get("expected_hits", []) or []
    not_expected = scenario.get("expected_not_hits", []) or []

    missing = [t for t in expected if t not in titles]
    unexpected = [t for t in not_expected if t in titles]
    passed = not missing and not unexpected

    return {
        "pass": passed,
        "error": None,
        "expected_hits": expected,
        "missing_titles": missing,
        "unexpected_titles": unexpected,
        "top5": titles[:5],
    }


@pytest.mark.parametrize("item", _ALL_SCENARIOS, ids=_case_id)
async def test_accuracy_scenario(item, scenario_service):
    """Soft per-scenario test — always passes, records into _RESULTS."""
    category, idx, scenario = item
    outcome = await _judge(scenario_service, scenario)
    _RESULTS[category].append(
        {
            "idx": idx,
            "query": scenario.get("query"),
            "query_type": scenario.get("query_type"),
            "description": scenario.get("description"),
            "pass": outcome["pass"],
            "missing_titles": outcome["missing_titles"],
            "unexpected_titles": outcome["unexpected_titles"],
            "top5": outcome["top5"],
            "error": outcome["error"],
        }
    )
    # Soft: never assert here — per-case failures are expected and reported
    # in the aggregate gate below.
    assert True


def _summarize() -> tuple[int, int, dict[str, tuple[int, int]]]:
    """Return (total_pass, total, per_category {cat: (pass, total)})."""
    total = 0
    total_pass = 0
    per_cat: dict[str, tuple[int, int]] = {}
    for cat, rows in _RESULTS.items():
        cp = sum(1 for r in rows if r["pass"])
        per_cat[cat] = (cp, len(rows))
        total_pass += cp
        total += len(rows)
    return total_pass, total, per_cat


def test_accuracy_gate(scenario_stats):
    """Hard gate: aggregate pass rate across all 12 categories >= 60%.

    Must run AFTER all parametrized cases — relies on pytest's in-file order
    plus alphabetical: ``test_accuracy_gate`` > ``test_accuracy_scenario``.
    Since pytest preserves declaration order, keep this function below.
    """
    total_pass, total, per_cat = _summarize()
    assert total > 0, "no accuracy scenarios collected — fixture loading failed"

    rate = total_pass / total if total else 0.0

    # Print the category breakdown (visible with pytest -s).
    print("\n===== Accuracy report =====")
    if scenario_stats:
        print(
            f"Fixture load: entries={scenario_stats.get('entries')} "
            f"inserted={scenario_stats.get('inserted')} "
            f"skipped={scenario_stats.get('skipped')} "
            f"active={scenario_stats.get('active')} "
            f"relations={scenario_stats.get('relations')}"
        )
    print(f"{'Category':<28} {'Pass':>6} {'Total':>6} {'Rate':>7}")
    print("-" * 52)
    for cat in sorted(per_cat.keys()):
        cp, ct = per_cat[cat]
        pct = (cp / ct * 100) if ct else 0.0
        print(f"{cat:<28} {cp:>6} {ct:>6} {pct:>6.1f}%")
    print("-" * 52)
    print(f"{'OVERALL':<28} {total_pass:>6} {total:>6} {rate * 100:>6.1f}%")

    # Show a sample of failing cases per category for diagnostics.
    print("\n----- Sample failures (up to 3 per category) -----")
    any_failures = False
    for cat in sorted(_RESULTS.keys()):
        fails = [r for r in _RESULTS[cat] if not r["pass"]][:3]
        if not fails:
            continue
        any_failures = True
        print(f"[{cat}]")
        for r in fails:
            qtype = r.get("query_type") or "search"
            query = r.get("query") or ""
            err = r.get("error")
            if err:
                print(f"  - ({qtype}) {query!r} — ERROR: {err}")
                continue
            missing = r.get("missing_titles") or []
            unexpected = r.get("unexpected_titles") or []
            top5 = r.get("top5") or []
            print(
                f"  - ({qtype}) {query!r} missing={missing[:2]} "
                f"unexpected={unexpected[:2]} top5={top5}"
            )
    if not any_failures:
        print("(no failures)")
    print("=" * 52)

    assert rate >= 0.60, (
        f"accuracy gate failed: pass rate {rate:.1%} < 60% "
        f"(passed {total_pass}/{total})"
    )
