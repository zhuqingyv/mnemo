"""Load all fixture knowledge into a fresh mnemo database.

Usage:
    MNEMO_DATA_DIR=/tmp/mnemo-test-fixtures python tests/fixtures/load_fixtures.py

Reads every JSON file under ``tests/fixtures/knowledge/`` and inserts each
entry via :class:`KnowledgeService`. Same-title entries collapse into a
version chain (the service handles it), content-hash duplicates emit a
warning but are still inserted, and any unexpected failure is logged and
skipped so one bad record cannot abort the whole load.

A second pass re-applies wikilinks/related edges for every entry so that
forward references (target came later in insertion order) resolve.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# Make ``src`` importable when running as a plain script.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from sqlalchemy import text  # noqa: E402

from mnemo.config import MnemoConfig  # noqa: E402
from mnemo.db import get_engine, get_session_factory, init_db, reset_engine  # noqa: E402
from mnemo.repository import knowledge_repository as kr  # noqa: E402
from mnemo.repository import relation_repository as rr  # noqa: E402
from mnemo.services.knowledge_service import (  # noqa: E402
    MANUAL_RELATION_TYPE,
    WIKILINK_RELATION_TYPE,
    KnowledgeService,
)


FIXTURE_DIR = Path(__file__).parent / "knowledge"


def _reset_data_dir(data_dir: Path) -> None:
    """Remove any existing sqlite file so ``init_db`` starts fresh."""
    data_dir.mkdir(parents=True, exist_ok=True)
    for name in ("mnemo.db", "mnemo.db-journal", "mnemo.db-wal", "mnemo.db-shm"):
        p = data_dir / name
        if p.exists():
            p.unlink()


def _load_all_entries() -> list[tuple[str, dict]]:
    """Return ``[(category_file, entry), ...]`` in stable filename order."""
    entries: list[tuple[str, dict]] = []
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            print(f"[warn] {path.name} is not a list, skipping")
            continue
        for item in data:
            entries.append((path.name, item))
    return entries


async def _insert_all(
    service: KnowledgeService, entries: list[tuple[str, dict]]
) -> tuple[int, int, list[tuple[str, str, str]]]:
    """First pass — create every knowledge entry.

    Returns ``(inserted, skipped, failures)``.
    """
    inserted = 0
    skipped = 0
    failures: list[tuple[str, str, str]] = []

    for filename, item in entries:
        title = item.get("title") or "<no-title>"
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
        except Exception as exc:  # noqa: BLE001
            skipped += 1
            failures.append((filename, title, repr(exc)))
            print(f"[warn] insert failed: {filename} :: {title} :: {exc!r}")

    return inserted, skipped, failures


async def _reapply_relations(
    service: KnowledgeService, entries: list[tuple[str, dict]]
) -> int:
    """Second pass — re-resolve wikilinks/related for every active row.

    Forward references (target inserted later) are skipped on the first
    pass. Here we wipe existing wikilink + manual edges for the current
    active row of each title and re-apply from the raw entry.

    Returns number of titles whose edges were refreshed.
    """
    factory = service._session_factory  # noqa: SLF001 — intentional, script-local.
    refreshed = 0

    async with factory() as session:
        for _filename, item in entries:
            title = item.get("title")
            if not title:
                continue
            row = await kr.get_by_title(session, title)
            if row is None:
                continue

            # Drop previously-inserted derived edges so we don't accumulate
            # duplicates on the second pass.
            await session.execute(
                text(
                    "DELETE FROM relation "
                    "WHERE source_id = :sid AND relation_type IN (:t1, :t2)"
                ),
                {
                    "sid": row.id,
                    "t1": WIKILINK_RELATION_TYPE,
                    "t2": MANUAL_RELATION_TYPE,
                },
            )
            await session.commit()

            # Re-extract from content and re-apply the manual list.
            await service._apply_wikilinks(session, row.id, row.content)  # noqa: SLF001
            await service._apply_manual_relations(  # noqa: SLF001
                session, row.id, item.get("related") or None
            )
            refreshed += 1

    return refreshed


async def _count_active(session_factory) -> int:
    async with session_factory() as session:
        result = await session.execute(
            text("SELECT COUNT(*) FROM knowledge WHERE status = 'active'")
        )
        return int(result.scalar_one())


async def _count_total(session_factory) -> int:
    async with session_factory() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM knowledge"))
        return int(result.scalar_one())


async def _count_relations(session_factory) -> int:
    async with session_factory() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM relation"))
        return int(result.scalar_one())


async def main() -> int:
    data_dir = Path(os.environ.get("MNEMO_DATA_DIR", "/tmp/mnemo-test-fixtures"))
    os.environ["MNEMO_DATA_DIR"] = str(data_dir)

    print(f"[info] data dir: {data_dir}")
    _reset_data_dir(data_dir)

    await reset_engine()
    config = MnemoConfig(data_dir=str(data_dir))

    # Force a fresh engine bound to this data_dir.
    get_engine(config)
    session_factory = get_session_factory(config)
    await init_db(config)
    print("[info] schema initialized")

    entries = _load_all_entries()
    print(f"[info] loaded {len(entries)} entries from {FIXTURE_DIR}")

    service = KnowledgeService(session_factory=session_factory, config=config)

    inserted, skipped, failures = await _insert_all(service, entries)
    print(
        f"[info] first pass done: inserted={inserted} skipped={skipped} "
        f"total={len(entries)}"
    )

    refreshed = await _reapply_relations(service, entries)
    print(f"[info] second pass done: re-applied relations for {refreshed} titles")

    active = await _count_active(session_factory)
    total_rows = await _count_total(session_factory)
    relations = await _count_relations(session_factory)
    print(
        f"[info] db state: active={active} total_rows={total_rows} "
        f"relations={relations}"
    )

    await reset_engine()

    if failures:
        print(f"[warn] {len(failures)} failures:")
        for filename, title, err in failures[:10]:
            print(f"  - {filename} :: {title} :: {err}")
        if len(failures) > 10:
            print(f"  ... ({len(failures) - 10} more)")

    if active < 700:
        print(f"[error] expected >= 700 active entries, got {active}")
        return 1

    print(f"[ok] load complete: {active} active entries ready for queries")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
