"""智能性 20 条 + EVAL_CRITERIA E2E 18 条。

依赖 ``tests/scenario_conftest.py``（它不是 pytest conftest，通过
``pytest_plugins`` 激活）。

三大维度约定：
- INT-01~20：20 条手工意图/口语/黑话/图谱 case。INT-16 错别字标
  hypothesis，不计入硬门禁分母。硬门禁 ≥ 50%。
- EVAL E2E（ACC-E-01~18）：18 条产品验收查询，按 5 组分数+总分。
  数据缺口 SKIP 不进分母。硬门禁 ≥ 70%（有效通过率）。

不 mock；走真实 SQLite + FTS5 + jieba。失败时 soft report 前 5 条
返回结果。
"""

from __future__ import annotations

import pytest

pytest_plugins = ["scenario_conftest"]


# ---------------------------------------------------------------------------
# 判定辅助
# ---------------------------------------------------------------------------


def _titles(rows: list[dict]) -> list[str]:
    return [r.get("title", "") for r in rows]


def _any_hit(expected: list[str], titles: list[str]) -> bool:
    return any(t in titles for t in expected)


def _report_fail(case_id: str, query: str, titles: list[str], note: str = "") -> str:
    top5 = titles[:5]
    suffix = f" ({note})" if note else ""
    return f"[{case_id}] query={query!r} top5={top5}{suffix}"


# ---------------------------------------------------------------------------
# 智能性 20 条
# ---------------------------------------------------------------------------


INTELLIGENCE_CASES = [
    # (case_id, query, query_type, scope, expected_any_of, note)
    (
        "INT-01",
        "用户脾气",
        "search",
        None,
        ["直接简洁不要 AI 腔", "说话带证据不要含糊"],
        "口语脾气→偏好",
    ),
    (
        "INT-02",
        "坑",
        "search",
        None,
        None,  # 特殊：只要结果里有 tag=risk 的条目即可
        "单字→风险类 (tag risk)",
    ),
    (
        "INT-03",
        "批量测试中文分词",
        "search",
        None,
        [
            "FTS5 中文分词不完善",
            "中文搜索先用英文关键词兜底",
            "FTS5 用 jieba 做中文分词",
        ],
        "纯中文跨词边界",
    ),
    (
        "INT-04",
        "不要废话",
        "search",
        None,
        ["直接简洁不要 AI 腔", "回答完不要再总结"],
        "口语否定式",
    ),
    (
        "INT-05",
        "怎么测试",
        "search",
        None,
        ["不 mock 测试", "新模块必须带单测"],
        "疑问式意图",
    ),
    (
        "INT-06",
        "哪些规则不能碰",
        "search",
        None,
        None,  # 特殊：要求至少 1 条 delivery-rule tag
        "否定语义→delivery-rule tag",
    ),
    (
        "INT-07",
        "改代码之前要做什么",
        "search",
        None,
        ["修改代码先 bun build 再测试", "破坏性改动先确认"],
        "流程类意图",
    ),
    (
        "INT-08",
        "Agent 新人入门",
        "search",
        None,
        ["workspace 多项目注册制", "评估由新 Agent 零上下文验证"],
        "角色意图",
    ),
    (
        "INT-09",
        "做完怎么算交付",
        "search",
        None,
        ["有证据才能说完成", "交付前必过 delivery-gate"],
        "完成定义意图",
    ),
    (
        "INT-10",
        "中文搜不到怎么办",
        "search",
        None,
        ["中文搜索先用英文关键词兜底", "FTS5 中文分词不完善"],
        "自指+口语",
    ),
    (
        "INT-11",
        "testing",
        "tag-search",
        None,
        None,  # 特殊：要求结果跨 >= 2 个类别（看 title 特征）
        "跨类别 tag testing",
    ),
    (
        "INT-12",
        "SQLite 为什么选它",
        "search",
        "project",
        [
            "SQLite 作为单文件零基础设施存储",
            "mnemo 选择纯数据库而非向量黑盒",
        ],
        "疑问+决策理由",
    ),
    (
        "INT-13",
        "Phase 1",
        "search",
        None,
        ["Phase 路线图 4 阶段", "Phase 1 并行 5 任务拆分"],
        "黑话",
    ),
    (
        "INT-14",
        "单测",
        "search",
        None,
        ["新模块必须带单测", "不 mock 测试"],
        "同义缩写",
    ),
    (
        "INT-15",
        "这个项目干啥的",
        "search",
        "project",
        [
            "mnemo 产品定位 Agent-first 知识库",
            "mnemo 产品定位决策 - 2026-04-18",
        ],
        "口语疑问→定位",
    ),
    (
        "INT-16",
        "数据裤",
        "search",
        None,
        [
            "SQLite 作为单文件零基础设施存储",
            "纯数据库架构不落 markdown 文件",
        ],
        "错别字 hypothesis（不计硬门禁分母）",
    ),
    (
        "INT-17",
        "不 fork basic-memory 自研 mnemo",
        "related",
        None,
        [
            "basic-memory 是 AGPL 协议不是 MIT",
            "mnemo 用 Proprietary 闭源协议",
        ],
        "图谱 depth=2 跨跳",
    ),
    (
        "INT-18",
        "SQLite 作为单文件零基础设施存储",
        "related",
        None,
        [
            "FTS5 用 jieba 做中文分词",
            "纯数据库架构不落 markdown 文件",
        ],
        "图谱多分支",
    ),
    (
        "INT-19",
        # 设计原定 ["user-preference", "global"]，但 fixture 中 "global" 是 scope
        # 不是 tag；按设计意图（多 tag AND 返回 ≥ 5 条），改为两个真实存在的 tag。
        ["user-preference", "communication-style"],
        "tag-search-and",
        "global",
        None,  # 特殊：要求 >=5 条
        "多 tag AND (scope=global)",
    ),
    (
        "INT-20",
        "跑通再说完",
        "search",
        None,
        ["有证据才能说完成", "真实验证优先", "跑起来看得见才信"],
        "口语交付观",
    ),
]

