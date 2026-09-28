"""Deliverable models for BA OS (§11.1, §11.2).

CLAUDE.md Non-Negotiables:
- BaDeliverableSpec: catalog metadata table (key, purpose, required_node_types, required_capabilities, renderer_key, review_process, versioning_strategy, approval_workflow).
  - All 8 metadata fields NOT NULL.
  - Nullable org_id (NULL = global default, non-null = tenant override).
  - Two partial unique indexes: UNIQUE(key) WHERE org_id IS NULL and UNIQUE(key, org_id) WHERE org_id IS NOT NULL.
- BaRenderer: renderer catalog mapping composite (key, output_format).
  - renderer_fn (dotted Python path), renderer_version, narration_mode ('structural' | 'narrated'), prompt_id (FK -> agent_prompt).
- BaDeliverableInstance: generated artifact instance per project.
  - frontier_seq: max(ba_fact.seq) over input node types at generation time.
  - content_ref: string pointer to blob storage (content not stored inline).
  - status: 'draft' | 'generated' | 'in_review' | 'approved' | 'stale' | 'regenerated' | 'superseded'.
"""
from datetime import datetime, timezone
import uuid
from typing import Any, Dict, List, Optional
from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from agents.business_analyst.models.base import Base


class BaDeliverableSpec(Base):
    """Catalog table for deliverable specifications."""

    __tablename__ = "ba_deliverable_spec"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    key = Column(String(100), nullable=False)
    org_id = Column(String(36), nullable=True)  # NULL = global default, non-null = tenant override
    purpose = Column(String(255), nullable=False)
    required_node_types = Column(JSONB, nullable=False)  # Array of ba_ontology_type keys
    required_capabilities = Column(JSONB, nullable=False)  # Array of ba_capability keys
    renderer_key = Column(String(100), nullable=False)
    review_process = Column(String(50), nullable=False)  # self_review | stakeholder_review | client_signoff
    versioning_strategy = Column(String(50), nullable=False)  # seq_snapshot | manual_version
    approval_workflow = Column(String(50), nullable=False)  # none | single_approver | multi_approver
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_ba_deliverable_spec_global_key", "key", unique=True, postgresql_where=(org_id.is_(None))),
        Index("ix_ba_deliverable_spec_tenant_key", "key", "org_id", unique=True, postgresql_where=(org_id.is_not(None))),
        Index("ix_ba_deliverable_spec_org_id", "org_id", unique=False, postgresql_where=(org_id.is_not(None))),
    )


class BaRenderer(Base):
    """Renderer catalog mapping deliverable_key + output_format to pure rendering functions."""

    __tablename__ = "ba_renderer"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    key = Column(String(100), nullable=False)  # deliverable key
    output_format = Column(String(50), nullable=False)  # markdown | json | html | pdf
    renderer_fn = Column(String(255), nullable=False)  # dotted Python path string
    renderer_version = Column(String(50), nullable=False, default="1.0.0")
    narration_mode = Column(String(50), nullable=False)  # structural | narrated
    prompt_id = Column(String(255), nullable=True)  # agent_prompt key (required if narrated)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_ba_renderer_key_format", "key", "output_format", unique=True),
    )


class BaDeliverableInstance(Base):
    """Generated artifact instance per project."""

    __tablename__ = "ba_deliverable_instance"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String(36), ForeignKey("ba_project.id", ondelete="CASCADE"), nullable=False)
    org_id = Column(String(36), nullable=False)
    deliverable_key = Column(String(100), nullable=False)
    frontier_seq = Column(BigInteger, nullable=False)  # max(ba_fact.seq) across input nodes
    renderer_version = Column(String(50), nullable=False)
    output_format = Column(String(50), nullable=False)
    content_ref = Column(Text, nullable=False)  # Pointer / file path / blob key
    status = Column(String(50), nullable=False, default="generated")  # draft | generated | in_review | approved | stale | regenerated | superseded
    approved_by = Column(String(255), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_ba_deliverable_instance_proj_org", "project_id", "org_id"),
        Index("ix_ba_deliverable_instance_key", "project_id", "deliverable_key"),
    )
