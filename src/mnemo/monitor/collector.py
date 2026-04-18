"""MCP tool collector — @monitor_tool decorator + async writer.

Responsibilities (docs/phase3/MONITOR_DESIGN.md §2.1-§2.2):
- Intercept MCP tool calls, capture tool_name / params / result_summary /
  latency / status / error_type.
- Persist a MonitorEvent row on a background task so the tool's return value
  is never blocked.
- Swallow any collection failure as logger.warning — business tool callers
  must never see a collection-layer exception.

Not in scope for task #51:
- The full result_meta schema (Phase-B domain fields) — the decorator stores
  whatever record_payload() the wrapped tool emits, but doesn't enforce a
  per-tool schema.
- The jieba-based args_digest — added in task #55 when search / search_by_tag
  rules land. For now the digest is left NULL.
- Queue / backpressure counters — the MVP fires asyncio.create_task per call;
  if the writer can't keep up, rows fall on the floor with a warning rather
  than blocking the tool.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mnemo.monitor.models import RESULT_SUMMARY_MAX_CHARS, MonitorEvent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# module-level writer wiring
#
# The decorator is applied at import-time on MCP tool functions (server.py).
# Session factory / enabled flag are injected from the MCP bootstrap, so tests
# can wire an in-memory engine without importing the global MnemoConfig.
# ---------------------------------------------------------------------------

_session_factory: async_sessionmaker[AsyncSession] | None = None
_enabled: bool = True

# Process-level fallback session id — MONITOR_DESIGN §3.1 requires
# session-aware rules (empty_streak / loop_suspect) to keep working in the
# CLI / direct-service path where no FastMCP Context exists. Frozen at
# import so every event in the same process shares the same fallback id.
_PROC_SESSION_ID: str = f"proc:{os.getpid()}:{int(time.time())}"

# Per-call payload slot — record_payload() inside a tool writes here and the
# decorator drains it when the tool returns. ContextVar keeps concurrent tool
# calls isolated (each asyncio Task gets its own view).
_payload_ctx: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "mnemo_monitor_payload", default=None
)


def configure(
    *,
    session_factory: async_sessionmaker[AsyncSession] | None,
    enabled: bool = True,
) -> None:
    """Wire the module to a SQLAlchemy async session factory.

    Called once from the MCP bootstrap (server._bootstrap) and also from test
    fixtures. When ``session_factory`` is None OR ``enabled`` is False the
    decorator becomes a passthrough.
    """
    global _session_factory, _enabled
    _session_factory = session_factory
    _enabled = enabled


def is_enabled() -> bool:
    return _enabled and _session_factory is not None


# ---------------------------------------------------------------------------
# payload api — tool function calls this to attach business-dimension fields
# ---------------------------------------------------------------------------

def record_payload(**fields: Any) -> None:
    """Attach business-dimension fields to the in-flight monitor event.

    Safe to call outside a monitored context — silently no-ops so callers
    don't need a try/except. Recognised top-level keys are lifted into
    dedicated columns; everything else is merged into result_meta JSON.
    """
    slot = _payload_ctx.get()
    if slot is None:
        return
    slot.update(fields)


# ---------------------------------------------------------------------------
# decorator
# ---------------------------------------------------------------------------

_LIFTED_KEYS = (
    "status",
    "error_type",
    "knowledge_id",
    "actor",
    "session_id",
    # Private hint keys planted by the wrapper — already consumed above;
    # never leak them into result_meta.
    "_ctx_actor",
    "_ctx_session_id",
)


def _infer_from_mcp_context() -> tuple[str | None, str | None]:
    """Best-effort pull of (actor, session_id) from the live FastMCP Context.

    FastMCP exposes ``client_id`` / ``session_id`` on the request context when
    the tool is invoked through an MCP transport. Outside that path (CLI /
    pytest / direct service call) ``get_context()`` raises — swallow and let
    the proc-level fallback take over.
    """
    try:
        from fastmcp.server.dependencies import get_context
    except Exception:
        return None, None
    try:
        ctx = get_context()
    except Exception:
        return None, None
    actor: str | None = None
    session_id: str | None = None
    try:
        cid = getattr(ctx, "client_id", None)
        if isinstance(cid, str) and cid:
            actor = f"agent:{cid}"
    except Exception:
        pass
    try:
        sid = getattr(ctx, "session_id", None)
        if isinstance(sid, str) and sid:
            session_id = sid
    except Exception:
        pass
    return actor, session_id


def monitor_tool(
    name: str | None = None,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Wrap a FastMCP @mcp.tool async function to emit a MonitorEvent.

    Usage:

        @mcp.tool
        @monitor_tool(name="search")
        async def search(query: str, ...) -> str:
            ...

    The inner function is untouched — its return value, exception propagation,
    and signature flow through unchanged. Collection failures are logged but
    never re-raised.
    """

    def _decorator(
        fn: Callable[..., Awaitable[Any]],
    ) -> Callable[..., Awaitable[Any]]:
        tool_name = name or fn.__name__

        @functools.wraps(fn)
        async def _wrapper(*args: Any, **kwargs: Any) -> Any:
            if not is_enabled():
                return await fn(*args, **kwargs)

            payload: dict[str, Any] = {}
            token = _payload_ctx.set(payload)
            started = time.perf_counter()
            status = "ok"
            error_type: str | None = None
            result: Any = None
            result_summary = ""
            try:
                result = await fn(*args, **kwargs)
                return result
            except Exception as exc:
                status = "error"
                error_type = type(exc).__name__
                result_summary = str(exc)[:500]
                raise
            finally:
                latency_ms = (time.perf_counter() - started) * 1000.0
                _payload_ctx.reset(token)
                # Capture FastMCP request context *now*, inside the request's
                # ContextVar scope — the background writer runs in a separate
                # task and the MCP dispatcher may have already torn the ctx
                # down by the time the writer fires. Payload overrides (when a
                # tool explicitly record_payload'd actor/session_id) still win
                # in _write_event.
                ctx_actor, ctx_session = _infer_from_mcp_context()
                if ctx_actor and "actor" not in payload:
                    payload.setdefault("_ctx_actor", ctx_actor)
                if ctx_session and "session_id" not in payload:
                    payload.setdefault("_ctx_session_id", ctx_session)
                try:
                    params = _serialize_params(fn, args, kwargs)
                    summary = _summarize_result(result) if status == "ok" else result_summary
                    _schedule_write(
                        tool_name=tool_name,
                        params=params,
                        result_summary=summary,
                        latency_ms=latency_ms,
                        status=payload.get("status", status),
                        error_type=payload.get("error_type", error_type),
                        payload=payload,
                    )
                except Exception:
                    logger.warning(
                        "monitor collector pre-write failed for tool=%s",
                        tool_name,
                        exc_info=True,
                    )

        return _wrapper

    return _decorator


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _serialize_params(
    fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> str:
    """Render positional+keyword args as a JSON dict keyed by parameter name.

    Falls back to ``{"__repr__": str(kwargs)}`` when the signature can't be
    bound (e.g. wrapped builtins) — better a rough hint than dropping the
    event.
    """
    try:
        sig = inspect.signature(fn)
        bound = sig.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        return json.dumps(bound.arguments, default=_json_default, ensure_ascii=False)
    except Exception:
        try:
            return json.dumps(
                {"args": list(args), "kwargs": kwargs},
                default=_json_default,
                ensure_ascii=False,
            )
        except Exception:
            return json.dumps({"__repr__": repr(kwargs)[:500]})


def _summarize_result(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        text = result
    else:
        try:
            text = json.dumps(result, default=_json_default, ensure_ascii=False)
        except Exception:
            text = repr(result)
    if len(text) > RESULT_SUMMARY_MAX_CHARS:
        return text[: RESULT_SUMMARY_MAX_CHARS - 3] + "..."
    return text


def _json_default(obj: Any) -> Any:
    try:
        return str(obj)
    except Exception:
        return f"<unserializable {type(obj).__name__}>"


def _schedule_write(
    *,
    tool_name: str,
    params: str,
    result_summary: str,
    latency_ms: float,
    status: str,
    error_type: str | None,
    payload: dict[str, Any],
) -> None:
    """Fire-and-forget the DB write. Never blocks the caller."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning(
            "monitor collector: no running loop when scheduling tool=%s",
            tool_name,
        )
        return

    task = loop.create_task(
        _write_event(
            tool_name=tool_name,
            params=params,
            result_summary=result_summary,
            latency_ms=latency_ms,
            status=status,
            error_type=error_type,
            payload=payload,
        )
    )
    # Detach — we don't await, but attaching a done-callback keeps the
    # "Task was destroyed but it is pending!" warning from firing in tests and
    # surfaces any late exception through logger.warning rather than the
    # default exception handler.
    task.add_done_callback(_log_task_exception)


def _log_task_exception(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("monitor writer task failed: %r", exc)


async def _write_event(
    *,
    tool_name: str,
    params: str,
    result_summary: str,
    latency_ms: float,
    status: str,
    error_type: str | None,
    payload: dict[str, Any],
) -> None:
    if _session_factory is None:
        return

    # Wrapper captured the FastMCP Context before scheduling this task and
    # stashed it under these private hint keys; fall back to a late lookup
    # only when the wrapper didn't set them (direct writer call in tests).
    ctx_actor = payload.get("_ctx_actor")
    ctx_session = payload.get("_ctx_session_id")
    if ctx_actor is None and ctx_session is None:
        ctx_actor, ctx_session = _infer_from_mcp_context()

    actor = payload.get("actor")
    if not isinstance(actor, str) or not actor:
        env_actor = os.environ.get("MNEMO_ACTOR")
        if isinstance(env_actor, str) and env_actor:
            actor = env_actor
        elif isinstance(ctx_actor, str) and ctx_actor:
            actor = ctx_actor
        else:
            actor = "agent:unknown"

    session_id = payload.get("session_id")
    if session_id is None:
        session_id = ctx_session if isinstance(ctx_session, str) and ctx_session else _PROC_SESSION_ID
    elif not isinstance(session_id, str):
        session_id = str(session_id)

    knowledge_id = payload.get("knowledge_id")
    if knowledge_id is not None and not isinstance(knowledge_id, int):
        try:
            knowledge_id = int(knowledge_id)
        except (TypeError, ValueError):
            knowledge_id = None

    args_digest = payload.get("args_digest")
    if args_digest is not None and not isinstance(args_digest, str):
        args_digest = str(args_digest)

    meta = {k: v for k, v in payload.items() if k not in _LIFTED_KEYS and k != "args_digest"}
    result_meta: str | None
    if meta:
        try:
            result_meta = json.dumps(meta, default=_json_default, ensure_ascii=False)
        except Exception:
            result_meta = None
    else:
        result_meta = None

    event = MonitorEvent(
        tool_name=tool_name,
        params_json=params,
        result_summary=result_summary,
        latency_ms=latency_ms,
        actor=actor,
        session_id=session_id,
        status=status,
        error_type=error_type,
        args_digest=args_digest,
        knowledge_id=knowledge_id,
        result_meta=result_meta,
    )

    try:
        async with _session_factory() as session:
            session.add(event)
            await session.commit()
    except Exception:
        logger.warning(
            "monitor writer: failed to persist event tool=%s status=%s",
            tool_name,
            status,
            exc_info=True,
        )


__all__ = ["configure", "is_enabled", "monitor_tool", "record_payload"]
