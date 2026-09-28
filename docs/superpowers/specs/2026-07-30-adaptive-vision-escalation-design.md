# Adaptive Vision Escalation for Extraction Quality

**Supersedes (partially):** `2026-07-30-vision-scraping-fallback-design.md` — see
"Decision Reversal" below. That spec's static `vision_priority_domains` /
`enable_field_enrichment` mechanisms are unaffected and remain in place; this
spec adds a second, reactive mechanism alongside them.

## Context

The static mechanism from the prior spec (`vision_priority_domains`) routes a
short, fixed per-agent domain list through vision unconditionally. It caught
dhan.co/screener.in (known DOM-hostile) but has no way to react to a domain
*not* on that list also failing.

Investigating a stock_market run this session surfaced exactly that case:
`nseindia.com/index-tracker/NIFTY%20IT` returned raw_text that was real (not
empty) but was sitewide boilerplate — the extractor's own title field came
back as NSE's generic homepage `<title>`, not tracker-specific content. The
LLM correctly self-reported `extraction_confidence=0.12`, and the record was
then deleted entirely by the existing shell-row gate (`run_extraction`,
`tasks/prefect_flows.py`, requires ≥2 populated content fields) before it
ever had a chance at a second read.

Two other real gaps, independent of NSE, motivate the same mechanism:
`raw_html`/table-tag extraction can legitimately come back empty on a
div-grid-rendered page (see the `KEEP_RAW_HTML` fix landed earlier this
session), and any container/listing page can be classified correctly but
still extract poorly for reasons specific to that page, not that domain.

## Decision Reversal

This is an **intentional supersession, not a silent edit or a claim that the
previous spec was wrong.** The previous spec was correct for the
architecture that existed at the time — a proactive-only allowlist, where
"reactive vs. proactive" was a binary choice and volume was bounded by
construction (a short fixed domain list). This spec expands the
architecture to include reactive escalation *alongside* the proactive path,
which changes the cost model and introduces a failure mode (unbounded
vision volume) that did not previously exist. Two decisions from the prior
spec's **Out of scope** section are superseded as a direct consequence:

**1. Reactive escalation**

- *Previous rationale:* Proactive trusted-source routing already covers
  known-problematic domains; a reactive fallback was rejected because *"it
  wouldn't reliably fire on sources we already trust"* — an argument for
  keeping the proactive path, framed against a binary either/or choice.
- *New evidence:* An **unknown** (non-allowlisted) domain can still produce
  a high-confidence-*looking*, DOM-based extraction that is actually wrong —
  the nseindia.com case: real (non-empty) raw_text, but sitewide boilerplate,
  correctly self-scored 0.12 by the extractor, then deleted by the shell
  gate before any second read was possible. The proactive allowlist cannot
  catch this by design (the domain was never flagged in advance). The two
  mechanisms are complementary, not competing: allowlist for known-hostile
  domains, reactive escalation for the unknown long tail.

**2. Hard cap on vision calls**

- *Previous rationale:* *"Volume is naturally bounded: priority domains are
  a short, fixed list per agent."* True under a proactive-only architecture.
- *New evidence:* Reactive escalation expands the eligible population from
  a fixed allowlist to **any weak record in the run** — a 100-page run could
  legitimately produce 100 screenshots + 100 vision calls with no cap. The
  cap doesn't contradict the old reasoning; it addresses a new problem the
  expanded scope introduces. An adaptive cap (see Budget below) is the
  right-sized fix, not a blanket cost objection.

Everything else in the prior spec (the two static mechanisms, `DomainSpec`
fields, `vision_extract.py`'s existence) stands unchanged. This spec extends
`vision_extract.py`'s return shape (breaking change, see below) — both
mechanisms share the new shape after this lands. The prior spec document is
kept as-is for historical traceability, with a pointer added at its two
superseded bullets rather than rewritten.

**On the prior implementation plan's unchecked tasks:** its checkboxes
(`docs/superpowers/plans/2026-07-30-vision-scraping-fallback.md`) are all
still `- [ ]` even though the code they describe is visibly implemented in
the repo today — the plan's bookkeeping was simply never updated, which is
an implementation-tracking gap, not an architectural question. Not
addressed here to avoid mixing implementation bookkeeping into an
architecture-decision document; this spec's own plan (produced by
writing-plans, next step) is the authoritative implementation target going
forward regardless of that plan's checkbox state.

