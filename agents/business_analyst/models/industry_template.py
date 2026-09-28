"""BaIndustryTemplate model — global-vs-tenant starting points for new projects.

Flat bag of defaults (no versioning, no DSL). A template is copied onto project creation;
it has no ongoing relationship to the project after that copy.

Reuses the three-partial-index pattern from BaCapability (§4.3):
  UNIQUE(key) WHERE org_id IS NULL   — one global default per key
  UNIQUE(key, org_id) WHERE NOT NULL — one tenant override per key
  INDEX(org_id) WHERE NOT NULL       — efficient tenant listing
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import DateTime, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agents.business_analyst.models.base import Base


class BaIndustryTemplate(Base):
    __tablename__ = "ba_industry_template"
    __table_args__ = (
        Index(
            "ix_ba_industry_tpl_global_key", "key",
            unique=True, postgresql_where=text("org_id IS NULL"),
        ),
        Index(
            "ix_ba_industry_tpl_tenant_key", "key", "org_id",
            unique=True, postgresql_where=text("org_id IS NOT NULL"),
        ),
        Index(
            "ix_ba_industry_tpl_org_id", "org_id",
            postgresql_where=text("org_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    org_id: Mapped[str | None] = mapped_column(String(36), nullable=True)   # NULL = global
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    default_instructions: Mapped[str | None] = mapped_column(Text(), nullable=True)
    default_must_have: Mapped[str | None] = mapped_column(Text(), nullable=True)
    default_should_have: Mapped[str | None] = mapped_column(Text(), nullable=True)
    # Lists of BaOntologyType.key / BaCapability.key values relevant to this industry
    ontology_type_keys: Mapped[list | None] = mapped_column(JSONB(), nullable=True)
    capability_keys: Mapped[list | None] = mapped_column(JSONB(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
