import uuid
from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from agents.business_analyst.models.base import Base

class BaSource(Base):
    __tablename__ = "ba_source"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ba_project.id"), nullable=False, index=True
    )
    org_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    tier: Mapped[str] = mapped_column(String(50), nullable=False)
    ref: Mapped[str | None] = mapped_column(Text(), nullable=True)
    stakeholder_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