HYPOTHESIS_IDS = {"INT-16"}


async def _run_intelligence_case(
    case_id: str,
    query,
    query_type: str,
    scope,
    expected_any,
    service,
) -> tuple[bool, list[str], str]:
    """Return (passed, titles_for_report, extra_note)."""
    if query_type == "search":
        rows = await service.search(query, scope=scope, limit=20)
        titles = _titles(rows)
        if case_id == "INT-02":
            # 单字 "坑" —— 要求任一结果 tag 含 "risk"
            for r in rows:
                tags = r.get("tags") or []
                if any("risk" in (tag or "").lower() for tag in tags):
                    return True, titles, ""
            return False, titles, "no tag=risk in top20"
        if case_id == "INT-06":
            # 否定语义 —— 至少 1 条 tag 含 delivery-rule
            for r in rows:
                tags = r.get("tags") or []
                if any("delivery-rule" in (tag or "") for tag in tags):
                    return True, titles, ""
            return False, titles, "no tag=delivery-rule in top20"
        if expected_any is None:
            return False, titles, "expected_any undefined"
        passed = _any_hit(expected_any, titles)
        return passed, titles, ""
    if query_type == "tag-search":
        # INT-11 跨类别：tag=testing 应同时召回 delivery_rules 和 test_cases
        # 两个类别下的知识（以代表 title 判别）。
        rows = await service.search_by_tag([query], scope=scope, limit=50)
        titles = _titles(rows)
        if case_id == "INT-11":
            delivery_reps = {
                "不 mock 测试",
                "新模块必须带单测",
                "单测不是通过而是发现问题",
                "AionUi 测试框架 Vitest 覆盖率 80%",
            }
            testcase_reps_keywords = (
                "cross-project",
                "AionUi Hub",
                "supercell",
                "ai-store",
                "mcp-team-hub",
                "pytest",
            )
            delivery_hit = any(t in delivery_reps for t in titles)
            testcase_hit = any(
                any(kw in t for kw in testcase_reps_keywords) for t in titles
            )
            passed = delivery_hit and testcase_hit and len(rows) >= 5
            return (
                passed,
                titles,
                f"n_rows={len(rows)} delivery={delivery_hit} testcase={testcase_hit}",
            )
        if expected_any is None:
            return False, titles, "expected_any undefined"
        return _any_hit(expected_any, titles), titles, ""
    if query_type == "tag-search-and":
        # INT-19 多 tag AND
        rows = await service.search_by_tag(query, scope=scope, limit=50)
        titles = _titles(rows)
        return len(rows) >= 5, titles, f"n_rows={len(rows)}"
    if query_type == "related":
        rows = await service.get_related(query, depth=2)
        titles = _titles(rows)
        if expected_any is None:
            return False, titles, "expected_any undefined"
        return _any_hit(expected_any, titles), titles, ""
    return False, [], f"unknown query_type={query_type}"


