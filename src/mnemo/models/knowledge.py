"""ORM models for knowledge and relations."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, LargeBinary, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """DateTime that always hands back tz-aware UTC values.

    SQLite's TEXT/DATETIME affinity strips tz on write, so a naive datetime
    comes back naive even if the ORM column is declared ``timezone=True``.
    The read-lazy stale lifecycle compares ``last_accessed_at`` against a
    tz-aware ``datetime.now(timezone.utc)``; subtracting the two raises a
    ``TypeError`` when one side is naive. This decorator re-attaches UTC on
    read so downstream arithmetic is safe.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    pass


class Knowledge(Base):
    __tablename__ = "knowledge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    tags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="global", index=True)
    project_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    source: Mapped[str | None] = mapped_column(String(512), nullable=True)
    claim_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", index=True
    )
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    extra_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True, default=None
    )

    __table_args__ = (
        Index("ix_knowledge_last_accessed", "last_accessed_at"),
    )

    outgoing_relations: Mapped[list["Relation"]] = relationship(
        "Relation",
        foreign_keys="Relation.source_id",
        back_populates="source",
        cascade="all, delete-orphan",
    )
    incoming_relations: Mapped[list["Relation"]] = relationship(
        "Relation",
        foreign_keys="Relation.target_id",
        back_populates="target",
        cascade="all, delete-orphan",
    )
    meta_entries: Mapped[list["KnowledgeMeta"]] = relationship(
        "KnowledgeMeta",
        back_populates="knowledge",
        cascade="all, delete-orphan",
    )
    events: Mapped[list["KnowledgeEvent"]] = relationship(
        "KnowledgeEvent",
        back_populates="knowledge",
        cascade="all, delete-orphan",
    )
    vectors: Mapped[list["KnowledgeVec"]] = relationship(
        "KnowledgeVec",
        back_populates="knowledge",
        cascade="all, delete-orphan",
    )
    tag_entries: Mapped[list["KnowledgeTag"]] = relationship(
        "KnowledgeTag",
        back_populates="knowledge",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Knowledge id={self.id} title={self.title!r} scope={self.scope}>"


class Relation(Base):
    __tablename__ = "relation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relation_type: Mapped[str] = mapped_column(String(64), nullable=False, default="related")
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    extra_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    source: Mapped["Knowledge"] = relationship(
        "Knowledge", foreign_keys=[source_id], back_populates="outgoing_relations"
    )
    target: Mapped["Knowledge"] = relationship(
        "Knowledge", foreign_keys=[target_id], back_populates="incoming_relations"
    )

    def __repr__(self) -> str:
        return (
            f"<Relation id={self.id} "
            f"{self.source_id}->{self.target_id} type={self.relation_type}>"
        )


class KnowledgeMeta(Base):
    __tablename__ = "knowledge_meta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    knowledge_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    knowledge: Mapped["Knowledge"] = relationship(
        "Knowledge", back_populates="meta_entries"
    )

    __table_args__ = (
        # Composite (key, knowledge_id): authority batch lookup at 500K picks
        # the single-column key-index and scans every row with that key (every
        # knowledge has one authority_score → 500K-row scan). Composite
        # collapses it to an index-range scan per id. M4 task #5 cut 100ms
        # → <5ms here.
        Index("ix_knowledge_meta_key_kid", "key", "knowledge_id"),
    )

    def __repr__(self) -> str:
        return f"<KnowledgeMeta id={self.id} kid={self.knowledge_id} key={self.key!r}>"


class KnowledgeEvent(Base):
    __tablename__ = "knowledge_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    knowledge_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge.id", ondelete="CASCADE"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    knowledge: Mapped["Knowledge"] = relationship(
        "Knowledge", back_populates="events"
    )

    def __repr__(self) -> str:
        return (
            f"<KnowledgeEvent id={self.id} kid={self.knowledge_id} "
            f"type={self.event_type}>"
        )


class KnowledgeTag(Base):
    """Flat (knowledge_id, tag) rows for O(log n) tag-search.

    Kept in sync with Knowledge.tags (JSON string) by the repository layer.
    The composite index (tag, knowledge_id) turns search_by_tag from a full
    scan + JSON parse into an index lookup. M4 500K stress: 6.5s → <50ms.
    """

    __tablename__ = "knowledge_tag"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    knowledge_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge.id", ondelete="CASCADE"), nullable=False
    )
    tag: Mapped[str] = mapped_column(String(128), nullable=False)

    knowledge: Mapped["Knowledge"] = relationship(
        "Knowledge", back_populates="tag_entries"
    )

    __table_args__ = (
        Index("ix_knowledge_tag_tag_kid", "tag", "knowledge_id"),
        Index("ix_knowledge_tag_kid", "knowledge_id"),
    )

    def __repr__(self) -> str:
        return f"<KnowledgeTag id={self.id} kid={self.knowledge_id} tag={self.tag!r}>"


class KnowledgeVec(Base):
    __tablename__ = "knowledge_vec"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    knowledge_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    knowledge: Mapped["Knowledge"] = relationship(
        "Knowledge", back_populates="vectors"
    )

    def __repr__(self) -> str:
        return (
            f"<KnowledgeVec id={self.id} kid={self.knowledge_id} "
            f"model={self.model_name!r}>"
        )
