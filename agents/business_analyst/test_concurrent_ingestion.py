"""No-DB test: document parts are analyzed concurrently and each part streams what it found."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agents.business_analyst.api import routes
from agents.business_analyst.extraction import RequirementAnalysis
from agents.business_analyst.ir import ProjectIR


def test_parts_of_all_files_run_concurrently_and_report_live() -> None:
    in_flight = 0
    peak = 0

    async def slow(result):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return result

    async def analyze_requirements(chunk, **_):
        return await slow(RequirementAnalysis("", [], []))

    async def analyze_facts(chunk):
        return await slow(ProjectIR(project_name="", decisions=[f"decided in {chunk[:5]}"]))

    @asynccontextmanager
    async def savepoint():
        yield

    async def execute(_statement):  # no parts finished before this run
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    db = SimpleNamespace(begin_nested=savepoint, execute=execute)
    ctx = SimpleNamespace(org_id="org", project_id="project")
    documents = [(SimpleNamespace(id=f"s{i}", ref=f"file{i}.md"), f"doc{i} text") for i in range(3)]
    events = []

    async def emit(event):
        events.append(event)

    with patch.object(routes, "analyze_requirements", analyze_requirements), \
         patch.object(routes, "analyze_facts", analyze_facts), \
         patch.object(routes, "persist_requirements", AsyncMock(return_value={"requirements_created": 0, "gaps_created": 0})), \
         patch.object(routes, "persist_facts", AsyncMock(return_value={"facts_created": 1})), \
         patch.object(routes, "assert_fact", AsyncMock()) as marker:
        results = asyncio.run(routes._analyze_source_texts(ctx, db, documents, emit))

    assert peak > 2  # parts of different files overlapped instead of running one after another
    assert all(result["facts_created"] == 1 and result["chunks_analyzed"] == 1 for result in results.values())
    found = [event["message"] for event in events if event["type"] == "tool_result"]
    assert sorted(message.split(":")[0] for message in found) == ["file0.md", "file1.md", "file2.md"]
    assert all("1 decision" in message for message in found)
    assert {call.kwargs["predicate"] for call in marker.await_args_list} == {"part_completed", "completed"}


def test_retry_skips_parts_that_already_finished() -> None:
    analyzed = []

    async def analyze_facts(chunk):
        analyzed.append(chunk)
        return ProjectIR(project_name="")

    @asynccontextmanager
    async def savepoint():
        yield

    source = SimpleNamespace(id="s1", ref="notes.md")
    text = "first part\n" * 10
    finished = routes._part_key("s1", 0, routes._source_chunks(text)[0])

    async def execute(_statement):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [finished]))

    db = SimpleNamespace(begin_nested=savepoint, execute=execute)
    with patch.object(routes, "analyze_requirements", AsyncMock()), \
         patch.object(routes, "analyze_facts", analyze_facts), \
         patch.object(routes, "assert_fact", AsyncMock()) as marker:
        results = asyncio.run(routes._analyze_source_texts(SimpleNamespace(org_id="o", project_id="p"), db, [(source, text)]))

    assert analyzed == []  # the only part was already analyzed
    assert results["s1"]["chunks_analyzed"] == results["s1"]["chunks_total"] == 1
    assert [call.kwargs["predicate"] for call in marker.await_args_list] == ["completed"]
