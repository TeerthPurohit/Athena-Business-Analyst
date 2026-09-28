"""Create Athena source-evidence tables.

Revision ID: 0001_athena_source_evidence
Revises:
Create Date: 2026-09-22
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_athena_source_evidence"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "athena_source",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "project_id", "content_hash", name="uq_athena_source_content"),
    )
    op.create_index("ix_athena_source_tenant_captured", "athena_source", ["org_id", "project_id", "captured_at"])
    op.create_table(
        "athena_source_content",
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("storage_ref", sa.Text(), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("extraction_version", sa.String(length=100), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["athena_source.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("source_id"),
    )
    op.create_index("ix_athena_source_content_tenant", "athena_source_content", ["org_id", "project_id"])
    op.create_table(
        "athena_source_span",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("start_char", sa.Integer(), nullable=False),
        sa.Column("end_char", sa.Integer(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("normalized_text_hash", sa.String(length=64), nullable=False),
        sa.Column("speaker", sa.String(length=255), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("turn_id", sa.String(length=255), nullable=True),
        sa.CheckConstraint("end_char >= start_char", name="ck_athena_source_span_offsets"),
        sa.ForeignKeyConstraint(["source_id"], ["athena_source.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "ordinal", name="uq_athena_source_span_ordinal"),
    )
    op.create_index("ix_athena_source_span_tenant_source", "athena_source_span", ["org_id", "project_id", "source_id"])


def downgrade() -> None:
    op.drop_index("ix_athena_source_span_tenant_source", table_name="athena_source_span")
    op.drop_table("athena_source_span")
    op.drop_index("ix_athena_source_content_tenant", table_name="athena_source_content")
    op.drop_table("athena_source_content")
    op.drop_index("ix_athena_source_tenant_captured", table_name="athena_source")
    op.drop_table("athena_source")
