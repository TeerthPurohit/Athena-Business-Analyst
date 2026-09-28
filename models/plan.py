import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class Plan(Base):
    """One of the platform's subscription tiers (Starter/Team/Business —
    Enterprise is "Custom" per the pricing sheet, no fixed row here).

    Entitlement columns mirror the pricing workbook's "Public Pricing and Core
    Entitlements" table 1:1 (same numbers for India and Global — only
    currency/price differs by region, which is billing data owned by the Node
    backend, not modeled here). Numeric fields named to match the entitlement
    keys `app/subscription_client.py` already checks against the Node backend
    (tasks_per_month, images_per_month, web_research_runs_per_month,
    scheduled_runs_per_month, lead_records_researched_per_month).
    """

    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    slug: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    users: Mapped[int] = mapped_column(Integer, nullable=False)
    workspaces: Mapped[int] = mapped_column(Integer, nullable=False)
    workforce_bundles: Mapped[int] = mapped_column(Integer, nullable=False)
    active_agents: Mapped[int] = mapped_column(Integer, nullable=False)
    tasks_per_month: Mapped[int] = mapped_column(Integer, nullable=False)
    knowledge_storage_gb: Mapped[int] = mapped_column(Integer, nullable=False)
    knowledge_sources: Mapped[int] = mapped_column(Integer, nullable=False)
    active_workflows: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_runs_per_month: Mapped[int] = mapped_column(Integer, nullable=False)
    standard_integrations: Mapped[int] = mapped_column(Integer, nullable=False)
    images_per_month: Mapped[int] = mapped_column(Integer, nullable=False)
    web_research_runs_per_month: Mapped[int] = mapped_column(Integer, nullable=False)
    lead_records_researched_per_month: Mapped[int] = mapped_column(Integer, nullable=False)
    audit_history_days: Mapped[int] = mapped_column(Integer, nullable=False)
    support_tier: Mapped[str] = mapped_column(String(100), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return f"<Plan(slug='{self.slug}')>"


class PlanModelEntitlement(Base):
    """One row = this model is included on this plan. Curated via
    POST/DELETE /api/admin/plans/{plan_id}/models — starts with every model
    attached to every plan (seed_plans.py), narrowed down from there."""

    __tablename__ = "plan_model_entitlements"

    plan_id: Mapped[str] = mapped_column(String(36), ForeignKey("plans.id", ondelete="CASCADE"), primary_key=True)
    model_catalog_id: Mapped[str] = mapped_column(String(36), ForeignKey("model_catalog.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
