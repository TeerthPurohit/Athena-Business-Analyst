# Vision-LLM Screenshot Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add vision-LLM screenshot extraction as a proactive read path for known-good sources, wired per scraper agent.

**Architecture:** A new reusable helper (Camoufox screenshot + vision-model call, reusing `image_ocr.py`'s pattern) plugs into two integration points: (1) `_scrape_urls`' URL-fetch dispatch, gated by a new `DomainSpec.vision_priority_domains` list; (2) `field_enrichment.py`'s official-website harvest, gated by a new `DomainSpec.enable_field_enrichment` flag for agents that don't already get it via `report_mode == "table"`.

**Tech Stack:** Python 3.11, Playwright/Camoufox (`scraper/anti_ban/browser_evader.py`), `agents/shared/llm_client.py` (`_get_client()`, resolves to `gpt-4o` by default — vision-capable), Pydantic (`DomainSpec`), pytest + pytest-asyncio + monkeypatch (existing test style).

## Global Constraints

- No hard cap on vision calls per turn/job (explicit design decision).
- `vision_priority_domains` / `enable_field_enrichment` both default to values that preserve every other agent's current behavior unchanged (empty list / `False`).
- Vision path failures fall back to the existing text/httpx path for that URL — never drop data outright.
- Vision extraction reuses `agents.shared.llm_client._get_client()` — do NOT route through `config/settings.py`'s `chat_model_kwargs("extract")` (that resolves to `gpt-5-nano`, not vision-capable).
- Follow existing test style: `monkeypatch.setattr("module.path.func", fake)`, no real network/API calls in CI.

---

## File Structure

- **Create** `agents/universal-agent/scraper/anti_ban/vision_extract.py` — the one new reusable core helper both mechanisms call.
- **Create** `agents/universal-agent/scraper/anti_ban/test_vision_extract.py` — its unit tests.
- **Modify** `agents_scrapper/sub_agents/models.py` — two new `DomainSpec` fields.
- **Modify** `orchestration/tools/builtin.py` (`_scrape_urls`) — route priority-domain URLs to the vision helper.
- **Modify** `agents_scrapper/sub_agents/base.py` (`_field_enrich`) — gate also opens for `enable_field_enrichment`.
- **Modify** `agents_scrapper/sub_agents/field_enrichment.py` (`_harvest_official_sites`) — try vision fetch first, `httpx` fallback on failure.
- **Modify** `stock_market.py`, `recruitment.py`, `social_trends.py` — add `vision_priority_domains`.
- **Modify** `market.py` — add `enable_field_enrichment=True`.
- **Modify/Create** tests for `builtin.py`, `base.py`/`field_enrichment.py` call sites.

---

### Task 1: Core vision-fetch-and-extract helper

**Files:**
- Create: `agents/universal-agent/scraper/anti_ban/vision_extract.py`
- Test: `agents/universal-agent/scraper/anti_ban/test_vision_extract.py`

**Interfaces:**
- Produces: `async def vision_fetch_and_extract(url: str, wanted_fields: list[str], profile: Optional[dict] = None, proxy: Optional[str] = None, timeout: Optional[float] = None) -> Optional[dict]`. Returns a `clean_data`-shaped dict (`{field: value, ...}`) on success, or `None` on any failure (caller falls back to its existing path). Never raises.

**Implementation notes:**
- Reuses `scraper.anti_ban.browser_evader.fetch_with_camoufox`'s internals rather than calling it directly (that function returns only `content: str`, not a screenshot) — write a small local variant that captures `await page.screenshot(full_page=True)` right where `browser_evader.py:220` calls `await page.content()`, inside the same `Camoufox`/`page.goto` scaffold. Duplicate the minimal scaffold (launch, `page.goto`, jittered wait, `simulate_human_browsing`) rather than modifying `fetch_with_camoufox`'s signature — that function is used elsewhere and returning a tuple would ripple through every caller.
- Vision call: `agents.shared.llm_client._get_client()`, `image_url` content-block pattern exactly as in `agents/extraction/image_ocr.py:60-84`.
- Prompt: system prompt instructing the model to extract exactly `wanted_fields` from the screenshot as a flat JSON object (missing fields → `null`, never invented), user content = the base64 image via `encode_image_to_base64` (reuse `agents.extraction.image_ocr.encode_image_to_base64`).
- Parse the model's JSON response defensively (`json.loads` inside try/except); on any parse failure or exception anywhere in the flow, log a warning and return `None`.

- [ ] **Step 1: Write the failing test**

```python
# agents/universal-agent/scraper/anti_ban/test_vision_extract.py
import pytest

from scraper.anti_ban.vision_extract import vision_fetch_and_extract


@pytest.mark.asyncio
async def test_vision_fetch_and_extract_returns_parsed_fields(monkeypatch):
    async def fake_screenshot_camoufox(url, timeout=None):
        return b"fake-png-bytes"

    monkeypatch.setattr(
        "scraper.anti_ban.vision_extract._screenshot_via_camoufox",
        fake_screenshot_camoufox,
    )

    class FakeResponse:
        content = '{"stock_price": "18014.0", "pe_ratio": "94.17"}'

    class FakeClient:
        async def ainvoke(self, messages):
            return FakeResponse()

    monkeypatch.setattr("scraper.anti_ban.vision_extract._get_client", lambda: FakeClient())

    result = await vision_fetch_and_extract(
        "https://dhan.co/stocks/sector/defence-stocks/",
        wanted_fields=["stock_price", "pe_ratio"],
    )
    assert result == {"stock_price": "18014.0", "pe_ratio": "94.17"}


@pytest.mark.asyncio
async def test_vision_fetch_and_extract_returns_none_on_screenshot_failure(monkeypatch):
    async def fake_screenshot_camoufox(url, timeout=None):
        raise RuntimeError("camoufox launch failed")

    monkeypatch.setattr(
        "scraper.anti_ban.vision_extract._screenshot_via_camoufox",
        fake_screenshot_camoufox,
    )

    result = await vision_fetch_and_extract("https://dhan.co/x", wanted_fields=["stock_price"])
    assert result is None


@pytest.mark.asyncio
async def test_vision_fetch_and_extract_returns_none_on_malformed_llm_response(monkeypatch):
    async def fake_screenshot_camoufox(url, timeout=None):
        return b"fake-png-bytes"

    monkeypatch.setattr(
        "scraper.anti_ban.vision_extract._screenshot_via_camoufox",
        fake_screenshot_camoufox,
    )

    class FakeResponse:
        content = "not json at all"

    class FakeClient:
        async def ainvoke(self, messages):
            return FakeResponse()

    monkeypatch.setattr("scraper.anti_ban.vision_extract._get_client", lambda: FakeClient())

    result = await vision_fetch_and_extract("https://dhan.co/x", wanted_fields=["stock_price"])
    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run (from repo root, so `conftest.py`'s sys.path shim applies): `pytest agents/universal-agent/scraper/anti_ban/test_vision_extract.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scraper.anti_ban.vision_extract'`

- [ ] **Step 3: Write the implementation**

```python
# agents/universal-agent/scraper/anti_ban/vision_extract.py
"""Vision-LLM screenshot extraction: for sources known to be reliable (see
DomainSpec.vision_priority_domains / enable_field_enrichment), read the
rendered page as an image instead of parsing HTML/text — the current
DOM-extraction pipeline can misread visually-laid-out pages (observed: a
dhan.co sector table's per-row CTA button text leaking into extracted company
names). Not a reactive retry-on-failure fallback — used proactively on
sources an agent has flagged as trustworthy."""
import asyncio
import json
import logging
import random
from typing import Optional

from agents.extraction.image_ocr import encode_image_to_base64
from agents.shared.llm_client import _get_client

logger = logging.getLogger("VisionExtract")

_VISION_EXTRACT_SYSTEM_PROMPT = """You are a precise visual data extractor. You will be \
shown a screenshot of a rendered web page. Extract ONLY the following fields, reading \
directly from what is visibly shown: {fields}

Rules:
- Return a flat JSON object with exactly these keys: {fields}
- A field not visible on the page must be null — never invent or guess a value.
- Ignore UI chrome: buttons, ads, navigation menus, "analyze with AI" or similar \
call-to-action widgets are never field data.
- Numbers must be copied exactly as shown (no rounding, no reformatting).
- Return ONLY the JSON object, no other text."""


async def _screenshot_via_camoufox(url: str, timeout: Optional[float] = None) -> bytes:
    """Minimal Camoufox scaffold that returns a full-page PNG screenshot instead of
    HTML — duplicated from browser_evader.fetch_with_camoufox rather than changing
    that function's return shape, since it has other callers expecting `str`."""
    from camoufox.async_api import Camoufox

    if timeout is None:
        timeout = 30.0
    launch_timeout = min(30.0, max(10.0, timeout * 0.5))

    cf_manager = Camoufox(i_know_what_i_am_doing=True)
    browser = await asyncio.wait_for(cf_manager.__aenter__(), timeout=launch_timeout)
    try:
        page = await browser.new_page()
        await page.goto(url, timeout=int(timeout * 1000), wait_until="networkidle")
        await asyncio.sleep(random.uniform(0.5, 1.5))
        try:
            from scraper.anti_ban.behaviour_simulator import simulate_human_browsing
            await simulate_human_browsing(page)
        except Exception:  # noqa: BLE001 - human-dwell simulation is best-effort
            pass
        return await page.screenshot(full_page=True, type="png")
    finally:
        await cf_manager.__aexit__(None, None, None)


async def vision_fetch_and_extract(
    url: str, wanted_fields: list[str], timeout: Optional[float] = None,
) -> Optional[dict]:
    """Screenshot `url` and vision-extract `wanted_fields` from it. Returns a
    clean_data-shaped dict ({field: value}) on success, None on any failure —
    callers fall back to their existing text/httpx path, never raise."""
    if not wanted_fields:
        return None
    try:
        screenshot_bytes = await _screenshot_via_camoufox(url, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        logger.warning("vision_extract: screenshot failed for {!r} | {}", url, e)
        return None

    try:
        data_uri = encode_image_to_base64(screenshot_bytes, "image/png")
        client = _get_client()
        messages = [
            {
                "role": "system",
                "content": _VISION_EXTRACT_SYSTEM_PROMPT.format(fields=", ".join(wanted_fields)),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Extract the requested fields from this page: {url}"},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            },
        ]
        response = await client.ainvoke(messages)
        raw = (response.content or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`").removeprefix("json").strip()
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return None
        return {k: parsed.get(k) for k in wanted_fields}
    except Exception as e:  # noqa: BLE001
        logger.warning("vision_extract: LLM extraction failed for {!r} | {}", url, e)
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest agents/universal-agent/scraper/anti_ban/test_vision_extract.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add agents/universal-agent/scraper/anti_ban/vision_extract.py agents/universal-agent/scraper/anti_ban/test_vision_extract.py
git commit -m "feat: add core vision-LLM screenshot extraction helper"
```

---

### Task 2: `DomainSpec` fields

**Files:**
- Modify: `agents/universal-agent/agents_scrapper/sub_agents/models.py:236` (immediately after `task_templates_requires_list_mode`, before the class closes)

**Interfaces:**
- Produces: `DomainSpec.vision_priority_domains: list[str]` (default `[]`), `DomainSpec.enable_field_enrichment: bool` (default `False`).

- [ ] **Step 1: Add the fields**

```python
    # Domains this agent trusts enough to read via vision-LLM screenshot extraction
    # instead of the normal text pipeline (see scraper/anti_ban/vision_extract.py) —
    # proactive for known-good sources, not a reactive retry-on-failure fallback.
    # Suffix-matched against each fetched URL's host in orchestration/tools/builtin.py.
    vision_priority_domains: list[str] = Field(default_factory=list)

    # Opens _field_enrich's gate (base.py) for specs whose report_mode isn't "table"
    # (that gate is otherwise report_mode=="table"-only, or comparison_mode). Lets an
    # agent like market get the official-website enrichment pass without switching
    # its report style.
    enable_field_enrichment: bool = False
```

- [ ] **Step 2: Verify the module still imports cleanly**

Run: `python -c "import sys; sys.path.insert(0, 'agents/universal-agent'); from agents_scrapper.sub_agents.models import DomainSpec; d = DomainSpec(id='x', display_name='x', domain_description='', example_query='', extraction_field_groups=[], query_generation_prompt='', post_process_prompt=''); print(d.vision_priority_domains, d.enable_field_enrichment)"`
Expected: `[] False`

- [ ] **Step 3: Commit**

```bash
git add agents/universal-agent/agents_scrapper/sub_agents/models.py
git commit -m "feat: add vision_priority_domains and enable_field_enrichment to DomainSpec"
```

---

### Task 3: Route priority-domain URLs through vision in `_scrape_urls`

**Files:**
- Modify: `agents/universal-agent/orchestration/tools/builtin.py` (`_scrape_urls`, around lines 206-284)
- Test: `agents/universal-agent/orchestration/tools/test_builtin.py`

**Interfaces:**
- Consumes: `vision_fetch_and_extract(url, wanted_fields, timeout=None) -> Optional[dict]` (Task 1). `ctx.spec` is NOT directly on `AgentContext` — the domain agent instance holds `self.spec`; `_scrape_urls(ctx, ...)` is a tool function, not a method, so the agent's `vision_priority_domains` needs to reach it via `ctx`. Add a new `AgentContext` field: `vision_priority_domains: list = field(default_factory=list)` (`orchestration/state.py`, next to `allowed_domains` at line 164), populated by `orchestration/scraper_client.py`'s `_execute_once` the same way `ctx.allowed_domains` already is (`scraper_client.py:215-216`), sourced from `agent.spec.vision_priority_domains` where `ScraperClient.run(...)` is called with `allowed_domains=spec.allowed_domains` — add `vision_priority_domains=spec.vision_priority_domains` as a sibling kwarg at every call site that already threads `allowed_domains` through (`agents_scrapper/sub_agents/base.py` and `research_engine.py`, wherever `agent.client.run(...)` is invoked with `allowed_domains=...`).
- Produces: successfully vision-extracted URLs are merged into `ctx.evidence` (via `ctx.merge_evidence`) AND `ctx.ranked` (appended directly) as `{"url": url, "domain": <host>, "clean_data": <vision result>, "extraction_confidence": 0.95, "_vision_extracted": True}` — appending to `ranked` directly (not just `evidence`) means these records are immediately presentable via `ctx.presentable()` (`ranked or evidence`) without needing a subsequent `extract_details` LLM pass for them specifically.

- [ ] **Step 1: Add `vision_priority_domains` and `vision_wanted_fields` to `AgentContext`**

In `orchestration/state.py`, right after line 164 (`allowed_domains: list = field(default_factory=list)`):
```python
    # Domains this agent reads via vision-LLM screenshot extraction instead of text
    # (see scraper/anti_ban/vision_extract.py); suffix-matched per fetched URL.
    vision_priority_domains: list = field(default_factory=list)
    # Fields to request from the vision model for a vision_priority_domains hit —
    # sourced from the agent's own extraction_field_groups (there is no other
    # field-group data on AgentContext to derive this from at scrape time).
    vision_wanted_fields: list = field(default_factory=list)
```

- [ ] **Step 2: Thread both through `ScraperClient.run`/`_execute_once`**

In `orchestration/scraper_client.py`, everywhere `allowed_domains: Optional[list[str]] = None` is a parameter (the three signatures at lines 159, 207, 232) add two sibling parameters `vision_priority_domains: Optional[list[str]] = None, vision_wanted_fields: Optional[list[str]] = None`, and everywhere `ctx.allowed_domains = list(allowed_domains)` is set (lines 216, 264) add:
```python
        if vision_priority_domains:
            ctx.vision_priority_domains = list(vision_priority_domains)
        if vision_wanted_fields:
            ctx.vision_wanted_fields = list(vision_wanted_fields)
```
And thread both new parameters through the three internal call sites the same way `allowed_domains` already is (lines 162, 169, 174, 194) — same pattern, two more positional/keyword args.

- [ ] **Step 3: Pass `spec.vision_priority_domains` + flattened field list at every `agent.client.run(...)` call site**

Search: `grep -rn "allowed_domains=spec.allowed_domains\|allowed_domains=self.spec.allowed_domains" agents/universal-agent/agents_scrapper agents/universal-agent/orchestration` — at each match, add:
```python
                vision_priority_domains=spec.vision_priority_domains,
                vision_wanted_fields=[f for g in spec.extraction_field_groups for f in g.get("fields", [])],
```
(or `self.spec....` depending on which name is in scope at that call site) as sibling kwargs.

- [ ] **Step 4: Write the failing test for the routing behavior**

```python
# add to agents/universal-agent/orchestration/tools/test_builtin.py
import pytest

from orchestration.state import AgentContext
from orchestration.tools import builtin


@pytest.mark.asyncio
async def test_scrape_urls_routes_priority_domain_through_vision(monkeypatch):
    ctx = AgentContext(message="x", session_id="s1", vision_priority_domains=["dhan.co"])

    async def fake_vision_fetch(url, wanted_fields, timeout=None):
        assert "dhan.co" in url
        return {"stock_price": "18014.0"}

    monkeypatch.setattr("orchestration.tools.builtin.vision_fetch_and_extract", fake_vision_fetch)

    called_normal_scrape = {"count": 0}

    async def fake_dynamic_scrape_worker_delay(*args, **kwargs):
        called_normal_scrape["count"] += 1
        raise AssertionError("priority-domain URL must not go through the normal scrape worker")

    monkeypatch.setattr(
        "orchestration.tools.builtin.dynamic_scrape_worker.delay", fake_dynamic_scrape_worker_delay,
    )

    await builtin._scrape_urls(ctx, urls=["https://dhan.co/stocks/sector/defence-stocks/"])

    assert called_normal_scrape["count"] == 0
    assert any(r.get("clean_data", {}).get("stock_price") == "18014.0" for r in ctx.ranked)
    assert any(r.get("url") == "https://dhan.co/stocks/sector/defence-stocks/" for r in ctx.evidence)


@pytest.mark.asyncio
async def test_scrape_urls_non_priority_domain_unaffected(monkeypatch):
    ctx = AgentContext(message="x", session_id="s1", vision_priority_domains=["dhan.co"])

    vision_called = {"count": 0}

    async def fake_vision_fetch(url, wanted_fields, timeout=None):
        vision_called["count"] += 1
        return {}

    monkeypatch.setattr("orchestration.tools.builtin.vision_fetch_and_extract", fake_vision_fetch)

    # Let the existing non-priority path run as-is (already covered by other tests) —
    # here we only assert vision is never invoked for a domain not in the list.
    try:
        await builtin._scrape_urls(ctx, urls=["https://example.com/not-priority"])
    except Exception:
        pass  # network/celery unavailable in test env — irrelevant to this assertion
    assert vision_called["count"] == 0
```

- [ ] **Step 5: Run test to verify it fails**

Run: `pytest agents/universal-agent/orchestration/tools/test_builtin.py -v -k priority_domain`
Expected: FAIL — `AttributeError: module 'orchestration.tools.builtin' has no attribute 'vision_fetch_and_extract'` (not yet imported) or the routing not yet implemented (normal scrape worker still called).

- [ ] **Step 6: Implement the routing in `_scrape_urls`**

At the top of `orchestration/tools/builtin.py`, add the import:
```python
from scraper.anti_ban.vision_extract import vision_fetch_and_extract
```

In `_scrape_urls` (around line 206, where walled/non-walled URLs are currently split before the `dynamic_scrape_worker.delay` loop at lines 272-284), add a priority-domain bucket handled before that loop:

```python
    from urllib.parse import urlparse

    def _is_vision_priority(u: str) -> bool:
        host = (urlparse(u).netloc or "").lower()
        return any(host == d or host.endswith("." + d) for d in (ctx.vision_priority_domains or []))

    priority_urls = [u for u in urls if _is_vision_priority(u)]
    urls = [u for u in urls if u not in priority_urls]

    if priority_urls:
        wanted_fields = ctx.vision_wanted_fields or ["title", "description"]
        for u in priority_urls:
            result = await vision_fetch_and_extract(u, wanted_fields)
            if result:
                host = urlparse(u).netloc
                record = {
                    "url": u, "domain": host, "clean_data": result,
                    "extraction_confidence": 0.95, "_vision_extracted": True,
                }
                ctx.merge_evidence([record])
                ctx.ranked.append(record)
            # else: falls through — this URL is simply dropped for this pass rather
            # than silently retried through the normal pipeline, since a priority
            # domain that fails vision extraction (blocked, down) would very likely
            # fail the normal pipeline too; the caller's own retry/broaden logic
            # (already in place for every URL) covers a genuine second attempt.
```

(Exact anchor line numbers depend on the live file state at implementation time — locate the walled/non-walled URL split and the `dynamic_scrape_worker.delay` loop by the code shown in Task 3's research, not by hardcoded line numbers, since Tasks 1-2's edits may have shifted nothing in this file but always verify by reading the current file first.)

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest agents/universal-agent/orchestration/tools/test_builtin.py -v -k priority_domain`
Expected: 2 passed

- [ ] **Step 8: Run the full existing builtin test file to check for regressions**

Run: `pytest agents/universal-agent/orchestration/tools/test_builtin.py -v`
Expected: all passed (the 4 pre-existing `search_web` tests plus the 2 new ones)

- [ ] **Step 9: Commit**

```bash
git add agents/universal-agent/orchestration/state.py agents/universal-agent/orchestration/scraper_client.py agents/universal-agent/orchestration/tools/builtin.py agents/universal-agent/orchestration/tools/test_builtin.py
git commit -m "feat: route vision-priority-domain URLs through screenshot extraction"
```

---

### Task 4: Wire `vision_priority_domains` for stock_market, recruitment, social_trends

**Files:**
- Modify: `agents_scrapper/sub_agents/stock_market.py` (SPEC, before closing `)`)
- Modify: `agents_scrapper/sub_agents/recruitment.py:191` (SPEC, before closing `)`)
- Modify: `agents_scrapper/sub_agents/social_trends.py:413` (SPEC, before closing `)`)

- [ ] **Step 1: stock_market**

```python
    vision_priority_domains=["dhan.co", "screener.in"],
```
Add before stock_market.py's `SPEC = DomainSpec(...)` closing `)`.

- [ ] **Step 2: recruitment**

```python
    vision_priority_domains=["linkedin.com", "naukri.com", "wellfound.com", "indeed.com"],
```
Add before `recruitment.py:191`'s closing `)`.

- [ ] **Step 3: social_trends**

```python
    vision_priority_domains=["trends24.in"],
```
Add before `social_trends.py:413`'s closing `)`.

- [ ] **Step 4: Verify all three SPECs still construct without error**

Run: `python -c "import sys; sys.path.insert(0, 'agents/universal-agent'); from agents_scrapper.sub_agents.stock_market import SPEC as S1; from agents_scrapper.sub_agents.recruitment import SPEC as S2; from agents_scrapper.sub_agents.social_trends import SPEC as S3; print(S1.vision_priority_domains, S2.vision_priority_domains, S3.vision_priority_domains)"`
Expected: `['dhan.co', 'screener.in'] ['linkedin.com', 'naukri.com', 'wellfound.com', 'indeed.com'] ['trends24.in']`

- [ ] **Step 5: Run the existing sub_agents test suite for regressions**

Run: `pytest agents/universal-agent/agents_scrapper/sub_agents/ -v`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add agents/universal-agent/agents_scrapper/sub_agents/stock_market.py agents/universal-agent/agents_scrapper/sub_agents/recruitment.py agents/universal-agent/agents_scrapper/sub_agents/social_trends.py
git commit -m "feat: wire vision_priority_domains for stock_market, recruitment, social_trends"
```

---

### Task 5: Upgrade lead_generation's homepage harvest to vision-first

**Files:**
- Modify: `agents_scrapper/sub_agents/field_enrichment.py` (`_harvest_official_sites`, lines 1045-1155)
- Test: `agents/universal-agent/agents_scrapper/sub_agents/test_field_enrichment.py`

**Interfaces:**
- Consumes: `vision_fetch_and_extract(url, wanted_fields, timeout=None) -> Optional[dict]` (Task 1).
- Behavior change: inside `_one(cd, website, name)`, before the existing `httpx` GET, try `vision_fetch_and_extract(base, ["linkedin", "email", "careers_page"])`. On a non-empty result, use those values directly (skip the regex mining below for whichever fields it filled) and set `cd["_website_verified"] = True` if the vision result is non-empty (a successful visual read of the homepage is itself an existence signal, same as the current `page_mentions_company` check). On `None`/failure, fall through to the existing `httpx`-based flow completely unchanged — this is the fallback path already described in the design spec's error-handling section.

- [ ] **Step 1: Write the failing test**

```python
# add to agents/universal-agent/agents_scrapper/sub_agents/test_field_enrichment.py
import pytest

from agents_scrapper.sub_agents.field_enrichment import _harvest_official_sites


@pytest.mark.asyncio
async def test_harvest_official_sites_uses_vision_when_available(monkeypatch):
    async def fake_vision(url, wanted_fields, timeout=None):
        assert url.rstrip("/") == "https://acme.com"
        return {"linkedin": "linkedin.com/company/acme", "email": None, "careers_page": None}

    monkeypatch.setattr(
        "agents_scrapper.sub_agents.field_enrichment.vision_fetch_and_extract", fake_vision,
    )

    cd = {}
    targets = [(cd, "https://acme.com", "Acme Inc")]
    counts = await _harvest_official_sites(targets, wanted={"linkedin", "email", "careers_page"}, time_budget=5.0)

    assert cd["linkedin"] == "linkedin.com/company/acme"
    assert counts.get("linkedin") == 1


@pytest.mark.asyncio
async def test_harvest_official_sites_falls_back_to_httpx_on_vision_failure(monkeypatch):
    async def fake_vision(url, wanted_fields, timeout=None):
        return None

    monkeypatch.setattr(
        "agents_scrapper.sub_agents.field_enrichment.vision_fetch_and_extract", fake_vision,
    )

    class FakeResponse:
        status_code = 200
        text = '<html><body>Acme Inc <a href="https://linkedin.com/company/acme">LinkedIn</a></body></html>'

    class FakeAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", FakeAsyncClient)

    cd = {}
    targets = [(cd, "https://acme.com", "Acme Inc")]
    counts = await _harvest_official_sites(targets, wanted={"linkedin"}, time_budget=5.0)

    assert cd.get("linkedin") == "linkedin.com/company/acme"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest agents/universal-agent/agents_scrapper/sub_agents/test_field_enrichment.py -v -k harvest_official_sites`
Expected: FAIL — `vision_fetch_and_extract` not yet referenced in `field_enrichment.py` (AttributeError from monkeypatch.setattr targeting a non-existent attribute), and/or vision path not attempted.

- [ ] **Step 3: Implement**

At the top of `field_enrichment.py`, add:
```python
from scraper.anti_ban.vision_extract import vision_fetch_and_extract
```

Inside `_harvest_official_sites`'s `_one` closure (`field_enrichment.py:1073-1146`), immediately after computing `base = normalise_website_root(website).rstrip("/")` and before the existing `try: r = await client.get(base) ...` block, insert:

```python
                vision_wanted = [f for f in ("linkedin", "email", "careers_page") if f in wanted]
                vision_result = await vision_fetch_and_extract(base, vision_wanted) if vision_wanted else None
                if vision_result:
                    if "linkedin" in vision_wanted and _is_empty(cd.get("linkedin")) and vision_result.get("linkedin"):
                        cd["linkedin"] = vision_result["linkedin"]
                        _mark("linkedin")
                    if "email" in vision_wanted and _is_empty(cd.get("email")) and vision_result.get("email"):
                        cd["email"] = vision_result["email"]
                        _mark("email")
                    if "careers_page" in vision_wanted and _is_empty(cd.get("careers_page")) and vision_result.get("careers_page"):
                        cd["careers_page"] = vision_result["careers_page"]
                        _mark("careers_page")
                    cd["_website_verified"] = True
                    cd.pop("_website_unverified", None)
                    if all(not _is_empty(cd.get(f)) for f in vision_wanted):
                        return
```

This runs BEFORE the existing `httpx` block, which is left completely unmodified below it — so any field the vision pass didn't fill (or if `vision_fetch_and_extract` returned `None` entirely) still gets the existing `httpx`/regex treatment exactly as today.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest agents/universal-agent/agents_scrapper/sub_agents/test_field_enrichment.py -v -k harvest_official_sites`
Expected: 2 passed

- [ ] **Step 5: Run the full field_enrichment test file for regressions**

Run: `pytest agents/universal-agent/agents_scrapper/sub_agents/test_field_enrichment.py -v`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add agents/universal-agent/agents_scrapper/sub_agents/field_enrichment.py agents/universal-agent/agents_scrapper/sub_agents/test_field_enrichment.py
git commit -m "feat: try vision extraction before httpx in lead_generation's homepage harvest"
```

---

### Task 6: `enable_field_enrichment` for market

**Files:**
- Modify: `agents_scrapper/sub_agents/base.py` (`_field_enrich`, line 98 gate + line 114 `wanted` computation)
- Modify: `agents_scrapper/sub_agents/market.py:331` (SPEC, before closing `)`)
- Test: `agents/universal-agent/agents_scrapper/sub_agents/test_base.py`

**Interfaces:**
- Consumes: `DomainSpec.enable_field_enrichment` (Task 2).
- Behavior change: `_field_enrich`'s gate at line 98 currently reads `if (self.spec.report_mode != "table" and not comparison_mode) or not records: return records`. Change to also let `enable_field_enrichment` through:
```python
        eligible = self.spec.report_mode == "table" or comparison_mode or self.spec.enable_field_enrichment
        if not eligible or not records:
            return records