## Where it hooks in

New module `pipeline/vision_escalation.py`, called from `run_extraction`
(`tasks/prefect_flows.py`) immediately after the extraction wave + score
blend (~line 1570) and **before** the shell-row drop gate (~line 1580),
dedup, and the relevance gate. This ordering is load-bearing: the shell gate
is what deletes exactly the class of record (title-only, <2 populated
fields) this mechanism exists to rescue. Escalating after any of those three
gates means the candidates are already gone.

A separate module rather than inline in `prefect_flows.py` (already ~2,900
lines) because the pass is pure enough (given an extracted-records list) to
unit-test in isolation from the rest of `run_extraction`.

## Vision return shape (breaking change)

Current (`vision_extract.py`, from the prior spec): a flat
`{field: value}` dict with no confidence signal at all.

New — **always a dict, never bare `None`** (a change from the prior spec,
where any failure collapsed to `None`). A `status` key on every outcome,
because the reactive path needs to distinguish *why* a call didn't succeed,
not just that it didn't:

```json
{
  "status": "success",
  "fields": {
    "company_name": {"value": "TCS", "confidence": 0.99},
    "stock_price": {"value": "4120.50", "confidence": 0.95}
  },
  "page_confidence": 0.91,
  "relevance_to_query": 0.95,
  "reasoning": null,
  "metadata": {
    "model": "gpt-4o",
    "latency_ms": 2100,
    "response_schema_version": "v1",
    "escalation_reason": "LOW_CONFIDENCE"
  }
}
```

Failure outcomes (both still "never raises" — the function's existing
contract — just no longer collapsed to one undifferentiated signal):

```json
{"status": "screenshot_failed", "error": "camoufox launch timeout"}
{"status": "extraction_failed", "error": "LLM call timed out"}
```

`vision_extract.py` already has two separate `try/except` blocks internally
— one around `_screenshot_via_camoufox`, one around the LLM call/JSON parse
— so this is surfacing a distinction the code already makes, not inventing
a new failure-detection mechanism:

- **`screenshot_failed`** — the browser/domain-level fetch itself failed
  (Camoufox launch, navigation, timeout). Real evidence the domain is
  vision-hostile too — this is what the reactive path uses to populate
  `intent["_vision_failed_domains"]` (see Termination Guarantees).
- **`extraction_failed`** — the screenshot succeeded but the LLM call or
  response parsing failed (timeout, malformed JSON, API error). Transient
  and infrastructure-side, **not** evidence about the domain — must **not**
  trigger domain-memory blacklisting, or a temporary vision-provider outage
  would wrongly poison every domain touched during it.

- `reasoning` is **optional** (`null` unless confidence is low, extraction is
  ambiguous, or validation fails) — populating it unconditionally adds tokens
  and latency for no operational value on the common high-confidence case.
- `metadata.escalation_reason` is `null` for the static (`vision_priority_domains`)
  call path (there was no escalation decision to record) and one of
  `LOW_CONFIDENCE` / `MISSING_FIELDS` / `BOTH` for the reactive path.
- `metadata.response_schema_version` (not `vision_version` — a model-version
  string would be ambiguous with this contract's own version, which evolves
  independently of which vision model serves the call).
- This shape change touches **both** callers of `vision_fetch_and_extract`:
  the static-domain path in `orchestration/tools/builtin.py` (which currently
  hardcodes `extraction_confidence: 0.95` on every success — replaced with
  the real computed `page_confidence`) and the new reactive path below. The
  hardcoded 0.95 was already a known-fake number; this is a strict accuracy
  improvement to existing behavior, not just new-code plumbing. That static
  caller doesn't need the failure-mode distinction (it has no domain-memory
  concept) — it can keep treating anything other than `status == "success"`
  as failure, matching its existing behavior unchanged.

