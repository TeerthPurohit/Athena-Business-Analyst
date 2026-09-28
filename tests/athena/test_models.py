def test_standalone_models_expose_evidence_tables():
    from athena.models import Base

    assert {
        "athena_source",
        "athena_source_content",
        "athena_source_span",
    } <= set(Base.metadata.tables)
