# BA OS — Product Specification: 05 AI Streaming Model

← [04-interaction-states](04-interaction-states.md) · [06-lifecycles](06-lifecycles.md) →

## Chat lifecycle

1. User opens Chat → WS connects → `status: thinking` → question or conflict streams in
   token-by-token → input enables once a `question` event carries a `gap_key`.
2. User answers → input disables → server processes → either next `question` streams, a `conflict`
   surfaces, or `done` closes the loop for now.
3. This is today's real mechanism (`WS /api/ba/projects/{id}/clarifications/stream`), reframed:
   the spec's Chat screen is the *same* gap-resolution loop, presented as an open conversation
   rather than a strictly modal Q&A — free-text messages outside of an active gap are accepted and
   routed to `/planner/semantic` or held client-side until a gap opens, rather than rejected. This
   is a real product-behavior gap between today's backend and this spec — see
   [08-developer-notes.md](08-developer-notes.md).

## Streaming event vocabulary (extends today's WS message types)

| Event | Meaning | UI effect |
|---|---|---|
| `status` (`stage: thinking`) | Model is working, no output yet | Typing-dots indicator |
| `token` | One chunk of assistant/conflict text | Append to open streaming bubble, no transition |
| `question` | A gap to resolve, stream finished | Close bubble, enable input, show meta (node type/field/score) |
| `conflict` / `conflict_done` | Contradictory facts found | Warning-styled bubble |
| `done` | No more open gaps | System line, disable input |
| `error` | Stream-level failure | Warning line, re-enable input if a gap was still open |
| `tool_call` *(new)* | AI invoked a deterministic tool (extract, search, generate) | Inline "Analyzing uploaded PDF…" / "Building facts…" style line, matches the brief's example sequence |
| `fact_created` *(new)* | A fact was written as a result of this turn | Live-increments the Chat header's Fact count without a full refetch |

`tool_call` and `fact_created` are **not implemented today** — the backend only emits
`status/token/question/conflict/conflict_done/done/error`. They're specified here because the
brief explicitly calls for visible tool execution ("Analyzing uploaded PDF... Extracting
requirements... Building facts... Updating graph...") — flagged as new backend work in
[08-developer-notes.md](08-developer-notes.md).

## Live knowledge counters

Chat header and Overview stat row (Knowledge %, Fact count, Requirement count, Source count) update
after every turn that changes the graph — driven by `fact_created` events where available, falling
back to a debounced refetch (max once per 3s) where they're not, so the UI never looks frozen even
before the new event type exists server-side.

## Never freeze

Every long-running action (deliverable generation, run execution, summary regeneration) shows
progressive status, not a blocking spinner: reuse the Multi-agent status panel
([03-components.md](03-components.md)) to name which stage is active. If a WS disconnects mid-turn,
reconnect silently once; on a second failure, show the existing "check Settings" warning pattern
already in `app.js`, not a raw stack trace.
