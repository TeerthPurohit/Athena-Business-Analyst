# BA OS — Product Specification: 08 Developer Notes

← [07-keyboard-and-responsive](07-keyboard-and-responsive.md) · [00-overview](00-overview.md) →

This spec is the canonical UX/IA reference for the eventual full product. It is **not** an
implementation plan — treat each unchecked area below as its own future
brainstorm → plan → build cycle (per this repo's `superpowers:brainstorming` process).

## What's real today vs. placeholder-only

Real endpoints today (`agents/business_analyst/api/routes.py`), all already wired in
`ba-frontend/app.js`:

| Endpoint | Screens it backs |
|---|---|
| `POST/GET /api/ba/projects`, `PATCH /projects/{id}` | Projects grid (partial), Settings |
| `POST /projects/{id}/summary/regenerate` | Overview (partial) |
| `GET /projects/{id}/facts`, `POST /facts/{id}/approve` | Knowledge Graph's Evidence data, History (partial) |
| `GET /projects/{id}/deliverables`, `POST .../{key}/generate`, `POST .../{instance_id}/approve` | Deliverables |
| `POST /projects/{id}/runs`, `GET .../runs/{run_id}` | (not in this spec's IA yet — session-local today) |
| `GET /clarifications/next`, `POST /clarifications/{gap_key}/answer`, `WS /clarifications/stream` | Chat |
| `POST /planner/semantic` | Chat free-text routing (referenced in 05, not yet wired client-side) |
| Node `POST /auth/login` | Login |

Everything else in [02-screens.md](02-screens.md) marked **placeholder** has no backend endpoint:
Sources, Knowledge Graph (as nodes/edges), Requirements/User Stories/Workflows/APIs/Database/
Screens/Business Rules/Risks (all graph-projection views), Review Center (aggregate queue),
History (unified audit log), Global Search, project-level status/progress/knowledge-score rollups,
`tool_call`/`fact_created` streaming events, and comments.

## Suggested phasing (for future sessions, not decided/committed here)

1. **Today's restyle** — apply [01-design-system.md](01-design-system.md) to the existing 5-tab
   `ba-frontend` shell (Chat/Facts/Deliverables/Runs/Summary) with zero new backend work. This is
   the only slice this spec's originating session also implements.
2. **Graph read model** — a single new endpoint that projects the fact table into nodes/edges
   would unlock Knowledge Graph, Requirements, User Stories, Workflows, APIs, Database, Screens,
   and Business Rules simultaneously (they're all the same read model, different filters/renders —
   see [00-overview.md § IA](00-overview.md#information-architecture)). Highest-leverage next slice.
2b. Sources upload/listing, Review Center aggregation, and History are each independently buildable
   after (1), in any order, once there's a stakeholder pulling on them.
3. **Streaming enrichment** (`tool_call`, `fact_created`) — nice-to-have polish on top of an
   already-working Chat; not a blocker for anything else.

## Constraints carried from the codebase

- Fact store is append-only (`agents/business_analyst/CLAUDE.md`) — no screen in this spec ever
  presents in-place editing of a fact; "edit" always means "propose a superseding fact."
- Deliverable content is only returned at generation time today — Deliverables' Preview/Download/
  Version History in [02-screens.md](02-screens.md) assumes a future content-retention endpoint;
  until then, Preview falls back to "regenerate to view latest," matching current `app.js` behavior.
- No literal draggable-window "OS" chrome (existing `ba-frontend/README.md` decision, reaffirmed
  here) — the "Operating System" in the product name is a metaphor for scope, not literal desktop
  UI.
