# Vision-LLM Screenshot Extraction for Scraper Agents

## Context

This repo's scraping pipeline (`agents/universal-agent`) is 100% DOM/HTML based:
Scrapy + Playwright for rendering, FlareSolverr for anti-bot, plain-text LLM
extraction (`brain/extractor.py`, `gpt-5-nano` tier) on the cleaned page text.

Investigating a stock_market bug this session (a sector-listing table on
dhan.co rendering as garbled per-company profiles instead of a clean table)
surfaced two real gaps:

1. Text extraction can misread structured, visually-laid-out pages — a
   dhan.co per-row "Analyze with 🧡 fuzz AI" CTA button got folded into
   scraped company names as junk data (fixed separately this session with a
   regex filter, but the root cause — reading a visual table as flat text —
   remains).
2. Certain sources are known-good for a given agent (dhan.co/screener.in for
   Indian stocks, LinkedIn/Naukri/Wellfound/Indeed for candidates, a
   company's own homepage for lead-gen enrichment) but the current pipeline
   has no way to say "always read this one more carefully."

This spec adds vision-LLM (screenshot → multimodal chat model) extraction as
an *additional* read path — reused proactively for known-good sources, not
as a reactive retry-on-failure fallback (that was considered and rejected:
a reactive fallback wouldn't reliably fire on sources we already trust).

## Two mechanisms

### Mechanism 1 — static trusted-domain routing

New field: `DomainSpec.vision_priority_domains: list[str] = []`
(`agents_scrapper/sub_agents/models.py`).

At the point the executor resolves a URL to fetch (`orchestration/tools/
builtin.py`), check its domain (`urlparse(url).netloc`, suffix match)
against `spec.vision_priority_domains`. A match routes that URL through the
vision fetch/extract path instead of the normal text pipeline — this
*replaces* the fetch for that URL, it is not an additional round-trip.
No match → completely unchanged behavior.

| Agent | `vision_priority_domains` |
|---|---|
| stock_market | `dhan.co`, `screener.in` |
| recruitment | `linkedin.com`, `naukri.com`, `wellfound.com`, `indeed.com` |
| social_trends | `trends24.in` |
| lead_generation | *(none — see Mechanism 2)* |
| market | *(none via this mechanism — see Mechanism 2)* |
| research | *(none — open-domain by design, no fixed trusted set fits)* |

`social_trends.py` is included here overriding a standing note from a prior
session ("internal-only, never edit even to match a fix pattern") — the
user explicitly confirmed this override for this feature.

### Mechanism 2 — "visit the entity's own homepage," generalized

A company's own official site isn't a fixed domain (it's different per
company), so it can't be a `vision_priority_domains` entry. This needs a
dynamic "go visit whatever this entity's own site is" pass instead — which
already exists for one agent, and gets reused/extended for another:

- **lead_generation**: already has this pass — `_harvest_official_sites` in
  `agents_scrapper/sub_agents/field_enrichment.py` ("Pass 3"), called via
  `base.py`'s `_field_enrich` hook (fires when `spec.report_mode ==
  "table"`, true for lead_generation today). It currently fetches each
  company's homepage with plain `httpx` and regexes the LinkedIn slug,
  mailto email, and careers link out of the raw HTML. Upgrade: fetch via
  the vision path instead of `httpx`, keep the same three harvested fields.
  Explicitly does **not** add LinkedIn as a search target (user: "linkedin
  will cause errors") — only as data occasionally printed on a company's
  own page, same as today.
- **market**: has no equivalent pass today (`report_mode="topical"`, so
  `_field_enrich`'s gate never fires for it). New work: add an opt-in
  `DomainSpec.enable_field_enrichment: bool = False` flag so `_field_enrich`
  can run for a non-"table" agent without changing its report style, then
  give `market` a homepage-visit call through the same generalized vision
  helper, targeting *market's own* fields (not lead-gen's linkedin/email/
  careers set) — whatever `market`'s own extraction fields call for
  (product positioning, pricing signals, key offerings, etc.). This is the
  most speculative, newest-plumbing piece of this spec.

## Shared technical flow (both mechanisms)

1. Fetch via the existing Camoufox path (`scraper/anti_ban/
   browser_evader.py`, `fetch_with_camoufox`) — already holds a live
   Playwright `page` right where it currently calls `page.content()`.
2. Capture `page.screenshot()` at that same point.
3. Base64-encode, send to a vision-capable chat model via the existing
   `image_url` content-block pattern already used in `agents/extraction/
   image_ocr.py` (same LLM client helper — no new plumbing there).
4. Prompt with the relevant field list: the agent's `extraction_field_groups`
   for Mechanism 1, or the specific wanted-fields param for Mechanism 2.
5. Merge the result into `ctx`/`records` in the same shape a normal
   `extract_details` result takes, tagged with its source URL/domain —
   zero changes needed to `_field_lookup`, `_compile_table`, dedup, etc.

## Error handling

If the Camoufox fetch or vision call fails (site down, blocked, malformed
response), fall back to the normal text pipeline for that URL rather than
dropping it — best-effort, matching this codebase's existing `except
Exception` conventions throughout the scrape pipeline. LinkedIn specifically
has heavy anti-bot defenses and may still get blocked even via Camoufox —
a known risk, not a blocker.

## Cost / model selection

No hard cap on vision calls (explicit user decision — trust the agentic
loop's existing judgment, consistent with how other tool-call budgets in
this codebase work). Volume is naturally bounded: priority domains are a
short, fixed list per agent, and Mechanism 2 fires once per discovered
entity, not per arbitrary retry. The extraction tier used today
(`gpt-5-nano`) is text-only — vision calls need an explicitly-selected
multimodal model.

## Testing

- Domain-matching (`vision_priority_domains` lookup) is a pure function —
  gets a direct unit test.
- Fetch+vision-call integration gets a mocked test following the existing
  style in `test_research_engine.py` / `test_scraper_orchestrator.py`
  (mock the Camoufox fetch and the LLM client) — no real network/API calls
  in CI, consistent with how the rest of the scrape pipeline is tested
  here.
- `market`'s new `enable_field_enrichment` flag gets the same
  default-off-preserves-existing-behavior verification pattern used for
  `task_templates_requires_list_mode` earlier this session (confirm other
  agents' `_field_enrich` gating is unaffected).

## Out of scope

- `research` agent (open-domain, no fixed trusted set fits its design).
- Any reactive "vision on confidence-failure" fallback — explicitly
  considered and rejected in favor of proactive trusted-source routing.
  **Superseded** by `2026-07-30-adaptive-vision-escalation-design.md`: this
  was correct reasoning under a proactive-only architecture, but new
  evidence (an unknown domain producing a confident-looking wrong DOM
  extraction — see that spec's Decision Reversal section) showed the
  proactive allowlist can't cover the unknown long tail. The two mechanisms
  are complementary in the superseding spec, not mutually exclusive.
- A hard cost cap on vision calls (explicit user decision). **Superseded**
  by the same spec: true when volume was bounded by a short fixed allowlist;
  false once reactive escalation expands the eligible population to any
  weak record in a run, which is why that spec introduces an adaptive cap.
