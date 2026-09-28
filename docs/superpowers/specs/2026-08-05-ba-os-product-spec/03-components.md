# BA OS — Product Specification: 03 Components

← [02-screens](02-screens.md) · [04-interaction-states](04-interaction-states.md) →

## Project Memory (right sidebar)

Always-visible panel while a project is open. Sections: Project Summary (1-2 sentence AI-written
rollup), Recent Decisions, Known Constraints, Business Rules (top 3, "view all" link), Stakeholders,
Goals, Assumptions, Open Questions. Updates automatically after each chat turn that changes the
graph — new/changed items get a brief highlight pulse (`--accent-subtle` background, 2s fade).

## Multi-agent status panel

Tiny, subtle strip (collapsed by default, expands on click) showing named agent stages: Document
Analyzer, Fact Extractor, Graph Builder, Planner, Renderer — each with a state dot (Idle / Waiting /
Running / Done). Maps to today's WS `status` events (`msg.stage`).

## Drawer

Slide-in from the right, `480px` wide, used for: Requirement Editor, Source detail, Node detail
(narrower variant of the Knowledge Graph's own side panel reused here). Dismiss via `Esc`, click
outside, or explicit close — same rules as modals but non-blocking (page behind stays interactive).

## Modal

Centered, `560px` default / `800px` "wide" variant (matches today's `.modal-wide` for deliverable
content). Header (title + close), body, footer (ghost cancel + primary action). Backdrop click and
`Esc` both close.

## Context menu

Right-click (or `…` button) on: project card, sidebar project item, facts-table row, deliverable
card, graph node. Options are resource-specific (e.g. fact row: Approve, View History, Copy ID).

## Command palette / global search

`Cmd/Ctrl+K` overlay, glass backdrop (only glassmorphism use, per
[01-design-system.md](01-design-system.md)). Plain text = search; `>` prefix = commands. Results
grouped by resource type with a small icon + type label. Arrow keys navigate, `Enter` selects,
`Esc` closes.

## Streaming message bubble

No boxed chat-bubble chrome (per repo's existing `README.md` note — Claude-style flowing text, not
boxes). Assistant text streams token-by-token with a blinking caret; system/status lines render
smaller and muted; conflict notices render with a `--warning` left border, not a full-width banner.

## Data table

Sticky header, zebra-free (relies on row-hover `--bg-hover` instead, calmer per the "no clutter"
brief), sortable columns where the data supports it, inline row actions right-aligned and only
visible on hover/focus (reduces default visual noise).

## Card

`10px` radius, `--border-subtle` 1px border, no shadow at rest (`--shadow-sm` only when
interactive/hovered), `16px` internal padding. Used for Project cards, Deliverable cards.

## Badge / pill

`999px` radius, `--text-micro` sizing, color-coded by status per
[01-design-system.md](01-design-system.md) semantic colors, always paired with text (not
color-only).

## Toast

Bottom-center, `--bg-surface-raised` + `--shadow-lg`, 4s auto-dismiss, one at a time (new toast
replaces old rather than stacking — matches today's implementation, kept as-is).
