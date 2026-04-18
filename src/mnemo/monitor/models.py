"""Monitor subsystem ORM models.

MonitorEvent is the collection layer table (task #51). MonitorHealth /
MonRuleState / AlertHistory are the storage layer tables (task #55). All
four share the knowledge Base so init_db picks them up via
Base.metadata.create_all.

Schema references:
- MonitorEvent: docs/phase3/MONITOR_DESIGN.md §3.1
- MonitorHealth: task #55 spec (knowledge-base health snapshot — diverges
  from design §3.2 KV-table variant; storage-layer task spec is the
  authority for the field set)
- MonRuleState: design §3.3
- AlertHistory: task #55 spec (simplified vs design §3.4)
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mnemo.models.knowledge import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


RESULT_SUMMARY_MAX_CHARS = 500


class MonitorEvent(Base):
    __tablename__ = "monitor_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    params_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    result_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, index=True
    )
    actor: Mapped[str] = mapped_column(
        String(255), nullable=False, default="agent:unknown", index=True
    )
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Phase-B fields — design §3.1. Nullable / defaulted so the collector may
    # populate only the task-spec subset without breaking INSERTs.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    args_digest: Mapped[str | None] = mapped_column(String(32), nullable=True)
    knowledge_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    result_meta: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_monitor_event_tool_created", "tool_name", "created_at"),
        Index(
            "ix_monitor_event_session_tool_created",
            "session_id",
            "tool_name",
            "created_at",
        ),
        Index(
            "ix_monitor_event_tool_digest_created",
            "tool_name",
            "args_digest",
            "created_at",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<MonitorEvent id={self.id} tool={self.tool_name!r} "
            f"status={self.status} latency_ms={self.latency_ms:.1f}>"
        )


class MonitorHealth(Base):
    """Periodic knowledge-base health snapshot (task #55 field set).

    Each row is an immutable point-in-time rollup written by the detector
    agent (e.g. hourly). Detectors read the most recent row to drive
    ``health.stale_ratio`` / ``health.no_writes`` / ``health.high_empty_ratio``
    rule decisions.
    """

    __tablename__ = "monitor_health"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    total_knowledge: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stale: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    archived: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_search_latency_ms: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    empty_search_ratio: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, index=True
    )

    def __repr__(self) -> str:
        return (
            f"<MonitorHealth id={self.id} total={self.total_knowledge} "
            f"active={self.active} stale={self.stale} archived={self.archived}>"
        )


class MonRuleState(Base):
    """Rule-engine cross-tick state (cooldown / streak / last-scanned-id)."""

    __tablename__ = "mon_rule_state"

    rule_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    domain: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    last_triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    trigger_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    suppressed_until: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    state_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<MonRuleState rule={self.rule_id} domain={self.domain} "
            f"triggers={self.trigger_count}>"
        )


class AlertHistory(Base):
    """Alert firings — audit log + cooldown / dedup source (task #55 spec)."""

    __tablename__ = "alert_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    notified_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, index=True
    )
    cooldown_until: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    __table_args__ = (
        Index("ix_alert_history_rule_notified", "rule_id", "notified_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<AlertHistory id={self.id} rule={self.rule_id} "
            f"severity={self.severity}>"
        )


__all__ = [
    "MonitorEvent",
    "MonitorHealth",
    "MonRuleState",
    "AlertHistory",
    "RESULT_SUMMARY_MAX_CHARS",
]
