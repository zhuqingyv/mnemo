"""Database engine and session management."""

import sqlite_vec
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mnemo.config import MnemoConfig

VECTOR_DIM = 1024

_engine = None
_session_factory = None


def _load_sqlite_vec(dbapi_conn, _connection_record) -> None:
    """Attach sqlite-vec to every new connection.

    aiosqlite runs its real sqlite3 connection in a background thread, so its
    Connection wrapper exposes async methods. SQLAlchemy's async adapter
    fires this sync connect listener from inside a greenlet-spawn context
    and provides ``await_()`` as a bridge — we use it to toggle extension
    loading and invoke ``sqlite_vec.load`` off the background thread.
    """
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


def _enable_wal(dbapi_conn, _connection_record) -> None:
    """Enable SQLite WAL mode on every new connection.

    WAL lets multiple readers run concurrently with a single writer, which is
    the right profile for the HTTP server (shared engine, many small
    requests). Follows the same aiosqlite-bridge pattern as
    ``_load_sqlite_vec``: on the async driver we dispatch a sync callback
    into aiosqlite's background thread via ``await_()``.
    """

    def _do_pragma(sync_conn) -> None:
        cursor = sync_conn.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.fetchall()
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()

    aiosqlite_conn = getattr(dbapi_conn, "_connection", None)
    if aiosqlite_conn is None:
        _do_pragma(dbapi_conn)
        return

    dbapi_conn.await_(aiosqlite_conn._execute(_do_pragma, aiosqlite_conn._conn))


def get_engine(config: MnemoConfig | None = None):
    global _engine
    if _engine is None:
        if config is None:
            config = MnemoConfig()
        _engine = create_async_engine(
            config.database_url,
            echo=False,
        )
        event.listen(_engine.sync_engine, "connect", _enable_wal)
        event.listen(_engine.sync_engine, "connect", _load_sqlite_vec)
    return _engine


def get_session_factory(config: MnemoConfig | None = None) -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        engine = get_engine(config)
        _session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return _session_factory


async def init_db(config: MnemoConfig | None = None):
    """Create all tables including FTS5 and sqlite-vec virtual tables."""
    from mnemo.models.knowledge import Base
    # Side-effect import: registers MonitorEvent on Base.metadata so
    # create_all picks up the monitor_event table.
    from mnemo.monitor import models as _monitor_models  # noqa: F401

    engine = get_engine(config)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts
                USING fts5(title, summary, content, tags, knowledge_id UNINDEXED)
                """
            )
        )
        await conn.execute(
            text(
                f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_vec_idx
                USING vec0(
                    knowledge_id INTEGER PRIMARY KEY,
                    embedding FLOAT[{VECTOR_DIM}]
                )
                """
            )
        )


async def reset_engine():
    """Reset engine and session factory (for testing)."""
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
    _engine = None
    _session_factory = None


__all__ = [
    "VECTOR_DIM",
    "get_engine",
    "get_session_factory",
    "init_db",
    "reset_engine",
]
