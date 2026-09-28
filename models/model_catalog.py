import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class ModelCatalog(Base):
    """One row per model a provider exposes, with the super-admin on/off toggle.
    Platform-wide — one row per model, shared by the whole Kaynetics install,
    not per-tenant. Which models a *plan* gets access to is a separate mapping
    (see models/plan.py PlanModelEntitlement).

    category: "llm" | "scraping" | "image" (matches ProviderCredential.category)
    is_enabled: the one-click toggle; is_default: which enabled model a category resolves to.
    """

    __tablename__ = "model_catalog"
    __table_args__ = (
        UniqueConstraint(
            "category", "provider", "model_name",
            name="uq_model_catalog_category_provider_model",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    meta: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return f"<ModelCatalog(category='{self.category}', provider='{self.provider}', model_name='{self.model_name}')>"