## Confidence contract

Three distinct terms, used consistently everywhere downstream (this
disambiguation is itself a deliverable — the point is that no module invents
its own meaning for "confidence"):

- **`page_confidence`** — the vision model's own self-reported confidence for
  the whole page read. Analogous to `ExtractedRecord.extraction_confidence`
  from the text extractor (`brain/extractor.py`), same 0.0–1.0 scale.
- **`field_confidence`** — per-field, inside `fields.<name>.confidence`. Not
  currently consumed by anything outside this pass (recorded for future use /
  debugging), but present in the contract now rather than added later.
- **`final_confidence`** — the blended, post-validation score computed by
  this module (formula below) for an *escalated* record. This is the number
  that replaces the record's `extraction_confidence` for scoring/gating
  purposes after escalation. Never confused with `page_confidence`: a page
  can self-report high confidence and still get a low `final_confidence` if
  it fails grounding/agreement checks.

## The escalation pass

```python
async def escalate_weak_records(
    enriched_all: list[dict], *, canon: str, requested_fields: list[str],
    intent: dict,  # read/write intent["_vision_failed_domains"] in place — see Termination Guarantees
) -> dict:  # returns run metrics
```

**Eligibility** — a record is a candidate iff:
- (`extraction_confidence < VISION_ESCALATION_CONFIDENCE_MAX` **OR**
  populated-field count via existing `count_populated_fields()` <
  `VISION_ESCALATION_MIN_FIELDS`), **AND**
- `extraction_confidence <= VISION_ESCALATION_SKIP_ABOVE` (never escalate an
  already-strong record — the explicit "don't touch >90%" rule), **AND**
- has a `url`, **AND**
- not already `_vision_extracted` (static path already tried vision) or
  `_vision_escalated` (**this** path already tried vision for this record in
  an earlier round of the same turn — `run_extraction` is called once per
  executor round, and a record's `_vision_escalated` flag persists on it
  across rounds via `ctx.evidence`/`ctx.ranked`, so this is what actually
  bounds retries to exactly one attempt per record for the record's entire
  lifetime in the run) or `_name_seeded` (synthetic roster row, no page to
  screenshot), **AND**
- domain not in `intent["_vision_failed_domains"]` (scoped to the run — see
  Termination Guarantees).

**Priority** (highest first, budget spent top-down):

```python
priority = (
    weights["relevance"] * relevance_to_query          # already on the record (extractor output)
    + weights["missing_fields"] * missing_field_ratio  # 1 - populated / max(1, len(requested_fields))
    + weights["low_confidence"] * (1 - extraction_confidence)
    + weights["domain"] * domain_importance            # normalized base_score (pre-extraction URLPrioritiser rank)
)
```

Weights live in settings as a dict, not hardcoded constants (explicit
requirement — these are tuning knobs, not architecture). Must sum to `1.0`
— validated at settings-load time (raise a clear config error, not a
silently out-of-range priority score):

```python
VISION_ESCALATION_PRIORITY_WEIGHTS = {
    "relevance": 0.40, "missing_fields": 0.30,
    "low_confidence": 0.20, "domain": 0.10,
}
```

**Tie-breaking** — deterministic, so results don't vary across runs/Python
versions on near-equal scores. Sort candidates by the tuple
`(-priority, -base_score, -missing_field_ratio)`; Python's sort is stable,
so an exact tie on all three falls back to original candidate order (the
order records appear in `enriched_all`) as the final, deterministic
tiebreak — never left to whatever the sort implementation happens to do.

**Budget** — bounds total vision *attempts* this run, unconditionally,
regardless of outcome:

```python
budget = min(
    settings.VISION_ESCALATION_MAX_PER_RUN,
    max(3, int(len(enriched_all) * settings.VISION_ESCALATION_BUDGET_RATIO)),
)
```

Defaults: `MAX_PER_RUN=10` (validated `>= 1` at settings-load time — `0`
would silently disable escalation without the intent being obvious; use
`VISION_ESCALATION_ENABLED=False` to actually disable it), `BUDGET_RATIO=0.1`
(10 pages → 3, 50 → 5, 100 → 10, 500 → capped at 10).

