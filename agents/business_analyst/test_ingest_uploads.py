"""No-DB regression tests for _ingest_uploads.

1. commit() expires loaded rows; reading one before refresh() lazy-loads synchronously and raises
   MissingGreenlet in production (a 9-file upload failed this way). The fake session reproduces
   that: any attribute read on an expired source raises.
2. A file saved by an earlier upload whose analysis never completed is analyzed on re-upload
   instead of being skipped as a duplicate.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agents.business_analyst.api import routes
from agents.business_analyst.extraction import RequirementAnalysis


class ExpiringSource:
    def __init__(self, source_id: str, ref: str):
        object.__setattr__(self, "_data", {
            "id": source_id, "ref": ref, "kind": "document", "tier": "tier1",
            "content_hash": "hash", "captured_at": datetime.now(timezone.utc),
        })
        object.__setattr__(self, "expired", False)

    def __getattr__(self, name):
        if object.__getattribute__(self, "expired"):
            raise RuntimeError("MissingGreenlet: expired attribute read outside the async session")
        return object.__getattribute__(self, "_data")[name]


class FakeSession:
    def __init__(self, existing_source=None, analyzed=False):
        self.sources = [existing_source] if existing_source else []
        self.existing_source = existing_source
        self.analyzed = analyzed

    async def execute(self, statement):
        table = str(statement).lower()
        if "from ba_fact" in table:
            value = "marker" if self.analyzed else None
        else:
            value = self.existing_source
        return SimpleNamespace(scalar_one_or_none=lambda: value, scalars=lambda: SimpleNamespace(all=lambda: []))

    async def commit(self):
        for source in self.sources:
            object.__setattr__(source, "expired", True)

    async def refresh(self, source, *_args, **_kwargs):
        object.__setattr__(source, "expired", False)

    @asynccontextmanager
    async def begin_nested(self):
        yield


def run_ingest(db, tmp_path, files):
    analyzed_texts = []

    async def register_source(ctx, session, **kwargs):
        source = ExpiringSource(f"new-{len(session.sources)}", kwargs["ref"])
        session.sources.append(source)
        return source

    async def analyze_facts(text):
        analyzed_texts.append(text)
        return routes.ProjectIR(project_name="")

    async def analyze_requirements(text, **_):
        return RequirementAnalysis("", [], [])

    ctx = SimpleNamespace(org_id="org", project_id="project")
    with patch.object(routes, "UPLOAD_DIR", tmp_path), \
         patch.object(routes, "register_source", register_source), \
         patch.object(routes, "analyze_facts", analyze_facts), \
         patch.object(routes, "analyze_requirements", analyze_requirements), \
         patch.object(routes, "persist_facts", AsyncMock(return_value={"facts_created": 1})), \
         patch.object(routes, "persist_requirements", AsyncMock(return_value={})), \
         patch.object(routes, "assert_fact", AsyncMock()), \
         patch.object(routes, "_get_or_create_chat_source", AsyncMock(return_value=SimpleNamespace(id="chat"))), \
         patch.object(routes, "_refresh_summary_if_changed", AsyncMock(return_value=({}, None))):
        result = asyncio.run(routes._ingest_uploads(ctx, db, files))
    return result, analyzed_texts


def test_sources_are_readable_after_the_pre_analysis_commit(tmp_path) -> None:
    db = FakeSession()
    result, analyzed = run_ingest(db, tmp_path, [("a.md", b"# Alpha\nThe app must assign drivers."), ("b.md", b"# Beta\nBudget is 15 lakh.")])

    assert [entry.get("error") for entry in result["sources"]] == [None, None]
    assert sorted(analyzed) == ["# Alpha\nThe app must assign drivers.", "# Beta\nBudget is 15 lakh."]
    assert all(entry["extraction"]["chunks_analyzed"] == 1 for entry in result["sources"])


def test_saved_but_unanalyzed_file_is_analyzed_on_reupload(tmp_path) -> None:
    earlier = ExpiringSource("earlier", "a.md")
    result, analyzed = run_ingest(FakeSession(existing_source=earlier, analyzed=False), tmp_path, [("a.md", b"# Alpha")])
    assert analyzed == ["# Alpha"] and result["sources"][0]["duplicate"] is False

    result, analyzed = run_ingest(FakeSession(existing_source=ExpiringSource("done", "a.md"), analyzed=True), tmp_path, [("a.md", b"# Alpha")])
    assert analyzed == [] and result["sources"][0]["duplicate"] is True
