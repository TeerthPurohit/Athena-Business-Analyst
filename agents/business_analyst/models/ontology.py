import uuid
from datetime import datetime, timezone
from sqlalchemy import DateTime, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agents.business_analyst.models.base import Base


class BaOntologyType(Base):
    __tablename__ = "ba_ontology_type"
    __table_args__ = (
        # Three partial indexes per architecture_plan.md §4.3 (avoids UNIQUE(key, org_id) NULL comparison bug)
        Index("ix_ba_ontology_global_key", "key", unique=True, postgresql_where=text("org_id IS NULL")),
        Index("ix_ba_ontology_tenant_key", "key", "org_id", unique=True, postgresql_where=text("org_id IS NOT NULL")),
        Index("ix_ba_ontology_org_id", "org_id", postgresql_where=text("org_id IS NOT NULL")),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    org_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # NULL = global
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)  # node / edge / attribute
    schema_def: Mapped[dict | None] = mapped_column(JSONB(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
