class FakeObjectStore:
    def __init__(self):
        self.writes = 0

    def put_if_absent(self, key, content, media_type):
        self.writes += 1
        return f"memory://{key}"


def test_ingest_builds_stable_spans_and_stores_content_once():
    from athena.ingestion import ingest_text_source

    store = FakeObjectStore()
    result = ingest_text_source(
        org_id="org-a",
        project_id="project-a",
        filename="meeting.txt",
        content=b"Manager: approve adjustments.\nInventory updates after approval.",
        media_type="text/plain",
        store=store,
    )

    assert result.storage_key == f"sha256/{result.content_hash}"
    assert [span.raw_text for span in result.spans] == [
        "Manager: approve adjustments.",
        "Inventory updates after approval.",
    ]
    assert store.writes == 1


def test_ingest_rejects_invalid_utf8_without_writing_content():
    from athena.ingestion import ingest_text_source

    store = FakeObjectStore()

    try:
        ingest_text_source(
            org_id="org-a",
            project_id="project-a",
            filename="meeting.txt",
            content=b"\xff",
            media_type="text/plain",
            store=store,
        )
    except ValueError as error:
        assert str(error) == "Source content must be valid UTF-8 text."
    else:
        raise AssertionError("invalid UTF-8 must be rejected")

    assert store.writes == 0
