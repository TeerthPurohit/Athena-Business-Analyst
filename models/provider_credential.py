import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class ProviderCredential(Base):
    """Encrypted API key for one (category, provider) pair. Platform-wide —
    one row per pair, shared by the whole Kaynetics install, not per-tenant.

    category: "llm" | "scraping" | "image"
    provider: e.g. "openai", "nvidia", "anthropic", "serper", "tinyfish", "nvidia_nim"
    """

    __tablename__ = "provider_credentials"
    __table_args__ = (
        UniqueConstraint(
            "category", "provider",
            name="uq_provider_credentials_category_provider",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return f"<ProviderCredential(category='{self.category}', provider='{self.provider}')>"
