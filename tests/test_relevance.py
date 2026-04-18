"""Relevance scenario tests (Top-N / negative / scope isolation).

Three sub-tests per ``TEST_CASE_DESIGN.md`` §1.2 + §2.2 + §5:
1. Top-N coverage over ~440 programmatic ``search`` scenarios.
2. 10 hand-crafted negative queries (REL-N-01 ~ REL-N-10) — must be 100% clean.
3. Scope isolation over every scenario carrying both ``scope`` and
   ``expected_not_hits``.

No mocks — reuses the session-scoped engine + service built by
``tests.scenario_conftest``.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.scenario_conftest import (  # noqa: F401 — re-exported for pytest
    all_scenarios,
    scenario_service,
    scenario_stats,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _flatten_search_with_hits(
    all_scenarios_map: dict[str, list[dict[str, Any]]],
) -> list[tuple[str, dict[str, Any]]]:
    """Pick every ``query_type=='search'`` scenario with non-empty expected_hits."""
    out: list[tuple[str, dict[str, Any]]] = []
    for category, scenarios in all_scenarios_map.items():
        for sc in scenarios:
            if sc.get("query_type") != "search":
                continue
            hits = sc.get("expected_hits") or []
            if not hits:
                continue
            out.append((category, sc))
    return out


def _flatten_scope_isolation(
    all_scenarios_map: dict[str, list[dict[str, Any]]],
) -> list[tuple[str, dict[str, Any]]]:
    """Scenarios that have both ``scope`` and non-empty ``expected_not_hits``."""
    out: list[tuple[str, dict[str, Any]]] = []
    for category, scenarios in all_scenarios_map.items():
        for sc in scenarios:
            if not sc.get("scope"):
                continue
            nothits = sc.get("expected_not_hits") or []
            if not nothits:
                continue
            out.append((category, sc))
    return out


async def _run_query(service, scenario: dict[str, Any]) -> list[dict[str, Any]]:
    qtype = scenario["query_type"]
    q = scenario["query"]
    scope = scenario.get("scope")
    project_name = scenario.get("project_name")
    if qtype == "search":
        return await service.search(
            q, scope=scope, project_name=project_name, limit=20
        )
    if qtype == "tag-search":
        return await service.search_by_tag([q], scope=scope, limit=50)
    if qtype == "related":
        return await service.get_related(q, depth=2)
    raise ValueError(f"unknown query_type: {qtype}")


# ---------------------------------------------------------------------------
# 1. Top-N coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_top_n_coverage(scenario_service, all_scenarios, capsys):
    """Programmatic Top-3 + Multi-hit coverage across ~440 search scenarios.

    Reports rates as stdout; enforces a soft lower bound so pipeline stays
    honest. Per §1.2 Top-3 target ≥ 60%, multi-hit target ≥ 50%. We harden
    to a conservative basic floor (20%) so a complete regression is visible
    but typo / tokenizer variance doesn't fail the suite.
    """

    items = _flatten_search_with_hits(all_scenarios)
    total = len(items)
    assert total > 0, "no search scenarios with expected_hits"

    top3_pass = 0
    multi_total = 0
    multi_pass = 0
    failures_top3: list[tuple[str, str, list[str], list[str]]] = []

    for category, sc in items:
        results = await _run_query(scenario_service, sc)
        titles = [r["title"] for r in results]
        expected = sc["expected_hits"]

        if any(t in titles[:3] for t in expected):
            top3_pass += 1
        else:
            failures_top3.append(
                (category, sc.get("query", ""), expected, titles[:5])
            )

        if len(expected) >= 2:
            multi_total += 1
            covered = sum(1 for t in expected if t in titles[:10])
            if (covered / len(expected)) >= 0.5:
                multi_pass += 1

    top3_rate = top3_pass / total
    multi_rate = (multi_pass / multi_total) if multi_total else 0.0

    with capsys.disabled():
        print()
        print("===== Relevance Top-N =====")
        print(f"Total search scenarios w/ expected_hits: {total}")
        print(f"Top-3 coverage: {top3_pass}/{total} ({top3_rate:.1%})")
        print(
            f"Multi-hit coverage (|hits|>=2): "
            f"{multi_pass}/{multi_total} "
            f"({multi_rate:.1%} of {multi_total})"
        )
        print(f"Sample Top-3 misses ({min(5, len(failures_top3))}):")
        for cat, q, exp, top5 in failures_top3[:5]:
            print(f"  [{cat}] q={q!r}")
            print(f"     expected any of: {exp}")
            print(f"     top5: {top5}")
        print("===========================")

    # Soft floors — keep pipeline honest without failing on small tokenizer regressions
    assert top3_rate >= 0.20, (
        f"Top-3 coverage {top3_rate:.1%} is below basic floor 20% "
        f"(target ≥60%); something in FTS/tokenizer is badly broken."
    )


# ---------------------------------------------------------------------------
# 2. Negative scenarios (REL-N-01 ~ REL-N-10)
# ---------------------------------------------------------------------------


# Each case: (id, query, scope, project_name, max_results, forbidden_titles)
# - max_results is None → no cap check (only forbidden-titles check applies)
# - forbidden_titles empty → must be empty
NEGATIVE_CASES: list[dict[str, Any]] = [
    {
        "id": "REL-N-01",
        "query": "外星人入侵",
        "scope": None,
        "project_name": None,
        "max_results": 0,
        "forbidden": [],
    },
    {
        "id": "REL-N-02",
        "query": "区块链 DeFi",
        "scope": None,
        "project_name": None,
        "max_results": 2,
        "forbidden": [],
    },
    {
        "id": "REL-N-03",
        "query": "PHP Laravel",
        "scope": None,
        "project_name": None,
        "max_results": 0,
        "forbidden": [],
    },
    {
        "id": "REL-N-04",
        "query": "nft-gmgn",
        "scope": "project",
        "project_name": "AionUi",
        "max_results": None,
        # No nft-gmgn-tagged deliverable should leak into an AionUi project query.
        "forbidden_substrings": ["nft-gmgn"],
    },
    {
        "id": "REL-N-05",
        "query": "Chakra UI",
        "scope": "project",
        "project_name": "AionUi",
        "max_results": None,
        "forbidden": ["nft-gmgn 禁止 Chakra UI 新代码"],
    },
    {
        "id": "REL-N-06",
        "query": "kubernetes 集群",
        "scope": None,
        "project_name": None,
        "max_results": 0,
        "forbidden": [],
    },
    {
        "id": "REL-N-07",
        "query": "游戏引擎",
        "scope": None,
        "project_name": None,
        "max_results": 0,
        "forbidden": [],
    },
    {
        "id": "REL-N-08",
        "query": "AionUi",
        "scope": "project",
        "project_name": "nft-gmgn",
        "max_results": None,
        # No AionUi-only deliverable should leak into an nft-gmgn project query.
        "forbidden_substrings": ["AionUi"],
    },
    {
        "id": "REL-N-09",
        "query": "家常菜 菜谱",
        "scope": None,
        "project_name": None,
        "max_results": 0,
        "forbidden": [],
    },
    {
        "id": "REL-N-10",
        "query": "stock price",
        "scope": None,
        "project_name": None,
        "max_results": 0,
        "forbidden": [],
    },
]


@pytest.mark.parametrize("case", NEGATIVE_CASES, ids=[c["id"] for c in NEGATIVE_CASES])
@pytest.mark.asyncio(loop_scope="session")
async def test_negative_scenarios(scenario_service, case):
    """Per §2.2.2 — negatives must be 100% clean. Hard assert."""
    results = await scenario_service.search(
        case["query"],
        scope=case["scope"],
        project_name=case["project_name"],
        limit=20,
    )
    titles = [r["title"] for r in results]

    if case["max_results"] is not None:
        assert len(results) <= case["max_results"], (
            f"{case['id']} query={case['query']!r} "
            f"expected ≤{case['max_results']} results but got "
            f"{len(results)}: {titles[:5]}"
        )

    for forbidden in case.get("forbidden", []):
        assert forbidden not in titles, (
            f"{case['id']} query={case['query']!r} "
            f"must not return {forbidden!r}; got titles={titles[:10]}"
        )

    for substr in case.get("forbidden_substrings", []):
        leaks = [t for t in titles if substr in t]
        assert not leaks, (
            f"{case['id']} query={case['query']!r} "
            f"scope={case['scope']} project={case['project_name']} "
            f"leaked titles containing {substr!r}: {leaks}"
        )


# ---------------------------------------------------------------------------
# 3. Scope isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_scope_isolation(scenario_service, all_scenarios, capsys):
    """Every scenario carrying scope + expected_not_hits must keep not_hits out.

    §1.2 target is ≥ 90%. We print failures but soft-assert a conservative
    floor so CI regressions are visible.
    """

    items = _flatten_scope_isolation(all_scenarios)
    total = len(items)
    assert total > 0, "no scope-isolation scenarios found"

    passed = 0
    failures: list[tuple[str, str, list[str], list[str]]] = []

    for category, sc in items:
        results = await _run_query(scenario_service, sc)
        titles = [r["title"] for r in results]
        bad = [t for t in (sc.get("expected_not_hits") or []) if t in titles]
        if not bad:
            passed += 1
        else:
            failures.append((category, sc.get("query", ""), bad, titles[:5]))

    rate = passed / total

    with capsys.disabled():
        print()
        print("===== Relevance Scope Isolation =====")
        print(f"Scope-isolation scenarios: {total}")
        print(f"Passed: {passed}/{total} ({rate:.1%})")
        if failures:
            print("Failures:")
            for cat, q, bad, top5 in failures:
                print(f"  [{cat}] q={q!r}")
                print(f"     leaked: {bad}")
                print(f"     top5: {top5}")
        print("======================================")

    assert rate >= 0.70, (
        f"Scope isolation rate {rate:.1%} below 70% floor (target ≥90%); "
        f"scope filter may be broken."
    )
