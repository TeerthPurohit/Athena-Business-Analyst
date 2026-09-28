from models.base import Base as RootBase

from agents.business_analyst.models import Base, BaFact, BaProject, BaSource


def test_ba_uses_its_own_base_not_the_shared_root_base():
    assert Base is not RootBase
    # The whole point of the separate Base: Alembic's autogenerate (scoped to Base.metadata)
    # must never be able to see a root table and emit a spurious CREATE TABLE for it.
    assert "ba_fact" not in RootBase.metadata.tables


def test_table_names():
    assert BaProject.__tablename__ == "ba_project"
    assert BaSource.__tablename__ == "ba_source"
    assert BaFact.__tablename__ == "ba_fact"


def test_ba_fact_columns():
    cols = set(BaFact.__table__.columns.keys())
    assert cols == {
        "id", "project_id", "org_id", "seq", "subject_type", "subject_key",
        "predicate", "value", "object_type", "object_key", "source_id",
        "run_id", "human_approval", "asserted_at", "asserted_by", "replaces",
    }
    # no retracted_by — see design doc's "Retraction is a forward-only pointer" correction
    assert "retracted_by" not in cols


def test_ba_fact_has_no_updated_at_style_column():
    # seq is the only ordering tiebreaker; asserted_at is display-only and there is no
    # updated_at at all, because rows are never updated.
    assert "updated_at" not in BaFact.__table__.columns.keys()


def test_ba_fact_seq_is_identity_and_not_the_primary_key():
    seq_col = BaFact.__table__.columns["seq"]
    assert seq_col.identity is not None
    assert seq_col.primary_key is False


def test_ba_fact_human_approval_defaults_false():
    col = BaFact.__table__.columns["human_approval"]
    assert col.nullable is False
