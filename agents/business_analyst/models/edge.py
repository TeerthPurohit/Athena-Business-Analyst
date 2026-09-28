from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, String, Index
from sqlalchemy.orm import Mapped, mapped_column

from agents.business_analyst.models.base import Base

class BaEdge(Base):
    __tablename__ = "ba_edge"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "source_node_id"],
            ["ba_node.project_id", "ba_node.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "target_node_id"],
            ["ba_node.project_id", "ba_node.id"],
            ondelete="CASCADE",
        ),
        Index("ix_ba_edge_org_project", "org_id", "project_id"),
    )

    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("ba_project.id", ondelete="CASCADE"), primary_key=True)
    source_node_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    target_node_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    relationship: Mapped[str] = mapped_column(String(100), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    projection_algo_version: Mapped[int] = mapped_column(nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
