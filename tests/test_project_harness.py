import asyncio
from types import SimpleNamespace

from agents.business_analyst import project_harness


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self, records):
        self.records = records

    async def get(self, _model, record_id):
        return self.records.get(record_id)

    async def execute(self, _statement):
        return FakeResult(list(self.records.values()))


def test_source_read_stays_within_active_project(tmp_path, monkeypatch):
    monkeypatch.setattr(project_harness, "UPLOAD_DIR", tmp_path)
    ctx = SimpleNamespace(org_id="org-a", project_id="project-a")
    source = SimpleNamespace(id="source-b", org_id="org-b", project_id="project-b", kind="document", ref="secret.md", content_hash="hash")
    db = FakeSession({source.id: source})

    result = asyncio.run(project_harness.read_project_source(ctx, db, source.id))

    assert result == {"error": "Source not found in this project."}


def test_fact_trace_stays_within_active_project():
    ctx = SimpleNamespace(org_id="org-a", project_id="project-a")
    fact = SimpleNamespace(id="fact-b", org_id="org-b", project_id="project-b")

    result = asyncio.run(project_harness.trace_project_fact(ctx, FakeSession({fact.id: fact}), fact.id))

    assert result == {"error": "Fact not found in this project."}


def test_search_reads_project_markdown_with_line_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(project_harness, "UPLOAD_DIR", tmp_path)
    async def no_semantic(_ctx, _db, _query, _chunks):
        return None
    monkeypatch.setattr(project_harness, "_semantic_matches", no_semantic)
    async def no_facts(_ctx, _db):
        return []
    monkeypatch.setattr(project_harness, "get_facts", no_facts)
    ctx = SimpleNamespace(org_id="org-a", project_id="project-a")
    source = SimpleNamespace(id="source-a", org_id="org-a", project_id="project-a", kind="document", ref="requirements.md", content_hash="hash", captured_at=1)
    target = tmp_path / ctx.org_id / ctx.project_id
    target.mkdir(parents=True)
    (target / "hash_requirements.md").write_text("# Scope\nInventory alerts require approval.\n", encoding="utf-8")

    result = asyncio.run(project_harness.search_project_evidence(ctx, FakeSession({source.id: source}), "inventory approval"))

    assert result["sources"][0]["source_id"] == source.id
    assert "2: Inventory alerts require approval." in result["sources"][0]["excerpt"]


def _chunk(**delta):
    from openai.types.chat import ChatCompletionChunk
    return ChatCompletionChunk.model_validate({
        "id": "chunk", "object": "chat.completion.chunk", "created": 0, "model": "test-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
    })


def test_analysis_runs_tool_loop_and_streams_thinking_and_answer(monkeypatch):
    ctx = SimpleNamespace(org_id="org-a", project_id="project-a")
    calls = []

    async def execute(_ctx, _db, name, arguments):
        calls.append((name, arguments))
        return {"project": "A"}

    class FakeCompletions:
        def __init__(self):
            self.turn = 0

        async def create(self, **_kwargs):
            self.turn += 1
            turn = self.turn

            async def stream():
                if turn == 1:
                    yield _chunk(reasoning="Need the overview.")
                    yield _chunk(tool_calls=[{"index": 0, "id": "call-1", "type": "function", "function": {"name": "get_project_overview", "arguments": "{}"}}])
                else:
                    yield _chunk(content="The project has ")
                    yield _chunk(content="an evidence gap.")
            return stream()

    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    async def no_route(_question):
        return None

    async def prompt(*_args):
        return "system"

    monkeypatch.setattr(project_harness, "llm_client_args", lambda *_args, **_kwargs: [{"model": "test-model"}])
    monkeypatch.setattr(project_harness, "chat_client", lambda _args: (client, {"model": "test-model"}))
    monkeypatch.setattr(project_harness, "choose_project_inspection_tool", no_route)
    monkeypatch.setattr(project_harness, "fetch_prompt", prompt)
    monkeypatch.setattr(project_harness, "execute_project_tool", execute)
    events = []

    async def emit(event):
        events.append(event)

    result = asyncio.run(project_harness.run_project_analysis(ctx, object(), "Which gaps remain?", emit))

    assert result["answer"] == "The project has an evidence gap."
    assert [step["tool"] for step in result["tools_used"]] == ["search_project_evidence", "get_project_overview"]
    assert calls == [("search_project_evidence", {"query": "Which gaps remain?"}), ("get_project_overview", {})]
    assert [event["text"] for event in events if event["type"] == "thinking_delta"] == ["Need the overview."]
    assert "".join(event["text"] for event in events if event["type"] == "answer_delta") == result["answer"]
    assert events[-1] == {"type": "answer", "text": result["answer"]}


