# BA OS — Product Specification: 06 Lifecycles

← [05-ai-streaming-model](05-ai-streaming-model.md) · [07-keyboard-and-responsive](07-keyboard-and-responsive.md) →

## Project lifecycle

`Draft` (created, no facts yet) → `Active` (facts flowing) → `Review` (deliverables pending
approval) → `Archived` (read-only, hidden from default grid view, recoverable). Status pill on the
Projects grid card reflects this. **Placeholder** — no status field exists on `BaProject` today;
this is a UI-only state machine until the backend adds one.

## Deliverable lifecycle

`Not generated` → `Generated` → `Stale` (new facts landed since) → `Approved`. Matches today's real
`status`/`is_stale`/`approved_by` fields on the deliverable-instance resource exactly — this one is
fully real today.

## Review workflow

Item enters Review Center when it has a "pending human decision" state (unapproved fact, generated-
but-unapproved deliverable). Reviewer acts (Approve / Reject / Comment) → item leaves the queue.
Reject requires a reason (free text) that gets attached as a fact-level comment, not silently
discarded. **Partially real:** the underlying approve actions exist; the unified queue and Reject
flow (facts today only support Approve) do not.

## Collaboration & comments

Async only, no live cursors. Comments attach to a fact, requirement, or deliverable; visible in
that node's detail drawer under a "Discussion" tab. **Placeholder** — no comment endpoint exists.

## Notifications

In-app only (bell icon in top bar, not built into the shell described in
[02-screens.md](02-screens.md) but reserved as a header slot): new gap assigned to you, deliverable
awaiting your review, conflict detected. No email/push in this spec's scope. **Placeholder.**
