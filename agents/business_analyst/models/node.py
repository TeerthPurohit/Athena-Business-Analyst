from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, String, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agents.business_analyst.models.base import Base

class BaNode(Base):
    __tablename__ = "ba_node"
    __table_args__ = (
        Index("ix_ba_node_org_project", "org_id", "project_id"),
    )

    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("ba_project.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[str] = mapped_column(String(255), primary_key=True)  # Normalized ID (e.g. type:key)
    org_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(100), nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    attrs: Mapped[dict | None] = mapped_column(JSONB(), nullable=True)
    projection_algo_version: Mapped[int] = mapped_column(nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