def test_semantic_matches_rank_and_reuse_project_embeddings(monkeypatch):
    ctx = SimpleNamespace(org_id="org-a", project_id="project-a")
    chunks = [
        {"entity_type": "ProjectSourceChunk", "entity_id": "source-a:0", "text": "Stock approval rule", "result": {"source_id": "source-a", "line_start": 1}},
        {"entity_type": "ProjectSourceChunk", "entity_id": "source-a:24", "text": "Holiday calendar", "result": {"source_id": "source-a", "line_start": 25}},
    ]

    class FakeEmbeddingClient:
        model = "qwen/qwen3-embedding-8b"

        def __init__(self):
            self.document_calls = 0

        async def aembed_query(self, _query):
            return [1.0, 0.0]

        async def aembed_documents(self, texts):
            self.document_calls += 1
            return [[1.0, 0.0] if "Stock" in text else [0.0, 1.0] for text in texts]

    class EmbeddingSession:
        def __init__(self):
            self.records = []
            self.commits = 0

        async def execute(self, _statement):
            return FakeResult(self.records)

        def add(self, record):
            self.records.append(record)

        async def commit(self):
            self.commits += 1

    client = FakeEmbeddingClient()
    db = EmbeddingSession()
    monkeypatch.setattr(project_harness, "_embedding_client", lambda: client)

    first = asyncio.run(project_harness._semantic_matches(ctx, db, "approval", chunks))
    second = asyncio.run(project_harness._semantic_matches(ctx, db, "approval", chunks))

    assert first["sources"][0]["line_start"] == 1
    assert second["sources"][0]["line_start"] == 1
    assert client.document_calls == 1
    assert db.commits == 1
    assert {(record.org_id, record.project_id) for record in db.records} == {("org-a", "project-a")}

def test_search_falls_back_when_embeddings_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(project_harness, "UPLOAD_DIR", tmp_path)

    async def no_facts(_ctx, _db):
        return []

    async def broken_embeddings(_ctx, _db, _query, _chunks):
        raise RuntimeError("provider unavailable")

    class SearchSession(FakeSession):
        def __init__(self, records):
            super().__init__(records)
            self.rolled_back = False

        async def rollback(self):
            self.rolled_back = True

    monkeypatch.setattr(project_harness, "get_facts", no_facts)
    monkeypatch.setattr(project_harness, "_semantic_matches", broken_embeddings)
    ctx = SimpleNamespace(org_id="org-a", project_id="project-a")
    source = SimpleNamespace(id="source-a", org_id="org-a", project_id="project-a", kind="document", ref="requirements.md", content_hash="hash", captured_at=1)
    target = tmp_path / ctx.org_id / ctx.project_id
    target.mkdir(parents=True)
    (target / "hash_requirements.md").write_text("Approval is required.\n", encoding="utf-8")
    db = SearchSession({source.id: source})

    result = asyncio.run(project_harness.search_project_evidence(ctx, db, "approval"))

    assert result["retrieval_mode"] == "lexical"
    assert result["sources"][0]["source_id"] == source.id
    assert db.rolled_back