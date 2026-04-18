"""Feature-flag framework tests — cover the 5 hard constraints from
docs/phase3/tech_research.md §16.2.

Constraints validated here:
  1. 入口守卫   — flag off routes through the Phase 2 code path.
  2. 幂等开关   — on→off→on is idempotent; no schema changes required.
  3. off 中性   — default off values are neutral (multiplier=1.0,
                  no state flip, no event write).
  4. 写侧受控   — write-path MCP tools return a structured
                  ``feature_disabled`` response when the flag is off.
  5. 测试覆盖   — every flag has at least one on + one off scenario here
                  (delegated to per-feature testers via the shared fixtures).

This suite is the *framework* — it does not assert feature logic itself.
Per-feature testers (writegate-tester / freshness-tester / stale-tester /
feedback-tester) write the behavior tests using the ``flags`` /
``flag_service`` / ``flag_state`` fixtures from ``tests/conftest.py``.

All tests use real ``MnemoConfig`` + real ``KnowledgeService`` — no mocks.
Tests that rely on Phase 3 config fields that haven't landed yet are
marked with ``@pytest.mark.phase3`` and skip via ``flag_available``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mnemo.config import MnemoConfig
from mnemo.models.knowledge import Base
from mnemo.services.knowledge_service import KnowledgeService
from tests.conftest import (
    PHASE3_FLAG_DEFAULTS,
    PHASE3_FLAG_NAMES,
    flag_available,
)


# ---------------------------------------------------------------------------
# framework-level sanity — always run, no phase3 marker required
# ---------------------------------------------------------------------------


def test_phase3_flag_name_list_matches_doc() -> None:
    """The canonical list in conftest mirrors tech_research.md §16.1 exactly.

    Adding/removing a flag must be a deliberate, reviewed edit — not a
    drive-by change. This guard prevents silent drift.
    """
    assert set(PHASE3_FLAG_NAMES) == {
        "write_gate_enabled",
        "freshness_enabled",
        "state_machine_enabled",
        "feedback_loop_enabled",
        "contradiction_pair_enabled",
        "context_aware_rank_enabled",
    }


def test_phase3_flag_defaults_match_doc() -> None:
    """Defaults: P3a flags on, P3b contradiction_pair on, context-aware rank off.

    contradiction_pair_enabled 在 P4 UX 阶段从 False 改为 True：盲测要求
    search 结果默认带 conflicts_with 字段，C8 矛盾可见场景需要默认开。
    """
    assert PHASE3_FLAG_DEFAULTS == {
        "write_gate_enabled": True,
        "freshness_enabled": True,
        "state_machine_enabled": True,
        "feedback_loop_enabled": True,
        "contradiction_pair_enabled": True,
        "context_aware_rank_enabled": False,
    }


def test_flag_available_returns_false_for_unknown_name(base_config: MnemoConfig) -> None:
    assert not flag_available(base_config, "definitely_not_a_flag_xyz")


def test_flags_fixture_skips_when_flag_not_landed(flags) -> None:
    """Constraint 5 (test coverage): the fixture must *skip* rather than
    *fail* when a teammate's feature flag hasn't landed on MnemoConfig yet,
    so this framework can be merged before P3a-M1.
    """
    with pytest.raises(pytest.skip.Exception):
        with flags(definitely_not_a_flag_xyz=False):
            pass


# ---------------------------------------------------------------------------
# Constraint 1: 入口守卫 — each declared flag must be readable as a bool.
# Actual "routes to Phase 2" behavior is validated per-feature by teammate
# testers. Here we only check the attribute contract.
# ---------------------------------------------------------------------------


@pytest.mark.phase3
@pytest.mark.parametrize("flag_name", PHASE3_FLAG_NAMES)
def test_flag_is_declared_on_config_with_bool_default(
    flag_name: str, base_config: MnemoConfig
) -> None:
    if not flag_available(base_config, flag_name):
        pytest.skip(f"{flag_name} not yet declared on MnemoConfig")
    value = getattr(base_config, flag_name)
    assert isinstance(value, bool), (
        f"{flag_name} must be bool-typed; got {type(value).__name__}"
    )
    assert value == PHASE3_FLAG_DEFAULTS[flag_name], (
        f"{flag_name} default must match §11 — expected "
        f"{PHASE3_FLAG_DEFAULTS[flag_name]}, got {value}"
    )


# ---------------------------------------------------------------------------
# Constraint 2: 幂等开关 — flipping on→off→on does not require a DB
# migration. We assert this at the schema level: before/after toggling,
# sqlite_master must be unchanged.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def schema_snapshot_service(
    tmp_path: Path,
) -> AsyncIterator[tuple[KnowledgeService, async_sessionmaker]]:
    """Fresh DB with the full schema — used to snapshot sqlite_master."""
    db_path = tmp_path / "mnemo-idempotent.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts "
                "USING fts5(title, summary, content, tags, knowledge_id UNINDEXED)"
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield KnowledgeService(session_factory=factory), factory
    finally:
        await engine.dispose()


async def _sqlite_master_rows(factory: async_sessionmaker) -> list[tuple]:
    """Snapshot every table/index definition for comparison."""
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT type, name, tbl_name, sql FROM sqlite_master "
                    "ORDER BY type, name"
                )
            )
        ).all()
    return [tuple(r) for r in rows]


@pytest.mark.phase3
@pytest.mark.parametrize("flag_name", PHASE3_FLAG_NAMES)
async def test_flag_toggle_is_schema_idempotent(
    flag_name: str,
    base_config: MnemoConfig,
    flags,
    schema_snapshot_service,
) -> None:
    """Constraint 2: on→off→on must not mutate schema.

    We take a sqlite_master snapshot, toggle the flag through all three
    states, then verify the snapshot is unchanged.
    """
    if not flag_available(base_config, flag_name):
        pytest.skip(f"{flag_name} not yet declared on MnemoConfig")

    _service, factory = schema_snapshot_service
    before = await _sqlite_master_rows(factory)

    default = PHASE3_FLAG_DEFAULTS[flag_name]
    with flags(**{flag_name: not default}):
        mid = await _sqlite_master_rows(factory)
    with flags(**{flag_name: default}):
        after = await _sqlite_master_rows(factory)

    assert before == mid == after, (
        f"toggling {flag_name} changed sqlite_master; "
        "flag switches must not require a migration"
    )


# ---------------------------------------------------------------------------
# Constraint 3: off 中性 — when off, the flag's downstream effect must be
# a no-op. We can't assert feature-specific neutrality here (that's the
# per-feature tester's job) but we can assert the *contract*: after
# entering `flags(flag=False)`, reading the flag returns False, and after
# exiting, it returns to the prior value.
# ---------------------------------------------------------------------------


@pytest.mark.phase3
@pytest.mark.parametrize("flag_name", PHASE3_FLAG_NAMES)
def test_flag_off_is_restored_on_exit(
    flag_name: str, base_config: MnemoConfig, flags
) -> None:
    if not flag_available(base_config, flag_name):
        pytest.skip(f"{flag_name} not yet declared on MnemoConfig")
    original = getattr(base_config, flag_name)

    with flags(**{flag_name: False}):
        assert getattr(base_config, flag_name) is False

    assert getattr(base_config, flag_name) == original, (
        f"{flag_name} was not restored after flags() context exited"
    )


@pytest.mark.phase3
def test_flags_context_restores_on_exception(
    base_config: MnemoConfig, flags
) -> None:
    """The context manager must restore state even if the test body raises."""
    if not all(flag_available(base_config, n) for n in PHASE3_FLAG_NAMES):
        pytest.skip("not all Phase 3 flags declared yet")

    originals = {n: getattr(base_config, n) for n in PHASE3_FLAG_NAMES}
    overrides = {n: not originals[n] for n in PHASE3_FLAG_NAMES}

    with pytest.raises(RuntimeError, match="boom"):
        with flags(**overrides):
            raise RuntimeError("boom")

    for name, expected in originals.items():
        assert getattr(base_config, name) == expected, (
            f"{name} leaked after exception — cleanup is broken"
        )


# ---------------------------------------------------------------------------
# Constraint 4: 写侧受控 — write-path MCP tools must return a structured
# ``feature_disabled`` response when their flag is off. The new write-path
# tools (write_gate_check, feedback_knowledge, archive_knowledge) don't
# exist yet, so this test discovers them by name and skips if absent —
# again, the framework ships before the tools.
# ---------------------------------------------------------------------------


# (flag_name, mcp_tool_name) pairs per §16.2 constraint 4
WRITE_PATH_TOOL_FOR_FLAG: dict[str, str] = {
    "write_gate_enabled": "write_gate_check",
    "feedback_loop_enabled": "feedback_knowledge",
    "state_machine_enabled": "archive_knowledge",
}

MINIMAL_ARGS_FOR_TOOL: dict[str, dict] = {
    "feedback_knowledge": {"knowledge_id": 999, "signal": "helpful"},
    "archive_knowledge": {"id": 999},
    "write_gate_check": {},
}


@pytest_asyncio.fixture
async def mcp_flag_env(
    tmp_path: Path, base_config: MnemoConfig
) -> AsyncIterator[KnowledgeService]:
    """Install a flag-aware KnowledgeService into the MCP server singleton."""
    from mnemo.mcp import server as mcp_server

    db_path = tmp_path / "mnemo-mcp.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts "
                "USING fts5(title, summary, content, tags, knowledge_id UNINDEXED)"
            )
        )
        await conn.execute(text("PRAGMA foreign_keys = ON"))

    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = KnowledgeService(session_factory=factory, config=base_config)
    previous = mcp_server._service
    mcp_server.set_service(service)
    try:
        yield service
    finally:
        mcp_server._service = previous
        await engine.dispose()


@pytest.mark.phase3
@pytest.mark.parametrize(
    "flag_name,tool_name", sorted(WRITE_PATH_TOOL_FOR_FLAG.items())
)
async def test_write_side_tool_returns_feature_disabled_when_flag_off(
    flag_name: str,
    tool_name: str,
    base_config: MnemoConfig,
    flags,
    mcp_flag_env,
) -> None:
    """Constraint 4: write-side MCP tools must refuse cleanly when flag off.

    The expected response shape (per §16.2 example):
        {"success": false, "reason": "feature_disabled"}

    This test is intentionally loose — it accepts any of:
      - JSON string containing both "success": false and "feature_disabled"
      - Markdown/text containing "feature_disabled"
    because the MCP tool return format is per-tool. Per-feature testers
    tighten this once the tool lands.
    """
    if not flag_available(base_config, flag_name):
        pytest.skip(f"{flag_name} not yet declared on MnemoConfig")

    from mnemo.mcp import server as mcp_server

    if not hasattr(mcp_server, tool_name):
        pytest.skip(
            f"MCP tool {tool_name!r} not yet implemented — "
            "write-path guard test will activate when the tool lands"
        )

    with flags(**{flag_name: False}):
        args = MINIMAL_ARGS_FOR_TOOL.get(tool_name, {})
        result = await mcp_server.mcp.call_tool(tool_name, args)
        # FastMCP returns a ToolResult; fall back to str() for safety.
        payload = getattr(result, "content", None)
        text_ = (
            payload[0].text
            if payload and hasattr(payload[0], "text")
            else str(result)
        )

    assert "feature_disabled" in text_, (
        f"{tool_name} with {flag_name}=False must surface 'feature_disabled'; "
        f"got: {text_[:200]}"
    )


# ---------------------------------------------------------------------------
# Constraint 5: per-feature testers are responsible for "on" and "off"
# behavior assertions. This meta-test documents the registry so that if a
# new flag is added, the test registry below must be updated — which is
# what keeps coverage from silently dropping.
# ---------------------------------------------------------------------------


def test_every_flag_has_a_known_feature_owner() -> None:
    """Registry keeping each flag tied to the teammate owning its tests.

    If you add a flag to PHASE3_FLAG_NAMES you MUST add it here too — this
    test fails loudly and reminds you to assign a tester. That is how
    constraint 5 (每个 feature 必须带 on/off 两个单测) is enforced as a
    team-level invariant, not just per-PR discipline.
    """
    owner_by_flag: dict[str, str] = {
        "write_gate_enabled": "writegate-tester",
        "freshness_enabled": "freshness-tester",
        "state_machine_enabled": "stale-tester",
        "feedback_loop_enabled": "feedback-tester",
        "contradiction_pair_enabled": "flag-tester",  # P3b, reassign at M5
        "context_aware_rank_enabled": "flag-tester",  # P3b, reassign at M7
    }
    missing = set(PHASE3_FLAG_NAMES) - owner_by_flag.keys()
    extra = owner_by_flag.keys() - set(PHASE3_FLAG_NAMES)
    assert not missing, f"flags without a tester owner: {sorted(missing)}"
    assert not extra, f"owner registry has stale entries: {sorted(extra)}"


# ---------------------------------------------------------------------------
# flag_state parametrize smoke — validates the on/off driver fixture works
# for teammates who will @pytest.mark.phase3 their feature tests.
# ---------------------------------------------------------------------------


@pytest.mark.phase3
def test_flag_state_fixture_parametrizes_on_and_off(flag_state: bool) -> None:
    """Sanity: the shared ``flag_state`` fixture yields True and False."""
    assert isinstance(flag_state, bool)
