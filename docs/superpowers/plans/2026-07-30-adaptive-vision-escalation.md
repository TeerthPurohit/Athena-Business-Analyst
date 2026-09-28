# Adaptive Vision Escalation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a reactive vision-escalation pass to `run_extraction` that rescues weak DOM extractions (low confidence or too few populated fields) with a budgeted, prioritized screenshot+vision re-read, before the existing shell-row gate deletes them.

**Architecture:** `vision_extract.py`'s return shape changes from a flat dict to a `status`-tagged dict (success/screenshot_failed/extraction_failed). A new pure module `pipeline/vision_escalation.py` (`escalate_weak_records`) is called from `run_extraction` right after the confidence/relevance score blend and before the shell-row gate. Priority, budget, confidence-blend, and merge logic all reuse existing helpers (`count_populated_fields`, `pipeline/normalisers/*`, `pipeline.deduplicator.fuzzy_match`) rather than inventing new ones.

**Tech Stack:** Python 3.11, pytest + pytest-asyncio + monkeypatch (existing style), pydantic `model_validator` (existing `Settings` pattern).

## Global Constraints

- `VISION_ESCALATION_MAX_PER_RUN >= 1`, validated at settings-load time (raise, don't silently no-op).
- `VISION_ESCALATION_PRIORITY_WEIGHTS` and `VISION_ESCALATION_CONFIDENCE_WEIGHTS` must each sum to `1.0` (± `1e-6`), validated at settings-load time.
- Budget consumed on every escalation **attempt**, regardless of outcome — never only on success (see spec's "Why attempts, not successes").
- A record is escalated **at most once, ever**, in the run — both on success and on failure (`_vision_escalated = True` set either way).
- Domain blacklisting (`intent["_vision_failed_domains"]`) triggers **only** on `status == "screenshot_failed"`, never on `extraction_failed` or a merely-low `final_confidence`.
- No new dependencies. No new normalizer/fuzzy-match logic — reuse `pipeline/normalisers/*`, `pipeline.enricher.traverse_and_normalise`, `pipeline.deduplicator.fuzzy_match`, `pipeline.enricher.count_populated_fields` exactly as they exist today.
- Follow existing test style: `monkeypatch.setattr("module.path.func", fake)`, `pytest.mark.asyncio`, no real network/API calls in CI.
- Spec of record: `docs/superpowers/specs/2026-07-30-adaptive-vision-escalation-design.md`.

---

## File Structure

- **Modify** `agents/universal-agent/scraper/anti_ban/vision_extract.py` — new `status`-tagged return shape.
- **Modify** `agents/universal-agent/scraper/anti_ban/test_vision_extract.py` — update the 4 existing tests for the new shape.
- **Modify** `agents/universal-agent/orchestration/tools/builtin.py` (`_scrape_urls`, ~line 209-249) — static-path caller adapts to the new shape.
- **Modify** `agents/universal-agent/orchestration/tools/test_builtin.py` — update/add coverage for that call site.
- **Modify** `agents/universal-agent/config/settings.py` — 8 new settings + a validator.
- **Create** `agents/universal-agent/pipeline/vision_escalation.py` — `escalate_weak_records` and its helpers.
- **Create** `agents/universal-agent/pipeline/test_vision_escalation.py` — its unit tests.
- **Modify** `agents/universal-agent/tasks/prefect_flows.py` (`run_extraction`, ~line 1570) — wire the call in.

---

### Task 1: `vision_extract.py` — new status-tagged return shape

**Files:**
- Modify: `agents/universal-agent/scraper/anti_ban/vision_extract.py`
- Test: `agents/universal-agent/scraper/anti_ban/test_vision_extract.py`

**Interfaces:**
- Produces: `async def vision_fetch_and_extract(url: str, wanted_fields: list[str], timeout: Optional[float] = None) -> dict`. Always returns a dict (never bare `None`), always has a `status` key: `"success"` | `"screenshot_failed"` | `"extraction_failed"` | `"no_fields"` (empty `wanted_fields` input — kept as its own status rather than folding into `extraction_failed`, since it's a caller error, not a runtime failure). On `"success"`: `{"status": "success", "fields": {name: {"value": ..., "confidence": ...}}, "page_confidence": float, "relevance_to_query": float, "reasoning": Optional[str], "metadata": {"model": str, "latency_ms": int, "response_schema_version": "v1", "escalation_reason": None}}`. On failure: `{"status": "screenshot_failed"|"extraction_failed", "error": str}`.

- [ ] **Step 1: Write the failing tests (rewrite the 4 existing ones for the new shape)**

```python
# agents/universal-agent/scraper/anti_ban/test_vision_extract.py
import pytest

from scraper.anti_ban.vision_extract import vision_fetch_and_extract

pytestmark = pytest.mark.asyncio


async def test_vision_fetch_and_extract_returns_success_shape(monkeypatch):
    async def fake_screenshot_camoufox(url, timeout=None):
        return b"fake-png-bytes"

    monkeypatch.setattr(
        "scraper.anti_ban.vision_extract._screenshot_via_camoufox",
        fake_screenshot_camoufox,
    )

    class FakeResponse:
        content = (
            '{"fields": {"stock_price": {"value": "18014.0", "confidence": 0.95}, '
            '"pe_ratio": {"value": "94.17", "confidence": 0.9}}, '
            '"page_confidence": 0.92, "relevance_to_query": 0.88, "reasoning": null}'
        )

    class FakeClient:
        async def ainvoke(self, messages):
            return FakeResponse()

    monkeypatch.setattr("scraper.anti_ban.vision_extract._get_client", lambda: FakeClient())

    result = await vision_fetch_and_extract(
        "https://dhan.co/stocks/sector/defence-stocks/",
        wanted_fields=["stock_price", "pe_ratio"],
    )
    assert result["status"] == "success"
    assert result["fields"]["stock_price"] == {"value": "18014.0", "confidence": 0.95}
    assert result["page_confidence"] == 0.92
    assert result["relevance_to_query"] == 0.88
    assert result["metadata"]["response_schema_version"] == "v1"


async def test_vision_fetch_and_extract_returns_screenshot_failed_status(monkeypatch):
    async def fake_screenshot_camoufox(url, timeout=None):
        raise RuntimeError("camoufox launch failed")

    monkeypatch.setattr(
        "scraper.anti_ban.vision_extract._screenshot_via_camoufox",
        fake_screenshot_camoufox,
    )

    result = await vision_fetch_and_extract("https://dhan.co/x", wanted_fields=["stock_price"])
    assert result["status"] == "screenshot_failed"
    assert "error" in result


async def test_vision_fetch_and_extract_returns_extraction_failed_on_malformed_response(monkeypatch):
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
    assert result["status"] == "extraction_failed"
    assert "error" in result


async def test_vision_fetch_and_extract_no_fields_status():
    result = await vision_fetch_and_extract("https://dhan.co/x", wanted_fields=[])
    assert result["status"] == "no_fields"
```

- [ ] **Step 2: Run to verify it fails**

Run (from repo root): `pytest agents/universal-agent/scraper/anti_ban/test_vision_extract.py -v`
Expected: FAIL — old code returns `None`/flat dicts, not `{"status": ...}`.

- [ ] **Step 3: Rewrite `vision_fetch_and_extract`**

```python
# agents/universal-agent/scraper/anti_ban/vision_extract.py
"""Vision-LLM screenshot extraction: for sources known to be reliable (see
DomainSpec.vision_priority_domains / enable_field_enrichment), read the
rendered page as an image instead of parsing HTML/text — the current
DOM-extraction pipeline can misread visually-laid-out pages (observed: a
dhan.co sector table's per-row CTA button text leaking into extracted company
names). Also called reactively by pipeline.vision_escalation for records
that extracted poorly regardless of domain — see
docs/superpowers/specs/2026-07-30-adaptive-vision-escalation-design.md.

Always returns a dict with a "status" key, never raises, never returns bare
None — callers that only care about success/failure can check
`result["status"] == "success"`; callers that need to distinguish failure
modes (e.g. to decide whether to blacklist a domain) read the status value
directly."""
import asyncio
import json
import logging
import random
import time
from typing import Optional

from agents.extraction.image_ocr import encode_image_to_base64
from agents.shared.llm_client import _get_client

logger = logging.getLogger("VisionExtract")

_RESPONSE_SCHEMA_VERSION = "v1"

_VISION_EXTRACT_SYSTEM_PROMPT = """You are a precise visual data extractor. You will be \
shown a screenshot of a rendered web page. Extract ONLY the following fields, reading \
directly from what is visibly shown: {fields}

Rules:
- Return a JSON object with this exact shape:
  {{"fields": {{"<field_name>": {{"value": <value or null>, "confidence": <0.0-1.0>}}, ...}}, \
"page_confidence": <0.0-1.0 overall confidence in this read>, \
"relevance_to_query": <0.0-1.0, always 1.0 unless the page is clearly off-topic>, \
"reasoning": <string or null, only fill this in when confidence is low or the read is \
ambiguous>}}
- Include every key in {fields} inside "fields", even if not visible (value: null, confidence: 0.0).
- A field not visible on the page must be null — never invent or guess a value.
- Ignore UI chrome: buttons, ads, navigation menus, "analyze with AI" or similar \
call-to-action widgets are never field data.
- Numbers must be copied exactly as shown (no rounding, no reformatting).
- Return ONLY the JSON object, no other text."""


async def _screenshot_via_camoufox(url: str, timeout: Optional[float] = None) -> bytes:
    """Minimal Camoufox scaffold that returns a full-page PNG screenshot instead of
    HTML — duplicated from browser_evader.fetch_with_camoufox rather than changing
    that function's return shape, since it has other callers expecting `str`."""
    from camoufox.async_api import AsyncCamoufox as Camoufox

    if timeout is None:
        timeout = 30.0
    launch_timeout = min(30.0, max(10.0, timeout * 0.5))

    cf_manager = Camoufox(i_know_what_im_doing=True, headless="virtual")
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


def _parse_json_response(raw: str) -> dict:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


async def vision_fetch_and_extract(
    url: str, wanted_fields: list[str], timeout: Optional[float] = None,
) -> dict:
    """Screenshot `url` and vision-extract `wanted_fields` from it. Always returns a
    dict with a "status" key ("success" | "screenshot_failed" | "extraction_failed" |
    "no_fields") — never raises, never returns bare None. Callers that only care
    about success/failure treat any non-"success" status as failure."""
    if not wanted_fields:
        return {"status": "no_fields"}

    try:
        started = time.monotonic()
        screenshot_bytes = await _screenshot_via_camoufox(url, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        logger.warning("vision_extract: screenshot failed for %r | %s", url, e)
        return {"status": "screenshot_failed", "error": str(e)}

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
        parsed = _parse_json_response(response.content or "")
        if not isinstance(parsed, dict) or "fields" not in parsed:
            return {"status": "extraction_failed", "error": "malformed response shape"}

        latency_ms = int((time.monotonic() - started) * 1000)
        return {
            "status": "success",
            "fields": {k: parsed["fields"].get(k) for k in wanted_fields if isinstance(parsed.get("fields"), dict)},
            "page_confidence": parsed.get("page_confidence"),
            "relevance_to_query": parsed.get("relevance_to_query", 1.0),
            "reasoning": parsed.get("reasoning"),
            "metadata": {
                "model": getattr(client, "model", None),
                "latency_ms": latency_ms,
                "response_schema_version": _RESPONSE_SCHEMA_VERSION,
                "escalation_reason": None,
            },
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("vision_extract: LLM extraction failed for %r | %s", url, e)
        return {"status": "extraction_failed", "error": str(e)}
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest agents/universal-agent/scraper/anti_ban/test_vision_extract.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add agents/universal-agent/scraper/anti_ban/vision_extract.py agents/universal-agent/scraper/anti_ban/test_vision_extract.py
git commit -m "feat: give vision_fetch_and_extract a status-tagged return shape"
```

---

### Task 2: Adapt the static-path caller in `builtin.py` to the new shape

**Files:**
- Modify: `agents/universal-agent/orchestration/tools/builtin.py` (`_scrape_urls`, lines 224-249)
- Test: `agents/universal-agent/orchestration/tools/test_builtin.py`

**Interfaces:**
- Consumes: `vision_fetch_and_extract(url, wanted_fields) -> dict` (Task 1) — reads `result["status"]` and, on success, `result["fields"]` (a `{name: {"value", "confidence"}}` dict — must be flattened to `{name: value}` before use as `clean_data`, since every other `clean_data` consumer in this codebase expects flat values) and `result["page_confidence"]`.

- [ ] **Step 1: Write the failing test**

```python
# add to agents/universal-agent/orchestration/tools/test_builtin.py
import pytest

from orchestration.state import AgentContext
from orchestration.tools import builtin


@pytest.mark.asyncio
async def test_scrape_urls_vision_priority_uses_real_page_confidence(monkeypatch):
    ctx = AgentContext(message="x", session_id="s1", vision_priority_domains=["dhan.co"])

    async def fake_vision_fetch(url, wanted_fields, timeout=None):
        return {
            "status": "success",
            "fields": {"stock_price": {"value": "18014.0", "confidence": 0.95}},
            "page_confidence": 0.73,
            "relevance_to_query": 0.9,
            "reasoning": None,
            "metadata": {"model": "gpt-4o", "latency_ms": 100, "response_schema_version": "v1", "escalation_reason": None},
        }

    monkeypatch.setattr("orchestration.tools.builtin.vision_fetch_and_extract", fake_vision_fetch)

    await builtin._scrape_urls(ctx, urls=["https://dhan.co/stocks/sector/defence-stocks/"])

    record = next(r for r in ctx.ranked if r.get("url") == "https://dhan.co/stocks/sector/defence-stocks/")
    assert record["clean_data"] == {"stock_price": "18014.0"}
    assert record["extraction_confidence"] == 0.73
    assert record["_vision_extracted"] is True


@pytest.mark.asyncio
async def test_scrape_urls_vision_priority_failure_status_treated_as_failure(monkeypatch):
    ctx = AgentContext(message="x", session_id="s1", vision_priority_domains=["dhan.co"])

    async def fake_vision_fetch(url, wanted_fields, timeout=None):
        return {"status": "screenshot_failed", "error": "timeout"}

    monkeypatch.setattr("orchestration.tools.builtin.vision_fetch_and_extract", fake_vision_fetch)

    await builtin._scrape_urls(ctx, urls=["https://dhan.co/stocks/sector/defence-stocks/"])

    assert ctx.ranked == []
    assert "https://dhan.co/stocks/sector/defence-stocks/" in ctx.empty_urls
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest agents/universal-agent/orchestration/tools/test_builtin.py -v -k vision_priority`
Expected: FAIL — current code does `if result:` (truthy dict check, always true now since a status dict is always truthy) and reads `result` directly as `clean_data`, so `record["clean_data"]` would be the whole status dict, not `{"stock_price": "18014.0"}`, and `extraction_confidence` would still be the hardcoded `0.95`.

- [ ] **Step 3: Update the routing block**

In `orchestration/tools/builtin.py`, replace lines 230-249 (the `for u in vision_priority_urls:` loop body):

```python
        for u in vision_priority_urls:
            ctx.attempted_urls.add(u)
            result = await vision_fetch_and_extract(u, wanted_fields)
            if result.get("status") == "success":
                clean_data = {k: (v or {}).get("value") for k, v in (result.get("fields") or {}).items()}
                record = {
                    "url": u,
                    "domain": urlparse(u).netloc,
                    "clean_data": clean_data,
                    "extraction_confidence": result.get("page_confidence"),
                    "relevance_to_query": result.get("relevance_to_query", 1.0),
                    "_vision_extracted": True,
                }
                vision_collected.append(record)
                ctx.ranked.append(record)
            else:
                # A priority domain that fails vision extraction (blocked, down) would
                # very likely fail the normal text pipeline too — dropped for this pass
                # rather than silently retried; the caller's own retry/broaden logic
                # (already in place for every URL) covers a genuine second attempt.
                # This caller doesn't need to distinguish screenshot_failed from
                # extraction_failed (that distinction is for pipeline.vision_escalation's
                # domain-memory logic) — any non-success is treated the same here.
                ctx.bump("failed_pages")
                ctx.empty_urls.add(u)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest agents/universal-agent/orchestration/tools/test_builtin.py -v -k vision_priority`
Expected: 2 passed (new) — run the full file too to check no regression:
Run: `pytest agents/universal-agent/orchestration/tools/test_builtin.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add agents/universal-agent/orchestration/tools/builtin.py agents/universal-agent/orchestration/tools/test_builtin.py
git commit -m "feat: use real page_confidence from vision_fetch_and_extract instead of hardcoded 0.95"
```

---

### Task 3: Settings

**Files:**
- Modify: `agents/universal-agent/config/settings.py`

**Interfaces:**
- Produces: 8 new `Settings` fields (see below) plus a validator that raises `ValueError` on misconfiguration.

- [ ] **Step 1: Write the failing test**

```python
# Run this as a quick inline check (no dedicated settings test file exists in this
# codebase today — Settings is validated via direct construction, matching how
# config/settings.py itself is exercised elsewhere in this repo):
python -c "
import sys; sys.path.insert(0, 'agents/universal-agent')
from config.settings import Settings
try:
    Settings(VISION_ESCALATION_MAX_PER_RUN=0)
    print('FAIL: should have raised')
except ValueError:
    print('OK: raised on MAX_PER_RUN=0')
try:
    Settings(VISION_ESCALATION_PRIORITY_WEIGHTS={'relevance': 0.5, 'missing_fields': 0.5, 'low_confidence': 0.5, 'domain': 0.5})
    print('FAIL: should have raised')
except ValueError:
    print('OK: raised on weights not summing to 1.0')
"
```

- [ ] **Step 2: Run to verify it fails**

Run the command above.
Expected: both print `FAIL: should have raised` (fields don't exist yet, so `Settings(...)` either raises `TypeError`/ignores unknown kwargs depending on `model_config`, or accepts them silently — either way, not the `ValueError` we want).

- [ ] **Step 3: Add the settings + validator**

In `config/settings.py`, add after the existing `MAX_INPUT_TOKENS: int = 2000` line (or any other clearly-delimited location in the `Settings` class body — exact position doesn't matter, pydantic fields are order-independent for this purpose):

```python
    # ── Adaptive vision escalation (docs/superpowers/specs/2026-07-30-adaptive-vision-escalation-design.md) ──
    VISION_ESCALATION_ENABLED: bool = True
    VISION_ESCALATION_CONFIDENCE_MAX: float = 0.5      # matches the extractor's own low-confidence warning threshold
    VISION_ESCALATION_SKIP_ABOVE: float = 0.9
    VISION_ESCALATION_MIN_FIELDS: int = 2              # matches run_extraction's shell-gate threshold
    VISION_ESCALATION_MAX_PER_RUN: int = 10
    VISION_ESCALATION_BUDGET_RATIO: float = 0.1
    VISION_ESCALATION_REPLACE_MARGIN: float = 0.15
    VISION_ESCALATION_PRIORITY_WEIGHTS: dict = {
        "relevance": 0.40, "missing_fields": 0.30, "low_confidence": 0.20, "domain": 0.10,
    }
    VISION_ESCALATION_CONFIDENCE_WEIGHTS: dict = {
        "page_confidence": 0.50, "schema_validation": 0.20, "dom_vision_agreement": 0.15,
        "text_grounding": 0.10, "domain_reputation": 0.05,
    }

    @model_validator(mode="after")
    def validate_vision_escalation_settings(self) -> "Settings":
        if self.VISION_ESCALATION_MAX_PER_RUN < 1:
            raise ValueError(
                "VISION_ESCALATION_MAX_PER_RUN must be >= 1 — use "
                "VISION_ESCALATION_ENABLED=False to disable escalation instead of 0."
            )
        for name, weights in (
            ("VISION_ESCALATION_PRIORITY_WEIGHTS", self.VISION_ESCALATION_PRIORITY_WEIGHTS),
            ("VISION_ESCALATION_CONFIDENCE_WEIGHTS", self.VISION_ESCALATION_CONFIDENCE_WEIGHTS),
        ):
            total = sum(weights.values())
            if abs(total - 1.0) > 1e-6:
                raise ValueError(f"{name} must sum to 1.0, got {total}")
        return self
```

- [ ] **Step 4: Run to verify it passes**

Run the Step 1 command again.
Expected: both print `OK: ...`

- [ ] **Step 5: Confirm the module still imports cleanly with defaults**

Run: `python -c "import sys; sys.path.insert(0, 'agents/universal-agent'); from config.settings import settings; print(settings.VISION_ESCALATION_MAX_PER_RUN, settings.VISION_ESCALATION_ENABLED)"`
Expected: `10 True`

- [ ] **Step 6: Commit**

```bash
git add agents/universal-agent/config/settings.py
git commit -m "feat: add VISION_ESCALATION_* settings with load-time validation"
```

---

### Task 4: `pipeline/vision_escalation.py` — core module

**Files:**
- Create: `agents/universal-agent/pipeline/vision_escalation.py`
- Test: `agents/universal-agent/pipeline/test_vision_escalation.py`

**Interfaces:**
- Consumes: `vision_fetch_and_extract(url, wanted_fields) -> dict` (Task 1); `pipeline.enricher.count_populated_fields(record, intent_type, requested_fields) -> int` (exists); `pipeline.enricher.traverse_and_normalise(value, key_name) -> Any` (exists); `pipeline.deduplicator.fuzzy_match(a, b, threshold=90.0) -> bool` (exists); `pipeline.normalisers.{normalise_price, normalise_number, normalise_date, normalise_email, normalise_phone, normalise_url}` (exist); `config.settings.settings` (Task 3's new fields).
- Produces: `async def escalate_weak_records(enriched_all: list[dict], *, canon: str, requested_fields: list[str], intent: dict) -> dict` — mutates `enriched_all`'s records in place (merges vision data into eligible ones) and returns `{"candidates": int, "budget": int, "escalated": int, "succeeded": int, "mean_confidence_delta": float}`.

- [ ] **Step 1: Write the failing tests**

```python
# agents/universal-agent/pipeline/test_vision_escalation.py
import pytest

from pipeline import vision_escalation as ve

pytestmark = pytest.mark.asyncio


def _record(url="https://example.com/a", confidence=0.3, clean_data=None, base_score=0.5, relevance=0.8, **extra):
    r = {
        "url": url,
        "domain": "example.com",
        "clean_data": clean_data or {"company_name": "Acme"},
        "extraction_confidence": confidence,
        "relevance_to_query": relevance,
        "base_score": base_score,
        "raw_text": "Acme is listed here. Price: 125000 INR.",
    }
    r.update(extra)
    return r


# ── Eligibility ──────────────────────────────────────────────────────

async def test_low_confidence_alone_is_eligible(monkeypatch):
    called = []

    async def fake_vision(url, wanted_fields, timeout=None):
        called.append(url)
        return {"status": "extraction_failed", "error": "x"}

    monkeypatch.setattr(ve, "vision_fetch_and_extract", fake_vision)
    records = [_record(confidence=0.2, clean_data={"company_name": "Acme", "ticker": "ACME", "price": "1"})]
    await ve.escalate_weak_records(records, canon="generic", requested_fields=["company_name"], intent={})
    assert called == ["https://example.com/a"]


async def test_missing_fields_alone_is_eligible(monkeypatch):
    called = []

    async def fake_vision(url, wanted_fields, timeout=None):
        called.append(url)
        return {"status": "extraction_failed", "error": "x"}

    monkeypatch.setattr(ve, "vision_fetch_and_extract", fake_vision)
    records = [_record(confidence=0.95, clean_data={"company_name": "Acme"})]
    await ve.escalate_weak_records(records, canon="generic", requested_fields=["company_name"], intent={})
    assert called == ["https://example.com/a"]


async def test_neither_low_confidence_nor_missing_fields_is_not_eligible(monkeypatch):
    called = []

    async def fake_vision(url, wanted_fields, timeout=None):
        called.append(url)
        return {"status": "extraction_failed", "error": "x"}

    monkeypatch.setattr(ve, "vision_fetch_and_extract", fake_vision)
    records = [_record(confidence=0.95, clean_data={"company_name": "Acme", "ticker": "ACME", "price": "1"})]
    await ve.escalate_weak_records(records, canon="generic", requested_fields=["company_name"], intent={})
    assert called == []


async def test_skip_above_boundary_never_escalates_very_high_confidence(monkeypatch):
    called = []

    async def fake_vision(url, wanted_fields, timeout=None):
        called.append(url)
        return {"status": "extraction_failed", "error": "x"}

    monkeypatch.setattr(ve, "vision_fetch_and_extract", fake_vision)
    # confidence 0.95 > SKIP_ABOVE (0.9) — must never escalate even with 1 field.
    records = [_record(confidence=0.95, clean_data={"company_name": "Acme"})]
    await ve.escalate_weak_records(records, canon="generic", requested_fields=["company_name"], intent={})
    assert called == []


async def test_already_vision_escalated_record_is_never_reattempted(monkeypatch):
    called = []

    async def fake_vision(url, wanted_fields, timeout=None):
        called.append(url)
        return {"status": "extraction_failed", "error": "x"}

    monkeypatch.setattr(ve, "vision_fetch_and_extract", fake_vision)
    records = [_record(confidence=0.1, clean_data={"company_name": "Acme"}, _vision_escalated=True)]
    await ve.escalate_weak_records(records, canon="generic", requested_fields=["company_name"], intent={})
    assert called == []


async def test_vision_extracted_static_path_record_is_never_reattempted(monkeypatch):
    called = []

    async def fake_vision(url, wanted_fields, timeout=None):
        called.append(url)
        return {"status": "extraction_failed", "error": "x"}

    monkeypatch.setattr(ve, "vision_fetch_and_extract", fake_vision)
    records = [_record(confidence=0.1, clean_data={"company_name": "Acme"}, _vision_extracted=True)]
    await ve.escalate_weak_records(records, canon="generic", requested_fields=["company_name"], intent={})
    assert called == []


async def test_name_seeded_record_is_never_attempted(monkeypatch):
    called = []

    async def fake_vision(url, wanted_fields, timeout=None):
        called.append(url)
        return {"status": "extraction_failed", "error": "x"}

    monkeypatch.setattr(ve, "vision_fetch_and_extract", fake_vision)
    records = [_record(confidence=0.1, clean_data={"company_name": "Acme"}, _name_seeded=True)]
    await ve.escalate_weak_records(records, canon="generic", requested_fields=["company_name"], intent={})
    assert called == []


async def test_record_without_url_is_never_attempted(monkeypatch):
    called = []

    async def fake_vision(url, wanted_fields, timeout=None):
        called.append(url)
        return {"status": "extraction_failed", "error": "x"}

    monkeypatch.setattr(ve, "vision_fetch_and_extract", fake_vision)
    records = [_record(confidence=0.1, clean_data={"company_name": "Acme"})]
    records[0]["url"] = None
    await ve.escalate_weak_records(records, canon="generic", requested_fields=["company_name"], intent={})
    assert called == []


# ── Priority ordering + tie-breaking ────────────────────────────────

async def test_priority_ordering_spends_budget_on_best_candidates_first(monkeypatch):
    call_order = []

    async def fake_vision(url, wanted_fields, timeout=None):
        call_order.append(url)
        return {"status": "extraction_failed", "error": "x"}

    monkeypatch.setattr(ve, "vision_fetch_and_extract", fake_vision)
    from config.settings import settings
    monkeypatch.setattr(settings, "VISION_ESCALATION_MAX_PER_RUN", 1)
    monkeypatch.setattr(settings, "VISION_ESCALATION_BUDGET_RATIO", 1.0)

    low_priority = _record(url="https://a.com/1", confidence=0.4, relevance=0.1, base_score=0.1)
    high_priority = _record(url="https://a.com/2", confidence=0.1, relevance=0.9, base_score=0.9)
    await ve.escalate_weak_records(
        [low_priority, high_priority], canon="generic", requested_fields=["company_name"], intent={},
    )
    assert call_order == ["https://a.com/2"]


async def test_tie_break_falls_back_to_original_order(monkeypatch):
    call_order = []

    async def fake_vision(url, wanted_fields, timeout=None):
        call_order.append(url)
        return {"status": "extraction_failed", "error": "x"}

    monkeypatch.setattr(ve, "vision_fetch_and_extract", fake_vision)
    from config.settings import settings
    monkeypatch.setattr(settings, "VISION_ESCALATION_MAX_PER_RUN", 2)
    monkeypatch.setattr(settings, "VISION_ESCALATION_BUDGET_RATIO", 1.0)

    # Identical priority/base_score/missing-fields inputs, different URLs — order
    # must follow input order (first-in-list first), not be implementation-defined.
    r1 = _record(url="https://a.com/1", confidence=0.3, relevance=0.5, base_score=0.5)
    r2 = _record(url="https://a.com/2", confidence=0.3, relevance=0.5, base_score=0.5)
    await ve.escalate_weak_records([r1, r2], canon="generic", requested_fields=["company_name"], intent={})
    assert call_order == ["https://a.com/1", "https://a.com/2"]


# ── Budget math ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "n_records,expected_budget",
    [(10, 3), (50, 5), (100, 10), (500, 10)],
)
async def test_budget_math_at_various_scales(monkeypatch, n_records, expected_budget):
    attempts = {"count": 0}

    async def fake_vision(url, wanted_fields, timeout=None):
        attempts["count"] += 1
        return {"status": "extraction_failed", "error": "x"}

    monkeypatch.setattr(ve, "vision_fetch_and_extract", fake_vision)
    records = [_record(url=f"https://a.com/{i}", confidence=0.1) for i in range(n_records)]
    await ve.escalate_weak_records(records, canon="generic", requested_fields=["company_name"], intent={})
    assert attempts["count"] == expected_budget


# ── Budget consumption on failure ────────────────────────────────────

async def test_budget_exhausts_on_attempts_not_successes(monkeypatch):
    attempts = {"count": 0}

    async def fake_vision(url, wanted_fields, timeout=None):
        attempts["count"] += 1
        return {"status": "extraction_failed", "error": "always fails"}

    monkeypatch.setattr(ve, "vision_fetch_and_extract", fake_vision)
    from config.settings import settings
    monkeypatch.setattr(settings, "VISION_ESCALATION_MAX_PER_RUN", 3)
    monkeypatch.setattr(settings, "VISION_ESCALATION_BUDGET_RATIO", 1.0)

    records = [_record(url=f"https://a.com/{i}", confidence=0.1) for i in range(10)]
    result = await ve.escalate_weak_records(records, canon="generic", requested_fields=["company_name"], intent={})
    assert attempts["count"] == 3
    assert result["escalated"] == 3
    assert result["succeeded"] == 0


async def test_domain_memory_limits_same_domain_to_one_attempt(monkeypatch):
    attempts = {"count": 0}

    async def fake_vision(url, wanted_fields, timeout=None):
        attempts["count"] += 1
        return {"status": "screenshot_failed", "error": "blocked"}

    monkeypatch.setattr(ve, "vision_fetch_and_extract", fake_vision)
    from config.settings import settings
    monkeypatch.setattr(settings, "VISION_ESCALATION_MAX_PER_RUN", 10)
    monkeypatch.setattr(settings, "VISION_ESCALATION_BUDGET_RATIO", 1.0)

    records = [
        _record(url=f"https://same-domain.com/{i}", confidence=0.1, domain="same-domain.com")
        for i in range(5)
    ]
    intent = {}
    result = await ve.escalate_weak_records(records, canon="generic", requested_fields=["company_name"], intent=intent)
    assert attempts["count"] == 1
    assert "same-domain.com" in intent["_vision_failed_domains"]
    assert result["escalated"] == 1


# ── screenshot_failed vs extraction_failed domain-memory distinction ──

async def test_extraction_failed_does_not_blacklist_domain(monkeypatch):
    async def fake_vision(url, wanted_fields, timeout=None):
        return {"status": "extraction_failed", "error": "llm timeout"}

    monkeypatch.setattr(ve, "vision_fetch_and_extract", fake_vision)
    intent = {}
    records = [_record(url="https://a.com/1", confidence=0.1, domain="a.com")]
    await ve.escalate_weak_records(records, canon="generic", requested_fields=["company_name"], intent=intent)
    assert intent.get("_vision_failed_domains", set()) == set()


# ── Termination guarantees ───────────────────────────────────────────

async def test_domain_in_failed_set_is_skipped_even_if_top_priority(monkeypatch):
    called = []

    async def fake_vision(url, wanted_fields, timeout=None):
        called.append(url)
        return {"status": "extraction_failed", "error": "x"}

    monkeypatch.setattr(ve, "vision_fetch_and_extract", fake_vision)
    intent = {"_vision_failed_domains": {"a.com"}}
    records = [_record(url="https://a.com/1", confidence=0.05, relevance=1.0, base_score=1.0, domain="a.com")]
    await ve.escalate_weak_records(records, canon="generic", requested_fields=["company_name"], intent=intent)
    assert called == []


# ── Merge precedence + confidence formula ────────────────────────────

async def test_dom_missing_field_filled_from_vision():
    async def fake_vision(url, wanted_fields, timeout=None):
        return {
            "status": "success",
            "fields": {"ticker": {"value": "ACME", "confidence": 0.9}},
            "page_confidence": 0.8,
            "relevance_to_query": 0.9,
            "reasoning": None,
            "metadata": {"escalation_reason": "MISSING_FIELDS"},
        }

    import pipeline.vision_escalation as ve_mod
    ve_mod.vision_fetch_and_extract = fake_vision  # direct monkeypatch without fixture, single test
    records = [_record(confidence=0.4, clean_data={"company_name": "Acme"})]
    await ve_mod.escalate_weak_records(records, canon="generic", requested_fields=["company_name", "ticker"], intent={})
    assert records[0]["clean_data"]["ticker"] == "ACME"
    assert records[0]["clean_data"]["company_name"] == "Acme"  # untouched, DOM already had it
    assert records[0]["_vision_escalated"] is True


async def test_failed_attempt_still_marks_vision_escalated(monkeypatch):
    async def fake_vision(url, wanted_fields, timeout=None):
        return {"status": "screenshot_failed", "error": "x"}

    monkeypatch.setattr(ve, "vision_fetch_and_extract", fake_vision)
    records = [_record(confidence=0.1, clean_data={"company_name": "Acme"})]
    await ve.escalate_weak_records(records, canon="generic", requested_fields=["company_name"], intent={})
    assert records[0]["_vision_escalated"] is True
    assert records[0]["clean_data"] == {"company_name": "Acme"}  # unchanged
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest agents/universal-agent/pipeline/test_vision_escalation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.vision_escalation'`

- [ ] **Step 3: Implement `pipeline/vision_escalation.py`**

```python
# agents/universal-agent/pipeline/vision_escalation.py
"""Reactive vision-escalation pass for weak DOM extractions.

Called from tasks.prefect_flows.run_extraction, after the confidence/
relevance score blend and BEFORE the shell-row drop gate — see
docs/superpowers/specs/2026-07-30-adaptive-vision-escalation-design.md for
the full design rationale (eligibility, priority, budget, confidence
formula, merge precedence, termination guarantees).

Complementary to (not a replacement for) the static vision_priority_domains
mechanism in orchestration/tools/builtin.py: that mechanism is proactive for
a short, known-DOM-hostile domain list; this pass is the reactive net for
everything else. A record already touched by either mechanism
(_vision_extracted or _vision_escalated) is never re-attempted here.
"""
from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlparse

from scraper.anti_ban.vision_extract import vision_fetch_and_extract


def _missing_field_ratio(record: dict, requested_fields: list[str]) -> float:
    from pipeline.enricher import count_populated_fields

    total = max(1, len(requested_fields))
    populated = count_populated_fields(record, "generic", requested_fields)
    return 1.0 - min(1.0, populated / total)


def _domain_of(record: dict) -> Optional[str]:
    domain = record.get("domain")
    if domain:
        return domain
    url = record.get("url")
    if not url:
        return None
    try:
        return urlparse(url).netloc or None
    except Exception:  # noqa: BLE001
        return None


def _is_eligible(record: dict, requested_fields: list[str], failed_domains: set) -> bool:
    from config.settings import settings
    from pipeline.enricher import count_populated_fields

    if not record.get("url"):
        return False
    if record.get("_vision_extracted") or record.get("_vision_escalated") or record.get("_name_seeded"):
        return False
    confidence = record.get("extraction_confidence")
    confidence = 0.0 if confidence is None else float(confidence)
    if confidence > settings.VISION_ESCALATION_SKIP_ABOVE:
        return False
    populated = count_populated_fields(record, "generic", requested_fields)
    weak_confidence = confidence < settings.VISION_ESCALATION_CONFIDENCE_MAX
    weak_fields = populated < settings.VISION_ESCALATION_MIN_FIELDS
    if not (weak_confidence or weak_fields):
        return False
    domain = _domain_of(record)
    if domain and domain in failed_domains:
        return False
    return True


def _priority(record: dict, requested_fields: list[str]) -> float:
    from config.settings import settings

    weights = settings.VISION_ESCALATION_PRIORITY_WEIGHTS
    relevance = float(record.get("relevance_to_query", 1.0) or 0.0)
    confidence = float(record.get("extraction_confidence") or 0.0)
    base_score = float(record.get("base_score") or 0.0)
    return (
        weights["relevance"] * relevance
        + weights["missing_fields"] * _missing_field_ratio(record, requested_fields)
        + weights["low_confidence"] * (1 - confidence)
        + weights["domain"] * base_score
    )


def _sort_key(record: dict, requested_fields: list[str], index: int) -> tuple:
    priority = _priority(record, requested_fields)
    base_score = float(record.get("base_score") or 0.0)
    missing_ratio = _missing_field_ratio(record, requested_fields)
    # Stable sort: (-priority, -base_score, -missing_ratio, index) — index as the
    # explicit final tiebreak makes the deterministic order visible in the sort
    # key itself rather than relying only on Python's stable-sort side effect.
    return (-priority, -base_score, -missing_ratio, index)


_FIELD_NORMALISERS = None


def _get_field_normalisers():
    global _FIELD_NORMALISERS
    if _FIELD_NORMALISERS is None:
        from pipeline.normalisers import (
            normalise_email, normalise_number, normalise_phone,
            normalise_price, normalise_url,
        )
        from pipeline.normalisers.date_normaliser import normalise_date
        _FIELD_NORMALISERS = [
            (("price", "cost"), normalise_price),
            (("date", "time", "published"), normalise_date),
            (("count", "votes", "likes", "followers", "rating"), normalise_number),
            (("url", "link", "website"), normalise_url),
            (("email", "mail"), normalise_email),
            (("phone", "telephone", "mobile", "contact_number"), normalise_phone),
        ]
    return _FIELD_NORMALISERS


def _normalise_for_field(field_name: str, value: Any) -> Any:
    if value is None:
        return None
    key_lower = (field_name or "").lower()
    for keywords, fn in _get_field_normalisers():
        if any(w in key_lower for w in keywords):
            normalised = fn(value)
            return normalised if normalised is not None else value
    return value


def _field_passes_schema(field_name: str, value: Any) -> bool:
    if value is None:
        return False
    key_lower = (field_name or "").lower()
    for keywords, fn in _get_field_normalisers():
        if any(w in key_lower for w in keywords):
            return fn(value) is not None
    return bool(str(value).strip())


def _schema_validation_score(vision_fields: dict) -> float:
    if not vision_fields:
        return 1.0
    passed = sum(1 for name, f in vision_fields.items() if _field_passes_schema(name, (f or {}).get("value")))
    return passed / len(vision_fields)


def _values_agree(field_name: str, dom_value: Any, vision_value: Any) -> bool:
    from pipeline.deduplicator import fuzzy_match

    if dom_value is None or vision_value is None:
        return False
    dom_norm = _normalise_for_field(field_name, dom_value)
    vision_norm = _normalise_for_field(field_name, vision_value)
    if dom_norm == vision_norm:
        return True
    return fuzzy_match(str(dom_value), str(vision_value))


def _dom_vision_agreement(clean_data: dict, vision_fields: dict) -> float:
    overlap = [name for name in vision_fields if name in clean_data and clean_data.get(name) is not None]
    if not overlap:
        return 1.0
    agreeing = sum(1 for name in overlap if _values_agree(name, clean_data.get(name), (vision_fields[name] or {}).get("value")))
    return agreeing / len(overlap)


def _text_grounding(clean_data: dict, vision_fields: dict, raw_text: str) -> float:
    vision_only = [
        name for name, f in vision_fields.items()
        if name not in clean_data or clean_data.get(name) is None
    ]
    if not vision_only:
        return 1.0
    raw_text = raw_text or ""
    grounded = 0
    for name in vision_only:
        value = (vision_fields[name] or {}).get("value")
        if value is None:
            continue
        normalised = _normalise_for_field(name, value)
        haystack = raw_text.lower()
        if str(normalised).lower() in haystack or str(value).lower() in haystack:
            grounded += 1
    return grounded / len(vision_only)


def _final_confidence(record: dict, vision_result: dict) -> float:
    from config.settings import settings

    weights = settings.VISION_ESCALATION_CONFIDENCE_WEIGHTS
    clean_data = record.get("clean_data") or {}
    vision_fields = vision_result.get("fields") or {}
    page_confidence = float(vision_result.get("page_confidence") or 0.0)
    schema_validation = _schema_validation_score(vision_fields)
    dom_vision_agreement = _dom_vision_agreement(clean_data, vision_fields)
    text_grounding = _text_grounding(clean_data, vision_fields, record.get("raw_text") or "")
    domain_reputation = float(record.get("base_score") or 0.0)
    return (
        weights["page_confidence"] * page_confidence
        + weights["schema_validation"] * schema_validation
        + weights["dom_vision_agreement"] * dom_vision_agreement
        + weights["text_grounding"] * text_grounding
        + weights["domain_reputation"] * domain_reputation
    )


def _merge_vision_into_record(record: dict, vision_result: dict, final_confidence: float, escalation_reason: str) -> None:
    from config.settings import settings

    clean_data = record.setdefault("clean_data", {})
    vision_fields = vision_result.get("fields") or {}
    old_confidence = float(record.get("extraction_confidence") or 0.0)
    margin = settings.VISION_ESCALATION_REPLACE_MARGIN

    for name, field in vision_fields.items():
        if not field:
            continue
        value = field.get("value")
        if value is None:
            continue
        dom_value = clean_data.get(name)
        if dom_value is None or dom_value == "":
            clean_data[name] = value
            continue
        if (
            old_confidence < settings.VISION_ESCALATION_CONFIDENCE_MAX
            and (final_confidence - old_confidence) > margin
            and _field_passes_schema(name, value)
        ):
            clean_data[name] = value
        # else: keep DOM's existing value (rules 1 and 4 of Merge precedence)

    record["extraction_confidence"] = final_confidence
    record["_vision_escalated"] = True
    record["_escalation_reason"] = escalation_reason


async def escalate_weak_records(
    enriched_all: list[dict], *, canon: str, requested_fields: list[str], intent: dict,
) -> dict:
    from config.settings import settings

    metrics = {"candidates": 0, "budget": 0, "escalated": 0, "succeeded": 0, "mean_confidence_delta": 0.0}
    if not settings.VISION_ESCALATION_ENABLED or not enriched_all:
        return metrics

    failed_domains = intent.setdefault("_vision_failed_domains", set())

    candidates = [r for r in enriched_all if _is_eligible(r, requested_fields, failed_domains)]
    metrics["candidates"] = len(candidates)
    if not candidates:
        return metrics

    budget = min(
        settings.VISION_ESCALATION_MAX_PER_RUN,
        max(3, int(len(enriched_all) * settings.VISION_ESCALATION_BUDGET_RATIO)),
    )
    metrics["budget"] = budget

    ordered = sorted(
        enumerate(candidates), key=lambda pair: _sort_key(pair[1], requested_fields, pair[0]),
    )

    confidence_deltas: list[float] = []
    attempts = 0
    for _, record in ordered:
        if attempts >= budget:
            break
        domain = _domain_of(record)
        if domain and domain in failed_domains:
            continue

        confidence = record.get("extraction_confidence")
        confidence = 0.0 if confidence is None else float(confidence)
        populated = record.get("clean_data") or {}
        from pipeline.enricher import count_populated_fields
        weak_confidence = confidence < settings.VISION_ESCALATION_CONFIDENCE_MAX
        weak_fields = count_populated_fields(record, "generic", requested_fields) < settings.VISION_ESCALATION_MIN_FIELDS
        reason = "BOTH" if (weak_confidence and weak_fields) else ("LOW_CONFIDENCE" if weak_confidence else "MISSING_FIELDS")

        attempts += 1
        metrics["escalated"] += 1
        old_confidence = confidence
        vision_result = await vision_fetch_and_extract(record["url"], requested_fields)

        if vision_result.get("status") == "screenshot_failed":
            if domain:
                failed_domains.add(domain)
            record["_vision_escalated"] = True
            continue
        if vision_result.get("status") != "success":
            record["_vision_escalated"] = True
            continue

        final_confidence = _final_confidence(record, vision_result)
        _merge_vision_into_record(record, vision_result, final_confidence, reason)
        metrics["succeeded"] += 1
        confidence_deltas.append(final_confidence - old_confidence)

    if confidence_deltas:
        metrics["mean_confidence_delta"] = sum(confidence_deltas) / len(confidence_deltas)
    return metrics
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest agents/universal-agent/pipeline/test_vision_escalation.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add agents/universal-agent/pipeline/vision_escalation.py agents/universal-agent/pipeline/test_vision_escalation.py
git commit -m "feat: add adaptive vision escalation pass for weak DOM extractions"
```

---

### Task 5: Wire into `run_extraction`

**Files:**
- Modify: `agents/universal-agent/tasks/prefect_flows.py` (`run_extraction`, around line 1570-1572)
- Test: `agents/universal-agent/tasks/test_prefect_flows.py`

**Interfaces:**
- Consumes: `escalate_weak_records(enriched_all, canon=canon, requested_fields=requested_fields, intent=intent) -> dict` (Task 4).
- Produces: escalation metrics pushed through the existing `publish_metrics(session_id, **metrics)` (already defined in this file, used elsewhere in `run_extraction`'s caller chain) — only when `intent.get("session_id")` is available (mirrors how other metrics publishers in this file guard on session_id being present).

- [ ] **Step 1: Write the failing test**

```python
# add to agents/universal-agent/tasks/test_prefect_flows.py
import pytest


@pytest.mark.asyncio
async def test_run_extraction_calls_escalate_weak_records_before_shell_gate(monkeypatch):
    import tasks.prefect_flows as pf

    calls = []

    async def fake_escalate(enriched_all, *, canon, requested_fields, intent):
        calls.append(len(enriched_all))
        return {"candidates": 0, "budget": 0, "escalated": 0, "succeeded": 0, "mean_confidence_delta": 0.0}

    monkeypatch.setattr(pf, "escalate_weak_records", fake_escalate)

    async def fake_enrich_multi(rec, canon, original_query=None, requested_fields=None, allow_single_fallback=True):
        return [{
            "url": rec.get("url"), "domain": "example.com",
            "clean_data": {"company_name": "Acme"},
            "extraction_confidence": 0.2, "relevance_to_query": 0.9,
            "score": 0.1, "raw_text": "Acme text",
        }]

    monkeypatch.setattr(pf, "enrich_records_with_multi_extraction", fake_enrich_multi)

    from pipeline import entity_classifier
    monkeypatch.setattr(entity_classifier, "classify_page_role", lambda rec: "entity_page")

    ranked = [{"url": "https://example.com/a", "raw_text": "Acme text", "title": "Acme"}]
    intent = {"intent_type": "find_companies", "message": "list companies", "_list_mode": "1"}

    await pf.run_extraction(ranked, intent)
    assert calls == [1]
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest agents/universal-agent/tasks/test_prefect_flows.py -v -k escalate_weak_records_before_shell_gate`
Expected: FAIL — `escalate_weak_records` not called (`calls == []`), and/or `AttributeError` since `tasks.prefect_flows` doesn't import/expose it yet.

- [ ] **Step 3: Wire it in**

In `tasks/prefect_flows.py`, add the import near the top of `run_extraction` (alongside its existing local imports, e.g. right after the `from pipeline.entity_classifier import classify_page_role, extract_entities_from_container` line):

```python
    from pipeline.vision_escalation import escalate_weak_records
```

Then insert, immediately after the score-blend loop (after the line `item["score"] = round(0.5 * base + 0.3 * float(conf) + 0.2 * float(rel), 4)`) and before the `# Drop "shell" rows` comment:

```python
    # Reactive vision escalation for weak records — must run BEFORE the shell-row
    # gate below, which would otherwise delete exactly the records this rescues.
    # See docs/superpowers/specs/2026-07-30-adaptive-vision-escalation-design.md.
    if not single_entity and not aggregate and enriched_all:
        escalation_metrics = await escalate_weak_records(
            enriched_all, canon=canon, requested_fields=requested_fields or [], intent=intent,
        )
        if intent.get("session_id") and any(escalation_metrics.get(k) for k in ("candidates", "escalated")):
            from tasks.prefect_flows import publish_metrics
            publish_metrics(intent["session_id"], **{f"vision_escalation_{k}": v for k, v in escalation_metrics.items()})
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest agents/universal-agent/tasks/test_prefect_flows.py -v -k escalate_weak_records_before_shell_gate`
Expected: 1 passed

- [ ] **Step 5: Run the full existing `test_prefect_flows.py` suite for regressions**

Run: `pytest agents/universal-agent/tasks/test_prefect_flows.py -v`
Expected: all passed (aside from any pre-existing unrelated failures already present before this change — compare against a baseline run if any show up)

- [ ] **Step 6: Commit**

```bash
git add agents/universal-agent/tasks/prefect_flows.py agents/universal-agent/tasks/test_prefect_flows.py
git commit -m "feat: wire adaptive vision escalation into run_extraction before the shell-row gate"
```

---

### Task 6: Full regression pass + Docker/build verification

- [ ] **Step 1: Run every test file touched or exercised by this feature**

Run (from repo root):
```
pytest agents/universal-agent/scraper/anti_ban/test_vision_extract.py agents/universal-agent/orchestration/tools/test_builtin.py agents/universal-agent/pipeline/test_vision_escalation.py agents/universal-agent/tasks/test_prefect_flows.py agents/universal-agent/pipeline/test_entity_classifier.py agents/universal-agent/agents_scrapper/sub_agents/ agents/universal-agent/orchestration/test_scraper_orchestrator.py -q
```
Expected: all passed.

- [ ] **Step 2: Run the full repo test suite once, to catch anything outside the touched-file list**

Run: `pytest -q` (from repo root)
Expected: no new failures relative to the pre-change baseline (some pre-existing unrelated failures/skips may already exist in this repo — compare, don't assume every red is caused by this change).

- [ ] **Step 3: Syntax/import sanity check on every modified file**

Run: `python -m py_compile agents/universal-agent/scraper/anti_ban/vision_extract.py agents/universal-agent/orchestration/tools/builtin.py agents/universal-agent/config/settings.py agents/universal-agent/pipeline/vision_escalation.py agents/universal-agent/tasks/prefect_flows.py`
Expected: no output, exit 0.

- [ ] **Step 4: Docker build check**

Run: `docker compose build` (or `docker build .` per this repo's `Dockerfile`, matching whichever the project's existing CI/dev workflow uses — check `docker-compose.yml`/`Dockerfile` at repo root first if unsure)
Expected: build succeeds — this change adds no new Python dependencies (no new `requirements.txt` entries), so a build failure here would indicate an unrelated pre-existing issue, not something this feature introduces; investigate before assuming it's caused by this work.

- [ ] **Step 5: Confirm no other agent's behavior changed unexpectedly**

Spot-check: `agents_scrapper/sub_agents/research.py`'s SPEC (the one agent explicitly out of scope for vision mechanisms) is untouched — `git diff --stat` should show no changes to that file or to any `DomainSpec` fields (this feature adds no new `DomainSpec` fields at all, unlike the prior vision-scraping-fallback spec).

- [ ] **Step 6: Update this plan's checkboxes**

Mark every completed task/step above with `- [x]` so this plan's bookkeeping reflects reality (the gap in the prior spec's plan — noted in the design doc's Decision Reversal section — is exactly what this step avoids repeating).
