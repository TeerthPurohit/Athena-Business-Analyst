# BA OS — Product Specification: 00 Overview

**Status:** Canonical UX/IA reference. Not an implementation plan — see [08-developer-notes.md](08-developer-notes.md)
for what's real today vs. placeholder, and for phasing into buildable slices.

**Files in this spec:**
[00-overview](00-overview.md) ·
[01-design-system](01-design-system.md) ·
[02-screens](02-screens.md) ·
[03-components](03-components.md) ·
[04-interaction-states](04-interaction-states.md) ·
[05-ai-streaming-model](05-ai-streaming-model.md) ·
[06-lifecycles](06-lifecycles.md) ·
[07-keyboard-and-responsive](07-keyboard-and-responsive.md) ·
[08-developer-notes](08-developer-notes.md)

## Product vision

BA OS turns unstructured conversation, documents, and stakeholder input into a governed, versioned
set of software specifications. The user should feel like they're working *with* a senior Business
Analyst, not filling out forms. The AI continuously converts conversation → facts → knowledge →
deliverables; the interface stays calm and minimal, with the underlying fact graph available to
power users but invisible by default.

Design references: Claude (minimal, distraction-free), ChatGPT (conversational UX), Linear
(project organization), Notion (structured workspace), Figma (spacing/craft), GitHub
(history/versioning), Jira (tracking), Apple HIG (restraint), Vercel (loading/deploy states), Arc
(browser chrome polish). See [01-design-system.md](01-design-system.md) for how these translate to
concrete tokens.

Non-negotiables carried over from the backend architecture (`agents/business_analyst/CLAUDE.md`):
the fact store is **append-only** — nothing in this UI ever presents an in-place edit of a fact;
edits are always framed as "supersede" or "propose a change," and history is always one click away.

## Personas

| Persona | Role | Primary goal in BA OS | Primary surface |
|---|---|---|---|
| **Analyst (owner)** | Business analyst running the project | Drive the fact-gathering conversation, resolve gaps/conflicts, approve facts, generate deliverables | Chat, Knowledge Graph, Review Center |
| **Reviewer / Stakeholder** | PM, tech lead, client sponsor | Approve or reject deliverables and requirements without touching the graph directly | Review Center, Deliverables, Requirements (read-heavy) |
| **Contributor** | SME, developer, other analyst | Uploads sources, answers clarifying questions, comments on specific facts/requirements | Sources, Chat, Requirements |
| **Org admin** | Workspace/org owner (Node side) | Manages membership, billing, org-wide settings — mostly outside this repo's scope | Settings only |

Every screen's empty/permission states in [04-interaction-states.md](04-interaction-states.md) are
keyed to these four roles.

## Information architecture

```
Workspace (org-scoped, from Node auth JWT: orgId/workspaceId)
└── Projects                              — grid, the landing surface after login
    └── Project workspace (three-column shell, see 02-screens.md)
        ├── Overview                      — project health: knowledge %, gaps, recent activity
        ├── Chat                          — the Project AI conversation (was "Clarify")
        ├── Sources                       — uploaded documents/meetings/links, grouped by type
        ├── Knowledge Graph               — the fact graph, interactive
        ├── Requirements                  — table view derived from graph
        ├── User Stories                  — kanban/table, derived from requirements
        ├── Workflows                     — BPMN-style diagrams, derived from graph
        ├── APIs                          — REST explorer, derived from graph
        ├── Database                      — ER diagram, derived from graph
        ├── Screens                       — UI inventory / wireframes, derived from graph
        ├── Business Rules                — table, derived from graph
        ├── Risks                         — matrix, derived from graph
        ├── Deliverables                  — generated documents (BRD, SRS, RTM, ...)
        ├── Review Center                 — pending approvals across all of the above
        ├── History                       — append-only audit trail (facts, approvals, runs)
        └── Settings                      — project instructions, must/should-have, template

Global (not project-scoped)
├── Global Search (Cmd+K)                 — everything across all projects
├── Command Palette (Cmd+K, same shortcut, mode-switched by query prefix)
└── Account / Org Settings                — thin wrapper over Node-owned data
```

Everything under a project is a **view over the same fact graph** — Requirements, User Stories,
Workflows, APIs, Database, Screens, and Business Rules are not separately-edited resources, they
are filtered/rendered projections of graph nodes (see [02-screens.md](02-screens.md) for exactly
which node/edge types feed each view). This is the single most important IA decision: it's why the
graph can stay hidden most of the time without the rest of the product feeling disconnected from it.

## Navigation tree (left sidebar, project workspace)

```
[Project name / switcher]
Overview
Chat
Sources
── Knowledge ──
Knowledge Graph
Requirements
User Stories
Workflows
APIs
Database
Screens
Business Rules
Risks
── Delivery ──
Deliverables
Review Center
── ──
History
Settings
```

Section dividers ("Knowledge", "Delivery") are the only grouping chrome — no icons-only rail, no
collapsible tree beyond that. Sidebar collapses to icons-only under 1280px (see
[07-keyboard-and-responsive.md](07-keyboard-and-responsive.md)).

## Primary user journey

```
Create Project
     │
     ▼
Start Chatting  ──────────────► (AI asks clarifying questions from the deterministic gap planner)
     │
     ▼
Upload Documents  ─────────────► Sources tab fills in, confidence-scored
     │
     ▼
AI Extracts Facts (streaming)  ─► Chat shows "Extracting facts…" progress; Overview knowledge % ticks up
     │
     ▼
Project Memory Updates  ───────► right-sidebar Project Memory panel (see 03-components.md)
     │
     ▼
Requirements Auto-Appear  ─────► Requirements table gains rows, each with Evidence back to a fact
     │
     ▼
Knowledge Graph Grows  ────────► available anytime, not forced on the user
     │
     ▼
Deliverables Become Available  ─► "Generate BRD" etc. unlock once required node types have enough facts
     │
     ▼
Review → Approve → Export
```

This journey is why **Chat, not Knowledge Graph, is the default view** when a project is opened —
confirmed decision, see [02-screens.md § Project Workspace](02-screens.md#project-workspace-shell).

## Explicitly not done here

- No multi-workspace / multi-org switching UI beyond a simple org name display — org and billing
  are Node-owned (per root `CLAUDE.md`'s node-python split).
- No real-time multi-cursor collaboration (Figma-style presence). Reviewer comments are
  asynchronous (see [06-lifecycles.md § Collaboration](06-lifecycles.md#collaboration--comments)).
- No native mobile app. Responsive web only, chat-first on small screens
  ([07-keyboard-and-responsive.md](07-keyboard-and-responsive.md)).
- No WYSIWYG editing of generated deliverable documents in this spec's v1 — approve/regenerate
  only, matching what the backend actually supports today
  ([08-developer-notes.md](08-developer-notes.md)).
