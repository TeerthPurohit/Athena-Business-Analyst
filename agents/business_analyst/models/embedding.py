import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional

from sqlalchemy import DateTime, Index, String, JSON
from sqlalchemy.orm import Mapped, mapped_column

from agents.business_analyst.models.base import Base


class BaEmbedding(Base):
    __tablename__ = "ba_embeddings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    org_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)  # org_id NOT NULL for isolation
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    embedding: Mapped[Optional[List[float]]] = mapped_column(JSON, nullable=True)  # Model-dependent vector dimensions
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_ba_embeddings_entity_unique", "entity_type", "entity_id", unique=True),
        Index("ix_ba_embeddings_org_lookup", "org_id", "entity_type"),
    )