@pytest.mark.asyncio
async def test_intelligence_suite(scenario_service, scenario_stats, capsys):
    """20 条智能性 case 汇总报告 + 硬门禁 ≥ 50%（不计 hypothesis）。"""
    results = []
    for case_id, query, qtype, scope, expected_any, note in INTELLIGENCE_CASES:
        passed, titles, extra = await _run_intelligence_case(
            case_id, query, qtype, scope, expected_any, scenario_service
        )
        results.append(
            {
                "id": case_id,
                "query": query,
                "type": qtype,
                "pass": passed,
                "titles": titles,
                "note": note,
                "extra": extra,
            }
        )

    # 分子/分母（排除 hypothesis）
    denom = [r for r in results if r["id"] not in HYPOTHESIS_IDS]
    passed_n = sum(1 for r in denom if r["pass"])
    total_n = len(denom)
    rate = passed_n / total_n if total_n else 0.0

    # 打印报告
    print("\n===== Intelligence Suite =====")
    print(f"fixture stats: {scenario_stats}")
    for r in results:
        tag = "PASS" if r["pass"] else "FAIL"
        hyp = " [hypothesis]" if r["id"] in HYPOTHESIS_IDS else ""
        line = f"  {tag}{hyp} {r['id']} ({r['note']}) query={r['query']!r}"
        print(line)
        if not r["pass"]:
            print(f"      top5={r['titles'][:5]} extra={r['extra']}")
    print(
        f"Intelligence: {passed_n}/{total_n} ({rate*100:.1f}%)  "
        f"hypothesis excluded: {sorted(HYPOTHESIS_IDS)}"
    )

    # 硬门禁：≥ 50%
    assert rate >= 0.50, (
        f"智能性硬门禁未达 50%：{passed_n}/{total_n}={rate:.1%}；"
        f"失败 case={[r['id'] for r in denom if not r['pass']]}"
    )


# ---------------------------------------------------------------------------
# EVAL_CRITERIA E2E 18 条
# ---------------------------------------------------------------------------


