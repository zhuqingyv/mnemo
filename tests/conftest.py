"""Phase 3 feature-flag fixtures — shared by every feature test.

Usage pattern (for other testers on the team):

    @pytest.mark.phase3
    async def test_my_feature_off_mirrors_phase2(flags, service):
        with flags(freshness_enabled=False):
            hits = await service.search("foo")
            # ... assert Phase-2-equivalent behavior

The `flags` fixture returns a context-manager factory that temporarily sets
attributes on a shared MnemoConfig instance and restores them on exit.

Design notes
------------
- Phase 3 config fields may not exist yet on MnemoConfig. The factory detects
  this via ``hasattr`` and:
    * records the original value if present,
    * otherwise marks the attribute as "missing" so the teardown restores the
      instance to its original shape (no leaked dynamic attributes).
- Tests that depend on a not-yet-landed flag should gate on
  ``flag_available(cfg, "freshness_enabled")`` and call ``pytest.skip`` if
  absent — the *framework* is valid before the *feature* is implemented.
- We do NOT mock MnemoConfig — tests set real attributes on a real instance.
  This respects the project red-line "no mocks" and exercises whatever
  downstream code reads the flag via getattr.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mnemo.config import MnemoConfig
from mnemo.models.knowledge import Base
from mnemo.services.knowledge_service import KnowledgeService


PHASE3_FLAG_NAMES: tuple[str, ...] = (
    "write_gate_enabled",
    "freshness_enabled",
    "state_machine_enabled",
    "feedback_loop_enabled",
    "contradiction_pair_enabled",
    "context_aware_rank_enabled",
)


PHASE3_FLAG_DEFAULTS: dict[str, bool] = {
    "write_gate_enabled": True,
    "freshness_enabled": True,
    "state_machine_enabled": True,
    "feedback_loop_enabled": True,
    "contradiction_pair_enabled": True,
    "context_aware_rank_enabled": False,
}


_SENTINEL = object()


def flag_available(cfg: MnemoConfig, name: str) -> bool:
    """Return True when the flag has been declared as a MnemoConfig field.

    We check the *model fields* (pydantic declaration) — not ``hasattr`` —
    because pydantic v2 raises on unknown attribute assignment, which is the
    behavior we want to detect before the feature lands.
    """
    return name in type(cfg).model_fields


@pytest.fixture
def base_config() -> MnemoConfig:
    """Fresh MnemoConfig with env vars disabled — deterministic across tests."""
    # ``_env_file=None`` ensures a user's shell env doesn't pollute defaults.
    return MnemoConfig(_env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def flags(base_config: MnemoConfig) -> Iterator[Any]:
    """Context-manager factory that patches MnemoConfig flag attributes.

    Example:
        with flags(freshness_enabled=False, state_machine_enabled=False):
            ... assert Phase-2 fallback behavior
    """

    @contextmanager
    def _patch(**overrides: Any) -> Iterator[MnemoConfig]:
        originals: dict[str, Any] = {}
        unknown: list[str] = []
        for name, value in overrides.items():
            if not flag_available(base_config, name):
                unknown.append(name)
                continue
            originals[name] = getattr(base_config, name, _SENTINEL)
            setattr(base_config, name, value)
        if unknown:
            # Restore anything we did set before raising.
            for name, prev in originals.items():
                if prev is _SENTINEL:
                    # Should be unreachable because flag_available passed.
                    continue
                setattr(base_config, name, prev)
            pytest.skip(
                "Phase 3 flag(s) not yet declared on MnemoConfig: "
                f"{', '.join(sorted(unknown))}"
            )
        try:
            yield base_config
        finally:
            for name, prev in originals.items():
                setattr(base_config, name, prev)

    yield _patch


@pytest_asyncio.fixture
async def flag_service(
    tmp_path: Path, base_config: MnemoConfig
) -> AsyncIterator[tuple[KnowledgeService, MnemoConfig]]:
    """KnowledgeService wired to a fresh SQLite DB + the flag-aware config.

    Feature tests that need end-to-end coverage (MCP tool return value,
    rerank path, etc.) should prefer this fixture over ``service`` from
    test_services.py because it carries the flag-carrying config.

    No mocks — real engine, real schema, real KnowledgeService.
    """
    db_path = tmp_path / "mnemo.db"
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
    try:
        yield service, base_config
    finally:
        await engine.dispose()


@pytest.fixture(params=[True, False], ids=["on", "off"])
def flag_state(request) -> bool:
    """Parametrize a test across flag-on / flag-off.

    Feature testers write one test body and this fixture runs it twice:

        @pytest.mark.phase3
        async def test_write_gate(flag_state, flags, flag_service):
            service, cfg = flag_service
            with flags(write_gate_enabled=flag_state):
                ... # assert behavior consistent with flag_state
    """
    return bool(request.param)
