# BA OS — Product Specification: 02 Screens

← [01-design-system](01-design-system.md) · [03-components](03-components.md) →

Each screen entry: purpose, layout, key elements, and a **Data** line marking whether it's backed
by a real endpoint today or is a placeholder-state-only view (full mapping in
[08-developer-notes.md](08-developer-notes.md)).

## Landing page

**Purpose:** entry point before any project is selected. **Layout:** centered hero, max-width
640px. **Elements:** "Business Analyst OS" wordmark, subtitle ("Transform conversations, documents
and ideas into complete software specifications."), primary button *Create Project*, secondary
*Import Existing Project*, below the fold a *Recent Projects* row (max 4 cards, same card as the
Projects grid). **Data:** real (`GET /api/ba/projects`, take first 4 by `updated_at`).

## Projects grid

**Purpose:** all projects for the org. **Layout:** responsive card grid, 3 columns ≥1440px, 2
columns ≥960px, 1 column below. **Card contents:** name, one-line description/instructions
excerpt, status pill, progress bar (knowledge %), last-activity relative time, owner avatar,
knowledge score, open-gap count, requirement count, deliverable count. Clicking opens the project
workspace. **Empty state:** single centered *Create Project* prompt, no card grid chrome shown.
**Data:** real for name/updated_at/instructions; status, progress, knowledge score, gap/requirement
counts are **placeholder** (backend has no project-level rollup endpoint yet — see dev notes).

## Project workspace shell

**Purpose:** the container every project-scoped screen lives inside. **Layout:** three columns —
left nav (`00-overview.md § Navigation tree`), center content (screen-specific), right context
panel (Project Memory, see [03-components.md](03-components.md)). Header row above center content:
project name (editable inline on click), org badge, primary action button (context-sensitive: "Ask
AI" on most screens, "Generate" on Deliverables). **Default screen on open: Chat** — not Knowledge
Graph, not Overview — matching the journey in [00-overview.md](00-overview.md). Right panel is
collapsible; state persists per-user in `localStorage`, not server-side.

## Overview

**Purpose:** at-a-glance project health, the "did anything need my attention" screen. **Layout:**
top stat row (Knowledge %, Fact count, Requirement count, Open gaps, Open risks), below it two
columns: left = Recent Activity feed (facts approved, deliverables generated, runs completed —
newest first, infinite scroll), right = Open Gaps list (click-through to Chat pre-scrolled to that
gap) and Running Tasks (active runs/agent jobs). **Data:** stat row and activity feed are
**placeholder** — no activity-log or rollup endpoint exists yet; gaps list could be sourced from
`GET /api/ba/projects/{id}/clarifications/next` (real, but single-item, not a list — needs a new
endpoint to list all outstanding gaps for this view; see dev notes).

## Chat

**Purpose:** the Project AI conversation — primary daily-driver screen. Full behavior spec in
[05-ai-streaming-model.md](05-ai-streaming-model.md). **Layout:** top bar shows Knowledge %, Fact
count, Requirement count, Source count, streaming connection status dot. Center: message list,
supports markdown/tables/Mermaid/PlantUML/images/PDF preview cards/code blocks. Bottom: input row
with drag-and-drop file zone, paste-image support, mention autocomplete (`@` for files/
requirements/deliverables), slash commands (`/create-brd`, `/find-gap`, `/show-workflow`,
`/generate-api`, `/export-docx`), voice input toggle. **Data:** real — this is today's
`clarifications/stream` WebSocket, reframed as an always-open chat rather than a strict
question/answer loop (see dev notes for the gap between "gap-driven Q&A" today and "open chat"
here).

## Sources

**Purpose:** everything the project's knowledge was extracted from. **Layout:** grouped list by
type (PDF, Meeting, Interview, Website, Spreadsheet, API, Code, Image), each group collapsible,
each source row shows confidence score, extracted-fact count, linked-requirement count, linked-
deliverable count, and a status pill (Processing / Ready / Failed). Clicking a source opens a
detail drawer (see [03-components.md § Drawer](03-components.md#drawer)) with the raw
file/transcript and its extracted facts side by side. **Data:** **placeholder** — no source-upload
or source-listing endpoint exists yet.

## Knowledge Graph

**Purpose:** the fact graph, made explorable — power-user surface, never forced. **Layout:**
full-canvas interactive graph (pan/zoom/search-to-focus/collapse-expand by node type), a filter
rail on the left (toggle node types on/off), a details panel on the right that opens when a node
is clicked. **Node types:** Project, Goal, Requirement, Actor, Workflow, Entity, API, Database,
Decision, Risk, Constraint, Business Rule, User Story, Acceptance Criteria. **Edge types:**
Satisfies, Uses, Depends On, Performed By, Constrains, Exposes, Threatens, Derived From. **Node
detail panel:** Properties, Evidence (source facts with confidence + provenance), History
(append-only change log for this node), Connected Nodes, linked Deliverables. **Data:** real facts
exist (`GET /api/ba/projects/{id}/facts`) but there is no graph-shaped endpoint (nodes/edges) yet —
this screen requires a new read model built from the fact table; see dev notes.

## Requirements

**Purpose:** tabular projection of Requirement nodes. **Columns:** Requirement, Actor, Priority,
Confidence, Evidence (link), Workflow, Status, Approval. Searchable, filterable per column, sticky
header. Row click opens Requirement Editor drawer: full text (view-only — append-only store, so
"editing" proposes a superseding fact), Evidence, History, Relationships (graph mini-view scoped to
this node). **Data:** placeholder (depends on the same graph read model as Knowledge Graph).

## User Stories

**Purpose:** User Story nodes as work items. **Layout:** toggle between Kanban (Backlog / Sprint /
Done, drag to move) and Table. Each card: title, priority, points (optional), acceptance-criteria
count, traceability link back to its parent Requirement. **Data:** placeholder.

## Workflows

**Purpose:** Workflow nodes rendered as BPMN-style diagrams. **Layout:** diagram canvas (swimlanes
by Actor), node palette for AI-assisted editing ("regenerate this step", "add a decision branch").
AI can generate a first draft from the graph; human can then drag/reconnect nodes — edits post as
new facts, not silent overwrites. **Data:** placeholder.

## APIs

**Purpose:** API nodes as a REST explorer (Swagger-like, generated, not hand-authored).
**Layout:** left list of endpoints grouped by resource, right pane: method/path, description,
parameters table, request/response schema, auth requirement, example request/response, linked
Requirements. **Data:** placeholder.

## Database

**Purpose:** Entity nodes rendered as an ER diagram. **Layout:** canvas diagram (tables as boxes,
FK edges), side panel listing columns/types/constraints/indexes for the selected table. Generated
from the graph, not manually modeled. **Data:** placeholder.

## Screens (UI inventory)

**Purpose:** an inventory of the *target product's* UI screens (i.e., the thing BA OS's users are
speccing, not BA OS itself) — generated wireframes, navigation flow between them, and which APIs
each screen calls. **Layout:** grid of low-fidelity wireframe thumbnails; click opens a larger
preview with a "connected APIs" list and inbound/outbound navigation arrows. **Data:** placeholder.

## Business Rules

**Purpose:** Business Rule nodes as a table. **Columns:** Rule text, Evidence, Applies To
(requirement/entity it constrains), Conflicts (flagged if two rules contradict — surfaced by the
same conflict-detection the Chat already does), linked Requirements. **Data:** placeholder.

## Risks

**Purpose:** Risk nodes as a matrix. **Layout:** 5×5 Likelihood × Impact grid, risks plotted as
dots (color = severity band), list view below for accessibility (matrix alone isn't a substitute
for a scannable list — see [01-design-system.md § Accessibility](01-design-system.md#accessibility)).
Each risk: description, likelihood, impact, mitigation, owner, status. **Data:** placeholder.

## Deliverables

**Purpose:** generated documents. **Layout:** card grid, one card per deliverable type from the
catalog (BRD, SRS, User Stories, Use Cases, Acceptance Criteria, API Spec, Glossary, Decision Log,
Risk Register, RTM, Workflow, Architecture, Database, Diagrams — full catalog matches
`DELIVERABLE_CATALOG` in today's `app.js`). Card shows status (not generated / generated / stale /
approved), frontier sequence, output format, approver. Click → Preview modal (rendered markdown,
not raw pre-formatted text as today) with Download and Version History actions. **Data:** real —
`GET .../deliverables`, `POST .../deliverables/{key}/generate`, `POST .../{instance_id}/approve`.
Version History and Download are **placeholder** (backend returns content only at generation time,
no version list endpoint — see dev notes).

## Review Center

**Purpose:** single queue of everything awaiting a human decision, across Facts, Deliverables, and
(later) Requirements — so a Reviewer persona never has to hunt across tabs. **Layout:** filterable
list (by type, by project section), each row: item summary, submitted-by, age, Approve/Reject/
Comment actions inline. **Data:** placeholder — would aggregate today's real per-resource approval
endpoints (facts, deliverables) into one queue; needs a new aggregate endpoint or client-side
merge of existing list calls.

## History

**Purpose:** literal append-only audit trail — the one screen that directly exposes the fact
store's core guarantee. **Layout:** reverse-chronological list, one row per fact/approval/run,
each with actor, timestamp, and a diff-style "what changed" (old value struck through, new value
shown, since nothing is ever deleted). **Data:** real facts have `seq`/timestamps
(`GET .../facts`), but there's no unified audit-log endpoint spanning facts+approvals+runs
together — placeholder pending that endpoint.

## Settings (project)

**Purpose:** the project's configuration — instructions, must-have/should-have, industry template.
**Layout:** simple form, same fields as today's New/Edit Project modals, promoted to a full page.
**Data:** real (`PATCH /api/ba/projects/{id}`).

## Global Search / Command Palette

Both bound to `Cmd/Ctrl+K`, same overlay, mode-switched by query: typing a plain query searches
(Requirements, APIs, Facts, Sources, User Stories, Business Rules, Deliverables); typing `>`
prefix switches to command mode (Create Requirement, Generate BRD, Open Graph, Upload Source,
Generate API, Review Risks, etc.). Full behavior in
[03-components.md § Command Palette](03-components.md#command-palette--global-search). **Data:**
placeholder — no cross-resource search endpoint exists; v1 would be a client-side fan-out over
existing list endpoints for whatever resources are already real (Projects, Facts, Deliverables).
