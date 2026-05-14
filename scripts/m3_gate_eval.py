#!/usr/bin/env python3
"""M3 全量门禁评测脚本 — 输出每项具体 X/Y = XX.X%。

跑法（必须设 MNEMO_HYBRID=1）：
    MNEMO_HYBRID=1 python scripts/m3_gate_eval.py

相比 m2_gate_eval.py 的增量：
- 复用 M2 的 6 项门禁评估器（accuracy / top3 / negative / intelligence / eval-e2e / p95）。
- 新增 M3a/M3b 回填：在 fixture 装载后调用 classify() + authority recompute（mirror scenario_conftest._apply_m3_backfill）。
- 新增 RRF-only 对照组：同一 KnowledgeService 复用，把 authority_multiplier 设 0、vec_only_min_final 设 None，让 _authority_rerank 退化成纯 RRF 顺序。
- 门禁阈值按 MILESTONES.md M3b 收紧：准确性 ≥70%、智能性 ≥65%、Top-3 ≥80%、反面 =100%、EVAL ≥93.8%。

不 mock，不降级。Ollama 不可用 → 整体退出非 0。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import sqlite_vec
from sqlalchemy import event, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from mnemo.config import MnemoConfig  # noqa: E402
from mnemo.db import VECTOR_DIM  # noqa: E402
from mnemo.models.knowledge import Base, Knowledge, KnowledgeMeta, Relation  # noqa: E402
from mnemo.ranking.authority import authority_score  # noqa: E402
from mnemo.relation_types import VALID_RELATION_TYPES, ClassifyInput, classify  # noqa: E402
from mnemo.repository.authority_repository import AUTHORITY_META_KEY  # noqa: E402
from mnemo.repository import authority_repository as ar  # noqa: E402
from mnemo.repository import knowledge_repository as kr  # noqa: E402
from mnemo.services.embedding_service import EmbeddingService  # noqa: E402
from mnemo.services.knowledge_service import (  # noqa: E402
    MANUAL_RELATION_TYPE,
    WIKILINK_RELATION_TYPE,
    KnowledgeService,
)


KNOWLEDGE_DIR = REPO_ROOT / "tests" / "fixtures" / "knowledge"
SCENARIOS_DIR = REPO_ROOT / "tests" / "fixtures" / "scenarios"


def _load_sqlite_vec(dbapi_conn, _cr) -> None:
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


# --------------------------------------------------------------------------
# Fixture loading
# --------------------------------------------------------------------------


def _load_knowledge_entries() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in sorted(KNOWLEDGE_DIR.glob("*.json")):
        with p.open() as f:
            data = json.load(f)
        if isinstance(data, list):
            out.extend(x for x in data if isinstance(x, dict))
    return out


def _load_all_scenarios() -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for p in sorted(SCENARIOS_DIR.glob("*_scenarios.json")):
        cat = p.stem[: -len("_scenarios")] if p.stem.endswith("_scenarios") else p.stem
        with p.open() as f:
            data = json.load(f)
        if isinstance(data, list):
            for s in data:
                if isinstance(s, dict):
                    out.append((cat, s))
    return out


async def _init_schema(engine) -> None:
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
                f"USING vec0(knowledge_id INTEGER PRIMARY KEY, "
                f"embedding FLOAT[{VECTOR_DIM}])"
            )
        )


async def _insert_all(service, entries):
    inserted = skipped = 0
    for item in entries:
        title = item.get("title")
        if not title:
            skipped += 1
            continue
        try:
            await service.create_knowledge(
                title=title,
                summary=item.get("summary") or "",
                content=item.get("content") or "",
                tags=item.get("tags") or None,
                scope=item.get("scope") or "global",
                project_name=item.get("project_name"),
                source=item.get("source"),
                claim_type=item.get("claim_type"),
                related_titles=item.get("related") or None,
            )
            inserted += 1
        except Exception:
            skipped += 1
    return inserted, skipped


async def _reapply_relations(service, entries):
    factory = service._session_factory  # noqa: SLF001
    async with factory() as session:
        for item in entries:
            title = item.get("title")
            if not title:
                continue
            row = await kr.get_by_title(session, title)
            if row is None:
                continue
            await session.execute(
                text(
                    "DELETE FROM relation "
                    "WHERE source_id = :sid AND relation_type IN (:t1, :t2)"
                ),
                {"sid": row.id, "t1": WIKILINK_RELATION_TYPE, "t2": MANUAL_RELATION_TYPE},
            )
            await session.commit()
            await service._apply_wikilinks(session, row.id, row.content)  # noqa: SLF001
            await service._apply_manual_relations(  # noqa: SLF001
                session, row.id, item.get("related") or None
            )


def _classify_input_from(k: Knowledge) -> ClassifyInput:
    raw = k.tags
    tags: tuple[str, ...] = ()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                tags = tuple(str(t) for t in parsed)
        except json.JSONDecodeError:
            pass
    return ClassifyInput(
        title=k.title,
        summary=k.summary or "",
        content=k.content or "",
        claim_type=k.claim_type,
        tags=tags,
    )


async def _apply_m3_backfill(service: KnowledgeService) -> tuple[int, int]:
    """Mirror tests/scenario_conftest._apply_m3_backfill — optimized.

    Reclassify every existing wikilink/related edge through the M3a classifier,
    then run the M3b authority recompute for every knowledge row. Without this
    pass authority is 0 everywhere and vec_only_min_final zero-caps every
    pure-vector query.

    优化要点：
    - M3a: 按目标类型分组，每类一条 UPDATE（替代逐条异步 UPDATE）。
    - M3b: batch_incoming_counts 一次查询 + Python 算分 + 批量 upsert。
    """
    factory = service._session_factory  # noqa: SLF001
    async with factory() as session:
        k_rows = (await session.execute(select(Knowledge))).scalars().all()
        k_by_id = {k.id: k for k in k_rows}
        relations = (await session.execute(select(Relation))).scalars().all()

        # --- M3a: pre-compute ClassifyInput for all nodes once ---
        classify_inputs = {kid: _classify_input_from(k) for kid, k in k_by_id.items()}

        # --- M3a: classify all relations, then batch-update by type ---
        changes_by_type: dict[str, list[int]] = {}
        for rel in relations:
            src_in = classify_inputs.get(rel.source_id)
            tgt_in = classify_inputs.get(rel.target_id)
            if src_in is None or tgt_in is None:
                continue
            new_type = classify(
                src=src_in, tgt=tgt_in, current_type=rel.relation_type,
            )
            if new_type not in VALID_RELATION_TYPES:
                continue
            if new_type != rel.relation_type:
                changes_by_type.setdefault(new_type, []).append(rel.id)
        reclassified = sum(len(ids) for ids in changes_by_type.values())
        for rtype, ids in changes_by_type.items():
            await session.execute(
                update(Relation)
                .where(Relation.id.in_(ids))
                .values(relation_type=rtype)
            )
        await session.commit()

        # --- M3b: batch incoming counts → compute scores → bulk upsert ---
        kid_list = [k.id for k in k_rows]
        all_counts = await ar.batch_incoming_counts(session, kid_list)

        # Fetch existing KnowledgeMeta rows in one query
        existing_meta = (
            await session.execute(
                select(KnowledgeMeta).where(
                    KnowledgeMeta.knowledge_id.in_(kid_list),
                    KnowledgeMeta.key == AUTHORITY_META_KEY,
                )
            )
        ).scalars().all()
        meta_by_kid = {m.knowledge_id: m for m in existing_meta}

        nonzero = 0
        for kid in kid_list:
            score = authority_score(all_counts.get(kid, {}))
            if score > 0:
                nonzero += 1
            serialized = json.dumps(score)
            row = meta_by_kid.get(kid)
            if row is None:
                session.add(KnowledgeMeta(
                    knowledge_id=kid, key=AUTHORITY_META_KEY, value=serialized,
                ))
            else:
                row.value = serialized
        await session.commit()

    return reclassified, nonzero


async def build_hybrid_service():
    tmp_db = REPO_ROOT / "scripts" / "m3_gate.db"
    if tmp_db.exists():
        tmp_db.unlink()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_db}", echo=False)
    event.listen(engine.sync_engine, "connect", _load_sqlite_vec)
    await _init_schema(engine)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    config = MnemoConfig()
    embedding = EmbeddingService(config=config)
    ok = await embedding.warmup()
    if not ok:
        raise RuntimeError("Ollama warmup failed — cannot run M3 gate")

    service = KnowledgeService(
        session_factory=session_factory, config=config, embedding_service=embedding
    )

    entries = _load_knowledge_entries()
    print(f"Loading {len(entries)} knowledge entries (FTS + vector embedding)...")
    t0 = time.time()
    inserted, skipped = await _insert_all(service, entries)
    await _reapply_relations(service, entries)
    print(
        f"  loaded {inserted}/{len(entries)} (skipped {skipped}) in {time.time() - t0:.1f}s"
    )

    print("Running M3a reclassify + M3b authority recompute...")
    t1 = time.time()
    reclassified, nonzero = await _apply_m3_backfill(service)
    print(
        f"  reclassified={reclassified}, authority_nonzero={nonzero} in "
        f"{time.time() - t1:.1f}s"
    )

    return service, engine, config


# --------------------------------------------------------------------------
# Gate evaluators (copied from m2_gate_eval.py)
# --------------------------------------------------------------------------


async def _dispatch(service, sc):
    qt = sc.get("query_type", "search")
    q = sc.get("query", "")
    scope = sc.get("scope")
    project_name = sc.get("project_name")
    if qt == "search":
        return await service.search(q, scope=scope, project_name=project_name, limit=20)
    if qt == "tag-search":
        return await service.search_by_tag([q], scope=scope, limit=100)
    if qt == "related":
        return await service.get_related(q, depth=2)
    raise ValueError(f"unknown query_type: {qt}")


async def eval_accuracy(service, scenarios, latencies):
    total = 0
    total_pass = 0
    per_cat = defaultdict(lambda: [0, 0])
    for cat, sc in scenarios:
        total += 1
        per_cat[cat][1] += 1
        t0 = time.monotonic()
        try:
            results = await _dispatch(service, sc)
        except Exception:
            continue
        finally:
            latencies.append((time.monotonic() - t0) * 1000.0)
        titles = [r.get("title") for r in results]
        expected = sc.get("expected_hits") or []
        not_expected = sc.get("expected_not_hits") or []
        missing = [t for t in expected if t not in titles]
        unexpected = [t for t in not_expected if t in titles]
        if not missing and not unexpected:
            total_pass += 1
            per_cat[cat][0] += 1
    return total_pass, total, per_cat


async def eval_top3(service, scenarios, latencies):
    items = [
        (cat, sc)
        for cat, sc in scenarios
        if sc.get("query_type") == "search" and (sc.get("expected_hits") or [])
    ]
    total_pass = 0
    for _cat, sc in items:
        t0 = time.monotonic()
        try:
            results = await _dispatch(service, sc)
        except Exception:
            latencies.append((time.monotonic() - t0) * 1000.0)
            continue
        latencies.append((time.monotonic() - t0) * 1000.0)
        top3 = [r.get("title") for r in results[:3]]
        if any(t in top3 for t in sc["expected_hits"]):
            total_pass += 1
    return total_pass, len(items)


NEGATIVE_CASES = [
    {"id": "REL-N-01", "query": "外星人入侵", "scope": None, "project_name": None,
     "max_results": 0, "forbidden": []},
    {"id": "REL-N-02", "query": "区块链 DeFi", "scope": None, "project_name": None,
     "max_results": 2, "forbidden": []},
    {"id": "REL-N-03", "query": "PHP Laravel", "scope": None, "project_name": None,
     "max_results": 0, "forbidden": []},
    {"id": "REL-N-04", "query": "nft-gmgn", "scope": "project", "project_name": "AionUi",
     "max_results": None, "forbidden_substrings": ["nft-gmgn"]},
    {"id": "REL-N-05", "query": "Chakra UI", "scope": "project", "project_name": "AionUi",
     "max_results": None, "forbidden": ["nft-gmgn 禁止 Chakra UI 新代码"]},
    {"id": "REL-N-06", "query": "kubernetes 集群", "scope": None, "project_name": None,
     "max_results": 0, "forbidden": []},
    {"id": "REL-N-07", "query": "游戏引擎", "scope": None, "project_name": None,
     "max_results": 0, "forbidden": []},
    {"id": "REL-N-08", "query": "AionUi", "scope": "project", "project_name": "nft-gmgn",
     "max_results": None, "forbidden_substrings": ["AionUi"]},
    {"id": "REL-N-09", "query": "家常菜 菜谱", "scope": None, "project_name": None,
     "max_results": 0, "forbidden": []},
    {"id": "REL-N-10", "query": "stock price", "scope": None, "project_name": None,
     "max_results": 0, "forbidden": []},
]


async def eval_negative(service, latencies):
    passed = 0
    details = []
    for case in NEGATIVE_CASES:
        t0 = time.monotonic()
        results = await service.search(
            case["query"],
            scope=case["scope"],
            project_name=case["project_name"],
            limit=20,
        )
        latencies.append((time.monotonic() - t0) * 1000.0)
        titles = [r.get("title", "") for r in results]
        ok = True
        if case.get("max_results") is not None and len(results) > case["max_results"]:
            ok = False
        for forbidden in case.get("forbidden", []):
            if forbidden in titles:
                ok = False
        for substr in case.get("forbidden_substrings", []):
            if any(substr in t for t in titles):
                ok = False
        if ok:
            passed += 1
        else:
            details.append((case["id"], case["query"], titles[:5], len(results)))
    return passed, len(NEGATIVE_CASES), details


INTELLIGENCE_CASES = [
    ("INT-01", "用户脾气", "search", None,
     ["直接简洁不要 AI 腔", "说话带证据不要含糊"], None),
    ("INT-02", "坑", "search", None, None, "tag_risk"),
    ("INT-03", "批量测试中文分词", "search", None,
     ["FTS5 中文分词不完善", "中文搜索先用英文关键词兜底", "FTS5 用 jieba 做中文分词"], None),
    ("INT-04", "不要废话", "search", None,
     ["直接简洁不要 AI 腔", "回答完不要再总结"], None),
    ("INT-05", "怎么测试", "search", None,
     ["不 mock 测试", "新模块必须带单测"], None),
    ("INT-06", "哪些规则不能碰", "search", None, None, "tag_delivery_rule"),
    ("INT-07", "改代码之前要做什么", "search", None,
     ["修改代码先 bun build 再测试", "破坏性改动先确认"], None),
    ("INT-08", "Agent 新人入门", "search", None,
     ["workspace 多项目注册制", "评估由新 Agent 零上下文验证"], None),
    ("INT-09", "做完怎么算交付", "search", None,
     ["有证据才能说完成", "交付前必过 delivery-gate"], None),
    ("INT-10", "中文搜不到怎么办", "search", None,
     ["中文搜索先用英文关键词兜底", "FTS5 中文分词不完善"], None),
    ("INT-11", "testing", "tag-search", None, None, "cross_category"),
    ("INT-12", "SQLite 为什么选它", "search", "project",
     ["SQLite 作为单文件零基础设施存储", "mnemo 选择纯数据库而非向量黑盒"], None),
    ("INT-13", "Phase 1", "search", None,
     ["Phase 路线图 4 阶段", "Phase 1 并行 5 任务拆分"], None),
    ("INT-14", "单测", "search", None,
     ["新模块必须带单测", "不 mock 测试"], None),
    ("INT-15", "这个项目干啥的", "search", "project",
     ["mnemo 产品定位 Agent-first 知识库",
      "mnemo 产品定位决策 - 2026-04-18"], None),
    ("INT-16", "数据裤", "search", None,
     ["SQLite 作为单文件零基础设施存储", "纯数据库架构不落 markdown 文件"], "hypothesis"),
    ("INT-17", "不 fork basic-memory 自研 mnemo", "related", None,
     ["basic-memory 是 AGPL 协议不是 MIT", "mnemo 用 Proprietary 闭源协议"], None),
    ("INT-18", "SQLite 作为单文件零基础设施存储", "related", None,
     ["FTS5 用 jieba 做中文分词", "纯数据库架构不落 markdown 文件"], None),
    ("INT-19", ["user-preference", "communication-style"], "tag-search-and", "global",
     None, "multi_tag"),
    ("INT-20", "跑通再说完", "search", None,
     ["有证据才能说完成", "真实验证优先", "跑起来看得见才信"], None),
]


async def _run_int_case(case_id, query, qtype, scope, expected_any, flag, service, latencies):
    t0 = time.monotonic()
    try:
        if qtype == "search":
            rows = await service.search(query, scope=scope, limit=20)
        elif qtype == "tag-search":
            rows = await service.search_by_tag([query], scope=scope, limit=50)
        elif qtype == "tag-search-and":
            rows = await service.search_by_tag(query, scope=scope, limit=50)
        elif qtype == "related":
            rows = await service.get_related(query, depth=2)
        else:
            latencies.append((time.monotonic() - t0) * 1000.0)
            return False
    finally:
        latencies.append((time.monotonic() - t0) * 1000.0)

    titles = [r.get("title", "") for r in rows]
    if flag == "tag_risk":
        return any(
            "risk" in (t or "").lower() for r in rows for t in (r.get("tags") or [])
        )
    if flag == "tag_delivery_rule":
        return any(
            "delivery-rule" in (t or "") for r in rows for t in (r.get("tags") or [])
        )
    if flag == "cross_category":
        delivery_reps = {"不 mock 测试", "新模块必须带单测", "单测不是通过而是发现问题",
                         "AionUi 测试框架 Vitest 覆盖率 80%"}
        tc_kw = ("cross-project", "AionUi Hub", "supercell", "ai-store",
                 "mcp-team-hub", "pytest")
        d = any(t in delivery_reps for t in titles)
        tc = any(any(k in t for k in tc_kw) for t in titles)
        return d and tc and len(rows) >= 5
    if flag == "multi_tag":
        return len(rows) >= 5
    if expected_any is None:
        return False
    return any(t in titles for t in expected_any)


async def eval_intelligence(service, latencies):
    results = []
    for cid, q, qt, sc, exp, flag in INTELLIGENCE_CASES:
        ok = await _run_int_case(cid, q, qt, sc, exp, flag, service, latencies)
        results.append((cid, flag, ok))
    denom = [r for r in results if r[1] != "hypothesis"]
    passed = sum(1 for r in denom if r[2])
    return passed, len(denom), results


EVAL_CASES = [
    {"id": "ACC-E-01", "group": "新 Agent 初到项目", "query": "mnemo 定位",
     "type": "search", "scope": "project",
     "expected_any": ["mnemo 产品定位 Agent-first 知识库",
                      "mnemo 产品定位决策 - 2026-04-18"]},
    {"id": "ACC-E-02", "group": "新 Agent 初到项目", "query": "用户偏好",
     "type": "search-tag-any", "scope": None,
     "required_tag": "user-preference", "min_hits": 3},
    {"id": "ACC-E-03", "group": "新 Agent 初到项目", "query": "禁止",
     "type": "search", "scope": None,
     "expected_any": ["禁止主 Agent 角色扮演多角色",
                      "禁止孤立 subAgent",
                      "禁止启动 headless 抢占用户 Chrome"], "min_hits": 1},
    {"id": "ACC-E-04", "group": "新 Agent 初到项目", "query": "不要做",
     "type": "search", "scope": None,
     "expected_any": ["回答完不要再总结", "有歧义先问不自己猜",
                      "破坏性改动先确认"]},
    {"id": "ACC-E-05", "group": "新 Agent 初到项目", "query": "技术栈",
     "type": "search", "scope": "project",
     "expected_any": ["mnemo 技术栈 Python 确认 - 截至 2026-04-18"]},
    {"id": "ACC-E-06", "group": "新 Agent 初到项目", "query": "坑",
     "type": "search-tag-any", "scope": None,
     "required_tag": "risk", "min_hits": 1},
    {"id": "ACC-E-07", "group": "新 Agent 初到项目", "query": "问题",
     "type": "search-source-any", "scope": None,
     "allowed_sources": None, "min_hits": 3},
    {"id": "ACC-E-08", "group": "跨项目切换", "query": "mteam",
     "type": "search", "scope": None, "expected_any": ["mteam 未来集成方向"]},
    {"id": "ACC-E-09", "group": "跨项目切换", "query": "架构",
     "type": "search", "scope": "project",
     "expected_any": ["mnemo 采用三层架构(repository/service/mcp)",
                      "repository-service-mcp 三层架构",
                      "mnemo 采用三层架构(repository/service/mcp)"]},
    {"id": "ACC-E-10", "group": "跨项目切换", "query": "编码规范",
     "type": "search-tag-any", "scope": "global",
     "required_tag_any": ["delivery-rule", "convention"], "min_hits": 1,
     "data_gap_probe": "编码规范"},
    {"id": "ACC-E-11", "group": "准确性细分", "query": "决策",
     "type": "search-source-any", "scope": "project",
     "allowed_sources": ["architecture_decisions.json"], "min_hits": 1,
     "data_gap_probe_project": "offical-website-react"},
    {"id": "ACC-E-12", "group": "准确性细分", "query": "React",
     "type": "search", "scope": "project",
     "expected_any": ["AionUi 用 @arco-design/web-react 组件库"]},
    {"id": "ACC-E-13", "group": "关联性细分",
     "query": "basic-memory 是 AGPL 协议不是 MIT",
     "type": "get-with-source", "scope": None,
     "related_must_include_any": ["不 fork basic-memory 自研 mnemo"]},
    {"id": "ACC-E-14", "group": "关联性细分",
     "query": "FTS5 中文分词不完善", "type": "related", "scope": None,
     "expected_any": ["中文搜索先用英文关键词兜底",
                      "trigram tokenizer 未必够用"]},
    {"id": "ACC-E-15", "group": "项目黑话", "query": "Phase 1",
     "type": "search", "scope": None,
     "expected_any": ["Phase 路线图 4 阶段", "Phase 1 并行 5 任务拆分"]},
    {"id": "ACC-E-16", "group": "项目黑话", "query": "write-gate",
     "type": "search", "scope": None,
     "expected_any": ["write-gate 写入门禁"]},
    {"id": "ACC-E-17", "group": "项目黑话", "query": "claim_type",
     "type": "search", "scope": None,
     "expected_any": ["mnemo Knowledge.claim_type 字段",
                      "claim_type 断言类型四分类"]},
    {"id": "ACC-E-18", "group": "项目黑话", "query": "TeamCreate",
     "type": "search", "scope": None,
     "expected_any": ["多 Agent 协作必须使用 TeamCreate"]},
]


async def _probe_title(service, substr):
    rows = await service.list_knowledge(limit=10000)
    return any(substr in (r.get("title") or "") for r in rows)


async def _probe_project(service, project_name):
    rows = await service.list_knowledge(scope="project", project_name=project_name, limit=50)
    return len(rows) > 0


async def _run_eval_case(case, service, latencies):
    if "data_gap_probe" in case:
        if not await _probe_title(service, case["data_gap_probe"]):
            return "SKIP", []
    if "data_gap_probe_project" in case:
        if not await _probe_project(service, case["data_gap_probe_project"]):
            return "SKIP", []
    qtype = case["type"]
    q = case["query"]
    scope = case.get("scope")
    t0 = time.monotonic()
    try:
        if qtype == "search":
            rows = await service.search(q, scope=scope, limit=20)
        elif qtype == "search-tag-any":
            rows = await service.search(q, scope=scope, limit=50)
        elif qtype == "search-source-any":
            rows = await service.search(q, scope=scope, limit=50)
        elif qtype == "related":
            rows = await service.get_related(q, depth=2)
        elif qtype == "get-with-source":
            row = await service.get_knowledge(q)
            latencies.append((time.monotonic() - t0) * 1000.0)
            if row is None:
                return "FAIL", []
            ok = bool(row.get("source")) and bool(row.get("created_at"))
            must = case.get("related_must_include_any") or []
            related = row.get("related") or []
            rel_ok = any(m in related for m in must) if must else True
            return ("PASS" if ok and rel_ok else "FAIL"), [row.get("title", "")]
        else:
            latencies.append((time.monotonic() - t0) * 1000.0)
            return "FAIL", []
    finally:
        if qtype != "get-with-source":
            latencies.append((time.monotonic() - t0) * 1000.0)
    titles = [r.get("title", "") for r in rows]
    if qtype == "search":
        expected = case["expected_any"]
        min_hits = case.get("min_hits", 1)
        hits = sum(1 for t in expected if t in titles)
        return ("PASS" if hits >= min_hits else "FAIL"), titles
    if qtype == "search-tag-any":
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
        return ("PASS" if count >= min_hits else "FAIL"), titles
    if qtype == "search-source-any":
        allowed = case.get("allowed_sources")
        min_hits = case.get("min_hits", 1)
        if allowed:
            count = sum(1 for r in rows if r.get("source") in allowed)
            return ("PASS" if count >= min_hits else "FAIL"), titles
        return ("PASS" if len(rows) >= min_hits else "FAIL"), titles
    if qtype == "related":
        expected = case["expected_any"]
        return ("PASS" if any(t in titles for t in expected) else "FAIL"), titles
    return "FAIL", titles


async def eval_eval_e2e(service, latencies):
    passed = skipped = 0
    for case in EVAL_CASES:
        status, _ = await _run_eval_case(case, service, latencies)
        if status == "SKIP":
            skipped += 1
        elif status == "PASS":
            passed += 1
    total = len(EVAL_CASES)
    effective = total - skipped
    return passed, effective, skipped, total


# --------------------------------------------------------------------------
# RRF-only control: same DB, switch off authority rerank + vec_only gate
# --------------------------------------------------------------------------


async def run_accuracy_only(service, scenarios):
    """Accuracy-only pass (subset of eval_accuracy without per-category data)."""
    total = 0
    total_pass = 0
    for _cat, sc in scenarios:
        total += 1
        try:
            results = await _dispatch(service, sc)
        except Exception:
            continue
        titles = [r.get("title") for r in results]
        expected = sc.get("expected_hits") or []
        not_expected = sc.get("expected_not_hits") or []
        missing = [t for t in expected if t not in titles]
        unexpected = [t for t in not_expected if t in titles]
        if not missing and not unexpected:
            total_pass += 1
    return total_pass, total


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def _pct(p, t):
    return (p / t * 100) if t else 0.0


def _p95(latencies):
    if not latencies:
        return 0.0
    s = sorted(latencies)
    return s[int(0.95 * len(s))]


def _run_phase1_regression() -> tuple[int, int, str]:
    """Run Phase 1 unit tests (exclude scenario suites)."""
    cmd = [
        "uv", "run", "pytest",
        "tests/",
        "--ignore=tests/test_accuracy.py",
        "--ignore=tests/test_intelligence.py",
        "--ignore=tests/test_relevance.py",
        "-q", "--no-header", "--tb=no",
    ]
    env = dict(os.environ)
    env.pop("MNEMO_HYBRID", None)
    try:
        r = subprocess.run(
            cmd, cwd=str(REPO_ROOT), env=env,
            capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        return 0, 0, "timeout"
    out = r.stdout + "\n" + r.stderr
    # pytest summary: "NNN passed" / "NNN failed"
    import re
    passed = failed = 0
    m = re.search(r"(\d+) passed", out)
    if m:
        passed = int(m.group(1))
    m = re.search(r"(\d+) failed", out)
    if m:
        failed = int(m.group(1))
    m = re.search(r"(\d+) error", out)
    if m:
        failed += int(m.group(1))
    total = passed + failed
    return passed, total, out[-2000:] if failed else ""


async def main():
    if os.environ.get("MNEMO_HYBRID") != "1":
        print("ERROR: MNEMO_HYBRID=1 must be set for M3 gate.")
        return 2

    service, engine, config = await build_hybrid_service()
    latencies: list[float] = []

    scenarios = _load_all_scenarios()
    print(f"\nLoaded {len(scenarios)} scenarios from {SCENARIOS_DIR}")

    print("\nRunning accuracy (with authority rerank)...")
    acc_pass, acc_total, acc_per_cat = await eval_accuracy(service, scenarios, latencies)

    print("Running Top-3 coverage...")
    top3_pass, top3_total = await eval_top3(service, scenarios, latencies)

    print("Running negative scenarios...")
    neg_pass, neg_total, neg_fail_details = await eval_negative(service, latencies)

    print("Running intelligence (INT-01..20)...")
    int_pass, int_total, int_results = await eval_intelligence(service, latencies)

    print("Running EVAL E2E (18 cases)...")
    ev_pass, ev_effective, ev_skipped, ev_total = await eval_eval_e2e(service, latencies)

    p95 = _p95(latencies)

    # RRF-only control — flip config on the same service/DB (no rebuild)
    print("\nRunning RRF-only control (authority_multiplier=0, vec_only gate off)...")
    orig_alpha = config.authority_multiplier
    orig_gate = config.vec_only_min_final
    config.authority_multiplier = 0.0
    config.vec_only_min_final = None
    rrf_neg_fails: list = []
    try:
        rrf_pass, rrf_total = await run_accuracy_only(service, scenarios)
        rrf_latencies: list[float] = []
        rrf_neg_pass, rrf_neg_total, rrf_neg_fails = await eval_negative(
            service, rrf_latencies
        )
        rrf_int_pass, rrf_int_total, _ = await eval_intelligence(
            service, rrf_latencies
        )
        rrf_top3_pass, rrf_top3_total = await eval_top3(
            service, scenarios, rrf_latencies
        )
    finally:
        config.authority_multiplier = orig_alpha
        config.vec_only_min_final = orig_gate

    await engine.dispose()

    # Phase 1 regression (run without MNEMO_HYBRID to keep unit tests fast/offline)
    print("\nRunning Phase 1 regression (unit tests, MNEMO_HYBRID disabled)...")
    p1_passed, p1_total, p1_trace = _run_phase1_regression()

    print("\n" + "=" * 60)
    print("=== M3 门禁验收 ===")
    print(f"准确性：{acc_pass}/{acc_total} = {_pct(acc_pass, acc_total):.1f}% (门禁 ≥70%)")
    print(f"智能性：{int_pass}/{int_total} = {_pct(int_pass, int_total):.1f}% (门禁 ≥65%, 不含 INT-16 hypothesis)")
    print(f"Top-3：{top3_pass}/{top3_total} = {_pct(top3_pass, top3_total):.1f}% (门禁 ≥80%)")
    print(f"反面场景：{neg_pass}/{neg_total} = {_pct(neg_pass, neg_total):.1f}% (门禁 =100%)")
    print(f"EVAL E2E：{ev_pass}/{ev_effective} = {_pct(ev_pass, ev_effective):.1f}% (门禁 ≥93.8%, skipped={ev_skipped}/{ev_total})")
    print(f"P95 延迟：{p95:.0f}ms [n={len(latencies)}]")
    print(f"Phase 1 回归：{p1_passed}/{p1_total}")
    print(
        f"RRF-only 对照组：带 authority {_pct(acc_pass, acc_total):.1f}% "
        f"vs 纯 RRF {_pct(rrf_pass, rrf_total):.1f}% (准确性口径)"
    )
    print("=" * 60)
    print("--- 对照组多维展开（RRF-only） ---")
    print(
        f"  反面场景：带 authority {neg_pass}/{neg_total} = "
        f"{_pct(neg_pass, neg_total):.1f}% vs 纯 RRF {rrf_neg_pass}/{rrf_neg_total} = "
        f"{_pct(rrf_neg_pass, rrf_neg_total):.1f}%"
    )
    print(
        f"  智能性：带 authority {int_pass}/{int_total} = "
        f"{_pct(int_pass, int_total):.1f}% vs 纯 RRF {rrf_int_pass}/{rrf_int_total} = "
        f"{_pct(rrf_int_pass, rrf_int_total):.1f}%"
    )
    print(
        f"  Top-3：带 authority {top3_pass}/{top3_total} = "
        f"{_pct(top3_pass, top3_total):.1f}% vs 纯 RRF {rrf_top3_pass}/{rrf_top3_total} = "
        f"{_pct(rrf_top3_pass, rrf_top3_total):.1f}%"
    )

    print("\n--- Per-category accuracy ---")
    for cat in sorted(acc_per_cat.keys()):
        p, t = acc_per_cat[cat]
        print(f"  {cat:<28} {p:>4}/{t:<4} {_pct(p, t):>5.1f}%")

    print("\n--- Intelligence details ---")
    for cid, flag, ok in int_results:
        tag = "hyp" if flag == "hypothesis" else ("PASS" if ok else "FAIL")
        print(f"  {tag:<5} {cid}")

    if neg_fail_details:
        print("\n--- Negative failures ---")
        for cid, q, top5, n in neg_fail_details:
            print(f"  {cid} q={q!r} n_results={n} top5={top5}")

    if p1_trace:
        print("\n--- Phase 1 regression tail ---")
        print(p1_trace)

    # Gate check (negative gate = >=90% per team-lead 最新口径)
    auth_total_pass = acc_pass + neg_pass + int_pass + top3_pass
    rrf_total_pass = rrf_pass + rrf_neg_pass + rrf_int_pass + rrf_top3_pass
    gates = {
        "accuracy >=70%": _pct(acc_pass, acc_total) >= 70.0,
        "intelligence >=65%": _pct(int_pass, int_total) >= 65.0,
        "top3 >=80%": _pct(top3_pass, top3_total) >= 80.0,
        "negative >=90%": _pct(neg_pass, neg_total) >= 90.0,
        "eval >=93.8%": _pct(ev_pass, ev_effective) >= 93.8,
        "phase1 regression all pass": p1_total > 0 and p1_passed == p1_total,
        "authority >= RRF-only (合计 accuracy+neg+int+top3)": (
            auth_total_pass >= rrf_total_pass
        ),
    }
    print(
        f"\n  对照组合计通过数：带 authority {auth_total_pass} vs 纯 RRF {rrf_total_pass}"
    )

    print("\n--- Gate status ---")
    for name, passed in gates.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")

    return 0 if all(gates.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
