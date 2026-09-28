import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, Identity, Index, String, BigInteger, Text, false
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agents.business_analyst.models.base import Base

class BaFact(Base):
    __tablename__ = "ba_fact"
    __table_args__ = (
        Index("ix_ba_fact_project_subject", "project_id", "subject_type", "subject_key"),
        Index("ix_ba_fact_project_seq", "project_id", "seq"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ba_project.id"), nullable=False, index=True
    )
    org_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False, unique=True)
    subject_type: Mapped[str] = mapped_column(String(100), nullable=False)
    subject_key: Mapped[str] = mapped_column(String(255), nullable=False)
    predicate: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[dict | None] = mapped_column(JSONB(), nullable=True)
    object_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    object_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_id: Mapped[str] = mapped_column(String(36), ForeignKey("ba_source.id"), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    human_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    asserted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    asserted_by: Mapped[str] = mapped_column(String(255), nullable=False)
    replaces: Mapped[str | None] = mapped_column(String(36), ForeignKey("ba_fact.id"), nullable=True)