**Why attempts, not successes, consume budget** — considered and rejected:
"only consume budget on a successful escalation" (so failures are 'free').
That reopens exactly the risk the Termination Guarantees section exists to
close: if budget only decremented on success, a vision-API outage (every
call comes back `extraction_failed`) means budget never decrements, so the
pass keeps attempting *every* eligible candidate — potentially hundreds on
a large run — with nothing ever bounding it. Budget-as-attempts is the hard,
unconditional cost/latency cap, immune to that failure mode. What actually
prevents "N failures burning the whole budget for nothing" is domain memory
below: repeated failures **on the same domain** cost exactly one attempt,
not N, because the domain is blacklisted after its first `screenshot_failed`.
If the failures are on N genuinely *different* domains, spending N attempts
to learn that is the real, unavoidable cost budget is meant to bound — not
a bug to engineer around.

## Final confidence formula

All five terms are computable from data that exists at this exact pipeline
stage — this was verified deliberately, after an earlier draft of this
formula included two terms (raw cross-*source* agreement, a separate OCR
pass) that don't exist until a later pipeline stage or don't exist at all in
this codebase. A formula with unmeasurable terms produces fake precision,
which is worse than a smaller honest one.

```python
final_confidence = (
    weights["page_confidence"] * page_confidence
    + weights["schema_validation"] * schema_validation
    + weights["dom_vision_agreement"] * dom_vision_agreement
    + weights["text_grounding"] * text_grounding
    + weights["domain_reputation"] * domain_reputation
)
# defaults: 0.50 / 0.20 / 0.15 / 0.10 / 0.05 — VISION_ESCALATION_CONFIDENCE_WEIGHTS in settings
```

Same rule as the priority weights above: `VISION_ESCALATION_CONFIDENCE_WEIGHTS`
must sum to `1.0`, validated at settings-load time. Without that check, e.g.
five weights of `0.5` each would silently produce a `final_confidence` above
`1.0` — validation catches a misconfiguration, not a runtime formula bug.

- **`page_confidence`** — vision's self-report, as above.
- **`schema_validation`** — fraction of `fields` whose value passes a basic
  type/format check for that field name. Concretely:
  `pipeline.enricher.traverse_and_normalise(value, field_name)` already
  dispatches by keyword match on `field_name` (`price`/`cost` →
  `normalise_price`, `date`/`time`/`published` → `normalise_date`,
  `url`/`link`/`website` → `normalise_url`, `email`/`mail` →
  `normalise_email`, `phone`/`telephone`/`mobile` → `normalise_phone`, etc.)
  — a field whose name matches one of these classes passes validation iff
  the underlying normalizer returns non-`None` (i.e., the value actually
  parses as that type, not silently falls through unparsed). A field name
  matching none of these classes (free text like `company_name`) has no
  stricter schema to fail, so it passes iff non-empty. No new per-field
  validators invented — this reuses the exact dispatch table
  `traverse_and_normalise` already encodes.
- **`dom_vision_agreement`** — over fields present in **both** the original
  DOM `clean_data` and the vision `fields`: fraction that agree, using
  **per-field-type comparison**, not exact string match, and reusing what
  actually exists rather than a normalizer this codebase doesn't have:
  - Structured fields (price/number/date/email/phone/url) — run both values
    through the matching `pipeline/normalisers/*` function
    (`normalise_price`, `normalise_number`, `normalise_date`,
    `normalise_email`, `normalise_phone`, `normalise_url`, picked by field
    name) and compare the normalized results. `normalise_price("₹1,25,000")`
    and `normalise_price("125000 INR")` both resolve to `125000.0` — this is
    the mechanism, verified against the codebase's real implementation
    (locale-aware via `babel.numbers`, not a bespoke regex).
  - Free-text name-like fields (company_name, entity, title, ...) —
    `pipeline.deduplicator.fuzzy_match(a, b, threshold=90.0)` (already used
    for cross-page entity consolidation; `rapidfuzz.fuzz.token_sort_ratio`
    under the hood). This correctly agrees "Tata Consultancy Services" with
    "Tata Consultancy Services Ltd." (shared tokens, minor suffix delta).
    **It does not, and is not claimed to,** agree a bare ticker against a
    full name ("TCS" vs "Tata Consultancy Services" share no tokens) — an
    earlier draft of this spec incorrectly implied a normalizer handles
    that case; no such normalizer exists in this codebase, and inventing
    one is out of scope here (semantic/embedding-based matching, as
    `pipeline/deduplicator.py`'s own dedup fallback uses via
    `batch_get_embeddings`/`cosine_similarity`, would be the honest way to
    close that gap — not attempted in this spec, since it's a new
    dependency on the embedding pipeline for one narrow signal).
  - `1.0` (vacuously true) if there is no field-overlap to compare — this is
    a bonus signal, not a penalty for a DOM read that returned nothing.