```
And the `wanted` computation at line 114 (`wanted = [c["field"] for c in (self.spec.table_columns or [])]`) needs a source of fields for specs that aren't `report_mode="table"` — market has no `table_columns`. Fall back to the agent's `extraction_field_groups` when `table_columns` is empty:
```python
        else:
            wanted = [c["field"] for c in (self.spec.table_columns or [])]
            if not wanted:
                wanted = [
                    f for group in (self.spec.extraction_field_groups or []) for f in group.get("fields", [])
                ]
```

- [ ] **Step 1: Write the failing test**

```python
# add to agents/universal-agent/agents_scrapper/sub_agents/test_base.py
import pytest

from agents_scrapper.sub_agents.models import DomainSpec, ScrapeJob
from agents_scrapper.sub_agents.base import BaseDomainAgent


def _spec(**overrides):
    base = dict(
        id="x", display_name="X", domain_description="", example_query="",
        extraction_field_groups=[{"name": "g", "fields": ["product_positioning", "pricing"]}],
        query_generation_prompt="", post_process_prompt="", report_mode="topical",
    )
    base.update(overrides)
    return DomainSpec(**base)


@pytest.mark.asyncio
async def test_field_enrich_skipped_for_topical_spec_by_default(monkeypatch):
    agent = BaseDomainAgent(_spec())
    records = [{"clean_data": {"company_name": "Acme"}}]
    out = await agent._field_enrich(None, records)
    assert out is records  # unchanged, gate closed


