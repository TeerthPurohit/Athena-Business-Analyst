"""Standalone relational evidence contract for Athena."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Metadata owned solely by the standalone Athena service."""


class AthenaSource(Base):
    __tablename__ = "athena_source"
    __table_args__ = (
        UniqueConstraint("org_id", "project_id", "content_hash", name="uq_athena_source_content"),
        Index("ix_athena_source_tenant_captured", "org_id", "project_id", "captured_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    org_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class AthenaSourceContent(Base):
    __tablename__ = "athena_source_content"
    __table_args__ = (Index("ix_athena_source_content_tenant", "org_id", "project_id"),)

    source_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("athena_source.id", ondelete="RESTRICT"), primary_key=True
    )
    org_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    storage_ref: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    extraction_version: Mapped[str] = mapped_column(String(100), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class AthenaSourceSpan(Base):
    __tablename__ = "athena_source_span"
    __table_args__ = (
        CheckConstraint("end_char >= start_char", name="ck_athena_source_span_offsets"),
        UniqueConstraint("source_id", "ordinal", name="uq_athena_source_span_ordinal"),
        Index("ix_athena_source_span_tenant_source", "org_id", "project_id", "source_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("athena_source.id", ondelete="RESTRICT"), nullable=False
    )
    org_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    start_char: Mapped[int] = mapped_column(Integer, nullable=False)
    end_char: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    speaker: Mapped[str | None] = mapped_column(String(255), nullable=True)
    timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    turn_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