- **`text_grounding`** — over fields **vision filled that DOM did not**:
  fraction whose value, run through the same per-field-type normalizer
  above, is found in the page's own `raw_text` (also normalized the same
  way before the substring check) — `₹1,25,000` grounds against
  `125000 INR` because both normalize to `125000.0` via `normalise_price`,
  not via a new fuzzy-text algorithm. This is the direct hallucination
  check: a misread value (the "OpenAI" → "OpenAI1" case) won't appear in
  the source text. `1.0` if vision filled nothing DOM didn't already have.
- **`domain_reputation`** — normalized `base_score` (the pre-extraction
  `URLPrioritiser` rank), same signal `priority`'s `domain_importance` term
  uses — reused, not reinvented.

## Merge precedence

Explicit, per-field, so no implementation detail is left to improvise:

1. DOM value present **and** `extraction_confidence >= VISION_ESCALATION_CONFIDENCE_MAX` → keep DOM. (Shouldn't occur given eligibility gating, but stated for completeness/defense-in-depth.)
2. DOM value missing/null for this field → use vision's value (if vision provided one).
3. DOM confidence low **and** vision's `final_confidence` is higher by more than `VISION_ESCALATION_REPLACE_MARGIN` (default `0.15`) **and** that field passes `schema_validation` → replace DOM's value with vision's.
4. Otherwise → keep DOM's existing value.

After a **successful** vision read, merged per the rules above: record's
`extraction_confidence` is set to `final_confidence` (the blended score, not
`page_confidence` alone), `_vision_escalated = True`, `_escalation_reason`
set to whichever eligibility rule fired.

After a **failed** attempt (`status` is `screenshot_failed` or
`extraction_failed`): no merge happens (there is no vision data to merge),
but `_vision_escalated = True` is still set on the record — this is what
the Termination Guarantees section's "exactly once, structurally" claim
depends on. Without this, a record whose vision attempt merely failed
would remain eligible and could be re-attempted on a later executor round,
silently reopening the retry loop this design specifically eliminates.

## Settings

```python
VISION_ESCALATION_ENABLED: bool = True
VISION_ESCALATION_CONFIDENCE_MAX: float = 0.5      # matches existing low-confidence warning threshold
VISION_ESCALATION_SKIP_ABOVE: float = 0.9
VISION_ESCALATION_MIN_FIELDS: int = 2              # matches existing shell-gate threshold
VISION_ESCALATION_MAX_PER_RUN: int = 10
VISION_ESCALATION_BUDGET_RATIO: float = 0.1
VISION_ESCALATION_REPLACE_MARGIN: float = 0.15
VISION_ESCALATION_PRIORITY_WEIGHTS: dict = {"relevance": 0.40, "missing_fields": 0.30, "low_confidence": 0.20, "domain": 0.10}
VISION_ESCALATION_CONFIDENCE_WEIGHTS: dict = {"page_confidence": 0.50, "schema_validation": 0.20, "dom_vision_agreement": 0.15, "text_grounding": 0.10, "domain_reputation": 0.05}
```

**Validated at settings-load time** (raise, don't silently misbehave):
`VISION_ESCALATION_MAX_PER_RUN >= 1` (use `VISION_ESCALATION_ENABLED = False`
to disable, not `MAX_PER_RUN = 0`); `sum(VISION_ESCALATION_PRIORITY_WEIGHTS.values()) == 1.0`;
`sum(VISION_ESCALATION_CONFIDENCE_WEIGHTS.values()) == 1.0` (within a small
float tolerance, e.g. `1e-6`).

## Metrics

Returns `{candidates, budget, escalated, succeeded, mean_confidence_delta}`
from `escalate_weak_records`, pushed through the existing `publish_metrics`
path (`tasks/prefect_flows.py`) — rides the `metrics` StreamChunk already
documented in `docs/BRAIN_STREAM_STEP_TREE_GUIDE.md` and
`docs/AGENT_STREAMING_INTEGRATION_GUIDE.md`, no new wire-format work needed.

## Termination Guarantees

The escalation pipeline must be provably finite **without relying on
wall-clock time** — bounded work, not bounded time, since wall-clock varies
with hardware/network/LLM latency and a timeout is a symptom-level guard,
not a structural one. (Per-call network timeouts already exist elsewhere in
this codebase — `EXTRACTOR_TIMEOUT_SECONDS`, `simulate_human_browsing`'s
`max_seconds`, `AGENT_SCRAPE_TIMEOUT` — those are orthogonal per-request
guards at a different layer and out of scope here; this section is about
the escalation *pass's own* control flow, which introduces no new timeout.)
Achieved entirely through finite, monotonically-shrinking resources:

- **Finite candidate set** — `enriched_all` is already bounded upstream
  (`page_budget`/`list_budget` in `run_extraction`); escalation adds no new
  unbounded input.
- **Finite adaptive vision budget** — `VISION_ESCALATION_MAX_PER_RUN`,
  consumed once per **attempted** record regardless of outcome (see "Why
  attempts, not successes" above — this is the unconditional cost/latency
  cap, immune to an all-failures scenario), **never replenished** within a
  run. Budget strictly decreases call-to-call; there is no code path that
  adds to it.
- **Finite retry count per record: exactly 1, structurally** — not a
  counted `_vision_attempts` field with a `MAX_VISION_RETRIES` check, but
  stronger: the `_vision_escalated`/`_vision_extracted` exclusion in
  Eligibility means a record is **never** reconsidered once escalated,
  ever, regardless of how many further executor rounds run — including on
  a `screenshot_failed`/`extraction_failed` outcome; a failed attempt still
  marks the record attempted, it just doesn't improve it. There is no
  retry loop to bound in the first place. This design eliminates retries
  rather than bounding them, which is why "stop retrying once no progress"
  logic isn't needed here — there's nothing to stop.
- **No re-escalation of already-escalated records** — same mechanism as
  above.
- **Domain memory, scoped to the run** — `run_extraction(ranked, intent)`
  takes no `ctx`, so a plain local variable wouldn't survive across the
  executor's multiple rounds (`run_extraction` is called fresh each round).
  This function already has an established convention for exactly this kind
  of cross-round scratch state: `intent["_entity_page_urls"]` and
  `intent["_container_entity_names"]` are mutated in place and persist
  because `intent` is the same object across rounds. `vision_failed_domains`
  follows the same convention: `intent["_vision_failed_domains"]` (a set,
  read/updated in place, defaulting to empty) rather than new plumbing. A
  domain is added **only** on `status == "screenshot_failed"` — the
  browser/domain-level failure mode, a real signal the domain is
  vision-hostile too (blocked, down, renders nothing). **Not** added on
  `status == "extraction_failed"` (LLM call/parse failure — transient,
  infrastructure-side; a vision-provider outage must not wrongly poison
  every domain touched during it) and **not** for a merely low
  `final_confidence` on a successful read (a successful-but-unconvincing
  read is still informative per-page, not evidence the whole domain is
  hostile). This is what actually prevents repeated failures on the *same*
  domain from draining the budget — the first `screenshot_failed` for a
  domain is the last attempt spent on it this run.
- **No recursive escalation paths** — `escalate_weak_records` is a terminal
  step within one `run_extraction` call; it does not itself invoke
  `scrape_urls`, DOM extraction, or anything that could re-enter this pass.

Net: the algorithm always terminates within a bounded number of vision
calls (≤ `VISION_ESCALATION_MAX_PER_RUN`) regardless of page complexity,
transient failures, or how many executor rounds the turn runs — with no
`MAX_EXECUTION_TIME` anywhere in this pass's own logic.

## Relationship to the static mechanism

Unchanged from the prior spec: `vision_priority_domains` remains the
proactive fast-path for the 3 known-DOM-hostile domains (skips a wasted DOM
round entirely for sources already proven bad). This spec's escalation pass
is the reactive net for everything else — a record from a
`vision_priority_domains` domain is never re-escalated (`_vision_extracted`
exclusion above), so the two mechanisms never double-spend budget on the
same page.

## Out of scope (this spec)

- Selector-cache enablement (`SELECTOR_CACHE_ENABLED`, currently `False`) —
  explicitly deferred to its own spec; different problem (DOM-side speed),
  different risk profile, would muddy this spec's testing surface.
- Persistent browser contexts, Browser Use / Crawl4AI / Trafilatura
  integration — separate sub-projects per the scope-decomposition discussion
  that preceded this spec.
- Any change to `run_extraction`'s existing single-entity path — escalation
  only applies to the list/aggregate flattened-records path, matching where
  the shell-gate problem actually occurs.

## Testing

Hermetic, vision call stubbed throughout:
- Priority ordering across a synthetic batch of records with known
  relevance/confidence/field-count/base_score combinations.
- **Tie-breaking**: two-plus records with identical `priority` (and, in a
  second case, identical `priority` *and* `base_score`) resolve in the
  documented deterministic order, not implementation-dependent sort order.
- Budget math at 10/50/100/500 records (verifies the `max(3, ratio)` and
  `MAX_PER_RUN` cap interact correctly at each size).
- **Budget consumption on failure**: a run where every escalation attempt
  returns `screenshot_failed`/`extraction_failed` still exhausts budget
  after exactly `budget` attempts (confirms attempts-not-successes
  accounting) — and, separately, a run where all failures share ONE domain
  confirms only one attempt is spent on it (domain memory kicking in),
  leaving the rest of the budget free for other candidates.
- Each eligibility rule in isolation (low confidence alone, missing fields
  alone, both, neither; `SKIP_ABOVE` boundary; `_vision_extracted` /
  `_name_seeded` exclusion).
- `final_confidence` formula: each term independently (agreement with
  normalization — "TCS" vs "Tata Consultancy Services Ltd." must agree;
  grounding with normalization — `₹1,25,000` vs `125000 INR` must ground).
- Merge precedence: all four rules, including the "DOM low + vision only
  marginally higher → keep DOM" case (margin not met).
- **Settings validation**: `VISION_ESCALATION_MAX_PER_RUN = 0` raises at
  load time; priority/confidence weight dicts that don't sum to `1.0` each
  raise at load time.
- `vision_extract.py`'s new return shape: both callers (static path in
  `builtin.py`, reactive path here) parse `status == "success"` correctly;
  malformed/partial vision responses (missing `page_confidence`, missing
  `fields`) degrade gracefully to a failure status rather than raising.
- **`screenshot_failed` vs `extraction_failed` distinction, directly**: a
  stubbed screenshot failure adds the domain to
  `intent["_vision_failed_domains"]`; a stubbed LLM/parse failure on the
  *same domain* does **not** — confirms only the browser/domain-level
  failure mode triggers domain memory, so a simulated vision-provider
  outage (all `extraction_failed`) never poisons any domain.
- **Termination guarantees, directly**: a record already carrying
  `_vision_escalated=True` (simulating a second executor round) is excluded
  from eligibility even with fresh low confidence/missing fields —
  confirms the exactly-once-per-record guarantee, not just budget
  exhaustion coincidentally stopping it. A domain present in
  `intent["_vision_failed_domains"]` is skipped even when it would
  otherwise be top-priority. Budget strictly decrements and is never
  observed to increase across repeated calls with a shared `intent` object.