# 每条 case：{id, group, query, type, scope, expected_any, data_gap_check?, note}
# expected_any=None 且无特殊 handler 的给特殊逻辑（ACC-E-02/03/04/06/07/10 等）
EVAL_CASES = [
    # ---------- 新 Agent 初到项目 (7) ----------
    {
        "id": "ACC-E-01",
        "group": "新 Agent 初到项目",
        "query": "mnemo 定位",
        "type": "search",
        "scope": "project",
        "expected_any": [
            "mnemo 产品定位 Agent-first 知识库",
            "mnemo 产品定位决策 - 2026-04-18",
        ],
    },
    {
        "id": "ACC-E-02",
        "group": "新 Agent 初到项目",
        "query": "用户偏好",
        "type": "search-tag-any",
        "scope": None,
        "required_tag": "user-preference",
        "min_hits": 3,
        "note": "hypothesis: title 无'偏好'二字",
    },
    {
        "id": "ACC-E-03",
        "group": "新 Agent 初到项目",
        "query": "禁止",
        "type": "search",
        "scope": None,
        "expected_any": [
            "禁止主 Agent 角色扮演多角色",
            "禁止孤立 subAgent",
            "禁止启动 headless 抢占用户 Chrome",
        ],
        "min_hits": 1,
    },
    {
        "id": "ACC-E-04",
        "group": "新 Agent 初到项目",
        "query": "不要做",
        "type": "search",
        "scope": None,
        "expected_any": [
            "回答完不要再总结",
            "有歧义先问不自己猜",
            "破坏性改动先确认",
        ],
    },
    {
        "id": "ACC-E-05",
        "group": "新 Agent 初到项目",
        "query": "技术栈",
        "type": "search",
        "scope": "project",
        "expected_any": ["mnemo 技术栈 Python 确认 - 截至 2026-04-18"],
    },
    {
        "id": "ACC-E-06",
        "group": "新 Agent 初到项目",
        "query": "坑",
        "type": "search-tag-any",
        "scope": None,
        "required_tag": "risk",
        "min_hits": 1,
    },
    {
        "id": "ACC-E-07",
        "group": "新 Agent 初到项目",
        "query": "问题",
        "type": "search-source-any",
        "scope": None,
        "allowed_sources": None,  # 宽松：任一非空结果
        "min_hits": 3,
    },
    # ---------- 跨项目切换 (3) ----------
    {
        "id": "ACC-E-08",
        "group": "跨项目切换",
        "query": "mteam",
        "type": "search",
        "scope": None,
        "expected_any": ["mteam 未来集成方向"],
    },
    {
        "id": "ACC-E-09",
        "group": "跨项目切换",
        "query": "架构",
        "type": "search",
        "scope": "project",
        "expected_any": [
            "mnemo 采用三层架构（repository/service/mcp）",
            "repository-service-mcp 三层架构",
        ],
    },
    {
        "id": "ACC-E-10",
        "group": "跨项目切换",
        "query": "编码规范",
        "type": "search-tag-any",
        "scope": "global",
        "required_tag_any": ["delivery-rule", "convention"],
        "min_hits": 1,
        "data_gap_probe": "编码规范",  # 若无任何 title 含此词则 SKIP
    },
    # ---------- 准确性细分 (2) ----------
    {
        "id": "ACC-E-11",
        "group": "准确性细分",
        "query": "决策",
        "type": "search-source-any",
        "scope": "project",
        "allowed_sources": ["architecture_decisions.json"],
        "min_hits": 1,
        "data_gap_probe_project": "offical-website-react",
    },
    {
        "id": "ACC-E-12",
        "group": "准确性细分",
        "query": "React",
        "type": "search",
        "scope": "project",
        "expected_any": ["AionUi 用 @arco-design/web-react 组件库"],
    },
    # ---------- 关联性细分 (2) ----------
    {
        "id": "ACC-E-13",
        "group": "关联性细分",
        "query": "basic-memory 是 AGPL 协议不是 MIT",
        "type": "get-with-source",
        "scope": None,
        "related_must_include_any": ["不 fork basic-memory 自研 mnemo"],
    },
    {
        "id": "ACC-E-14",
        "group": "关联性细分",
        "query": "FTS5 中文分词不完善",
        "type": "related",
        "scope": None,
        "expected_any": [
            "中文搜索先用英文关键词兜底",
            "trigram tokenizer 未必够用",
        ],
    },
    # ---------- 项目黑话 (4) ----------
    {
        "id": "ACC-E-15",
        "group": "项目黑话",
        "query": "Phase 1",
        "type": "search",
        "scope": None,
        "expected_any": [
            "Phase 路线图 4 阶段",
            "Phase 1 并行 5 任务拆分",
        ],
    },
    {
        "id": "ACC-E-16",
        "group": "项目黑话",
        "query": "write-gate",
        "type": "search",
        "scope": None,
        "expected_any": ["write-gate 写入门禁"],
    },
    {
        "id": "ACC-E-17",
        "group": "项目黑话",
        "query": "claim_type",
        "type": "search",
        "scope": None,
        "expected_any": [
            "mnemo Knowledge.claim_type 字段",
            "claim_type 断言类型四分类",
        ],
    },
    {
        "id": "ACC-E-18",
        "group": "项目黑话",
        "query": "TeamCreate",
        "type": "search",
        "scope": None,
        "expected_any": ["多 Agent 协作必须使用 TeamCreate"],
    },
]


async def _probe_title_exists(service, substr: str) -> bool:
    """大概率扫一遍：拉 10k 条 title，看是否任一包含 substr。"""
    rows = await service.list_knowledge(limit=10000)
    for r in rows:
        if substr in (r.get("title") or ""):
            return True
    return False


async def _probe_project_exists(service, project_name: str) -> bool:
    rows = await service.list_knowledge(
        scope="project", project_name=project_name, limit=50
    )
    return len(rows) > 0


