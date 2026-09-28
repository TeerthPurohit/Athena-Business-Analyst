# BA OS — Product Specification: 04 Interaction States

← [03-components](03-components.md) · [05-ai-streaming-model](05-ai-streaming-model.md) →

Catalog of the states every data-bearing screen must handle. Not every screen needs every row —
this is the checklist to run each screen from [02-screens.md](02-screens.md) against.

| State | Rule |
|---|---|
| **Loading (first load)** | Skeleton rows/cards matching the real layout's shape — never a spinner-only blank screen. Table: 5 skeleton rows. Grid: 3-6 skeleton cards. Graph: centered spinner + "Loading knowledge graph…" (skeleton doesn't work for a force-directed canvas). |
| **Loading (refresh)** | No skeleton — keep existing content, show a small inline spinner next to the action that triggered it (e.g. Refresh button). Never blank the screen for a refresh. |
| **Empty (no data, can create)** | Centered icon + one-line explanation + primary CTA. Never an empty table with just headers. |
| **Empty (no data, read-only role)** | Same as above, no CTA — text only ("Nothing here yet"). |
| **Error (request failed)** | Inline error message in place of the content, with a Retry action. Toast additionally for actions (create/approve/generate), not for passive loads. |
| **Permission-denied** | Distinct from generic error: explains *why* ("Only the project owner can approve deliverables") rather than a raw 403. |
| **Stale / needs regeneration** | `--warning` badge ("stale") — used today for deliverables, extended to any graph-derived view when new facts have landed since last render. |
| **AI working** | Typing-dots indicator (existing pattern) for "thinking", token stream for "producing text", named-stage list for multi-step generation (matches Multi-agent status panel). |
| **Conflict detected** | `--warning`-bordered inline notice, never blocks the rest of the UI — user can keep working while resolving. |
| **Approved / final** | `--success` badge, primary action button disables/relabels (e.g. "Approved" replaces "Approve"), matches today's deliverable-modal pattern. |

## Per-role empty states (persona × screen)

| Screen | Analyst (owner) | Reviewer | Contributor |
|---|---|---|---|
| Projects grid | "Create Project" CTA | Same list, no create CTA if not permitted | Same as Analyst if they can create |
| Chat | Full input enabled | Read-only transcript, no input | Full input enabled |
| Review Center | Full queue + actions | Full queue + actions (this is their home screen) | Own submissions only |
| Deliverables | Generate + Approve | Approve only, no Generate | View only |

Exact role→permission mapping is a backend/auth concern (Node-issued JWT `role` claim) — this
table defines the *UI* behavior once that data is available; it does not invent new permission
semantics.