@pytest.mark.asyncio
async def test_field_enrich_runs_for_topical_spec_when_enabled(monkeypatch):
    agent = BaseDomainAgent(_spec(enable_field_enrichment=True))

    captured = {}

    async def fake_fill(records, job, search_hits, wanted_fields, session_id=None):
        captured["wanted_fields"] = wanted_fields
        return records, {}

    monkeypatch.setattr(
        "agents_scrapper.sub_agents.field_enrichment.fill_requested_fields", fake_fill,
    )

    records = [{"clean_data": {"company_name": "Acme"}}]
    await agent._field_enrich(None, records)

    assert "product_positioning" in captured["wanted_fields"]
    assert "pricing" in captured["wanted_fields"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest agents/universal-agent/agents_scrapper/sub_agents/test_base.py -v -k field_enrich_runs_for_topical`
Expected: FAIL — gate still closed for `report_mode="topical"` even with `enable_field_enrichment=True` (returns `records` unchanged before reaching `fill_requested_fields`).

- [ ] **Step 3: Implement the base.py changes described above**

(Exact replacement text given in the Interfaces section — locate current line 98 and line 114 by reading the file fresh, since Task 5 didn't touch this file but always verify current line numbers before editing.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest agents/universal-agent/agents_scrapper/sub_agents/test_base.py -v -k field_enrich`
Expected: both new tests pass

- [ ] **Step 5: Add `enable_field_enrichment=True` to market's SPEC**

```python
    enable_field_enrichment=True,
```
Add before `market.py:331`'s closing `)`.

- [ ] **Step 6: Run the full sub_agents test suite for regressions**

Run: `pytest agents/universal-agent/agents_scrapper/sub_agents/ -v`
Expected: all passed — critically, confirm `lead_generation`'s existing `_field_enrich`-dependent tests (report_mode="table") are unaffected, since the gate condition changed.

- [ ] **Step 7: Commit**

```bash
git add agents/universal-agent/agents_scrapper/sub_agents/base.py agents/universal-agent/agents_scrapper/sub_agents/market.py agents/universal-agent/agents_scrapper/sub_agents/test_base.py
git commit -m "feat: let market opt into field enrichment without switching report_mode"
```

---

### Task 7: Full regression pass

- [ ] **Step 1: Run every test file touched or exercised by this feature**

Run (from repo root):
```
pytest agents/universal-agent/scraper/anti_ban/test_vision_extract.py agents/universal-agent/orchestration/tools/test_builtin.py agents/universal-agent/agents_scrapper/sub_agents/ agents/universal-agent/orchestration/test_scraper_orchestrator.py agents/universal-agent/api/test_subagent_stream_close.py agents/universal-agent/api/test_orchestrator_routes.py -q
```
Expected: all passed (aside from the one pre-existing, unrelated failure in `tasks/test_prefect_flows.py::test_publish_artifact_persists_and_announces_live` noted earlier this session, which this feature does not touch).

- [ ] **Step 2: Confirm no other agent's behavior changed**

Spot-check: `research.py`'s SPEC still has empty `vision_priority_domains` and `enable_field_enrichment=False` (never set) — grep confirms no accidental edits landed there.