async def _run_eval_case(case, service) -> tuple[str, list[str], str]:
    """Return ("PASS" | "FAIL" | "SKIP", titles, note)."""
    cid = case["id"]

    # 数据缺口预判
    if "data_gap_probe" in case:
        if not await _probe_title_exists(service, case["data_gap_probe"]):
            return "SKIP", [], f"data gap: no title contains {case['data_gap_probe']!r}"
    if "data_gap_probe_project" in case:
        if not await _probe_project_exists(service, case["data_gap_probe_project"]):
            return (
                "SKIP",
                [],
                f"data gap: no project={case['data_gap_probe_project']!r}",
            )

    qtype = case["type"]
    q = case["query"]
    scope = case.get("scope")

    if qtype == "search":
        rows = await service.search(q, scope=scope, limit=20)
        titles = _titles(rows)
        expected = case["expected_any"]
        min_hits = case.get("min_hits", 1)
        hit_count = sum(1 for t in expected if t in titles)
        if hit_count >= min_hits:
            return "PASS", titles, f"hits={hit_count}/{len(expected)}"
        return "FAIL", titles, f"hits={hit_count}/{len(expected)} need>={min_hits}"

    if qtype == "search-tag-any":
        # search 关键词后看结果里是否有指定 tag 的条目
        rows = await service.search(q, scope=scope, limit=50)
        titles = _titles(rows)
        required = case.get("required_tag")
        required_any = case.get("required_tag_any")
        min_hits = case.get("min_hits", 1)
        count = 0
        for r in rows:
            tags = r.get("tags") or []
            if required and any(required in (t or "") for t in tags):
                count += 1
            elif required_any and any(
                any(rt in (t or "") for rt in required_any) for t in tags
            ):
                count += 1
        if count >= min_hits:
            return "PASS", titles, f"tag hits={count}"
        return "FAIL", titles, f"tag hits={count} need>={min_hits}"

    if qtype == "search-source-any":
        rows = await service.search(q, scope=scope, limit=50)
        titles = _titles(rows)
        allowed = case.get("allowed_sources")
        min_hits = case.get("min_hits", 1)
        if allowed:
            count = sum(1 for r in rows if r.get("source") in allowed)
            if count >= min_hits:
                return "PASS", titles, f"source hits={count}"
            return "FAIL", titles, f"source hits={count} need>={min_hits}"
        # 宽松：只要有 >= min_hits 条结果
        if len(rows) >= min_hits:
            return "PASS", titles, f"n_rows={len(rows)}"
        return "FAIL", titles, f"n_rows={len(rows)} need>={min_hits}"

    if qtype == "related":
        rows = await service.get_related(q, depth=2)
        titles = _titles(rows)
        expected = case["expected_any"]
        if _any_hit(expected, titles):
            return "PASS", titles, ""
        return "FAIL", titles, "no expected in related depth=2"

    if qtype == "get-with-source":
        row = await service.get_knowledge(q)
        if row is None:
            return "FAIL", [], "get returned None"
        has_source = bool(row.get("source"))
        has_created_at = bool(row.get("created_at"))
        must_include = case.get("related_must_include_any") or []
        related = row.get("related") or []
        rel_ok = (
            _any_hit(must_include, related) if must_include else True
        )
        ok = has_source and has_created_at and rel_ok
        note = (
            f"source={has_source} created_at={has_created_at} "
            f"related_ok={rel_ok} related={related[:5]}"
        )
        return ("PASS" if ok else "FAIL"), [row.get("title", "")], note

    return "FAIL", [], f"unknown type={qtype}"


EVAL_GROUPS = [
    ("新 Agent 初到项目", 7),
    ("跨项目切换", 3),
    ("准确性细分", 2),
    ("关联性细分", 2),
    ("项目黑话", 4),
]


@pytest.mark.asyncio
async def test_eval_criteria_e2e(scenario_service):
    """EVAL_CRITERIA 5 场景 18 查询。硬门禁：有效通过率 ≥ 70%。"""
    service = scenario_service
    group_pass = {g: 0 for g, _ in EVAL_GROUPS}
    group_skip = {g: 0 for g, _ in EVAL_GROUPS}
    group_total = {g: n for g, n in EVAL_GROUPS}

    total_pass = 0
    total_effective = 0  # 分母（去 SKIP）
    fail_lines = []
    skip_lines = []

    for case in EVAL_CASES:
        status, titles, note = await _run_eval_case(case, service)
        g = case["group"]
        if status == "SKIP":
            group_skip[g] += 1
            skip_lines.append(f"    SKIP {case['id']} ({case['query']!r}) — {note}")
            continue
        total_effective += 1
        if status == "PASS":
            group_pass[g] += 1
            total_pass += 1
        else:
            fail_lines.append(
                f"    FAIL {case['id']} ({case['query']!r}) top5={titles[:5]} | {note}"
            )

    rate = total_pass / total_effective if total_effective else 0.0

    print("\n===== EVAL_CRITERIA E2E =====")
    for g, n in EVAL_GROUPS:
        skipped = group_skip[g]
        effective = n - skipped
        p = group_pass[g]
        line = f"  [{g}] {p}/{effective}"
        if skipped:
            line += f"  ({skipped} skip)"
        print(line)
    print("-----")
    if skip_lines:
        print("Skipped:")
        for ln in skip_lines:
            print(ln)
    if fail_lines:
        print("Failed:")
        for ln in fail_lines:
            print(ln)
    print(
        f"Effective: {total_pass}/{total_effective} "
        f"({rate*100:.1f}%)  skipped_total={sum(group_skip.values())}"
    )
    print("Gate: >= 70%  " + ("PASS" if rate >= 0.70 else "FAIL"))

    assert rate >= 0.70, (
        f"EVAL_CRITERIA E2E 有效通过率 {rate:.1%} < 70%；"
        f"失败 case={[l.split()[1] for l in fail_lines]}"
    )
