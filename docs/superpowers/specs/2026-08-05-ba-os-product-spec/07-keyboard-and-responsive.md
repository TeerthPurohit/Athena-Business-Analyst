# BA OS — Product Specification: 07 Keyboard & Responsive

← [06-lifecycles](06-lifecycles.md) · [08-developer-notes](08-developer-notes.md) →

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Cmd/Ctrl+K` | Open global search / command palette |
| `Cmd/Ctrl+N` | New project (from Projects grid) |
| `G` then `C/S/K/R/D` | Go to Chat / Sources / Knowledge Graph / Requirements / Deliverables (Linear-style go-to, project workspace only) |
| `Esc` | Close modal/drawer/palette, or cancel an in-progress AI turn's focus (does not stop the stream) |
| `Enter` | Send chat message / confirm modal primary action |
| `/` | Focus chat input and open slash-command list (when chat input not already focused) |
| `Cmd/Ctrl+Enter` | Approve current item, when a detail drawer/modal is open |

All shortcuts documented in-app via `?` (opens a shortcut cheat-sheet modal).

## Responsive breakpoints

| Breakpoint | Layout change |
|---|---|
| `≥1440px` | Full three-column shell, right context panel open by default |
| `1280–1439px` | Right context panel collapsed by default, toggle to reopen |
| `960–1279px` | Left sidebar collapses to icons-only |
| `640–959px` | Right panel hidden entirely (accessible via a slide-over), tables switch to card-list rendering |
| `<640px` | Single-column, chat-first: sidebar becomes a bottom-sheet nav, non-chat screens are secondary — matches the brief's "mobile not primary, focus on chat" |

## Mobile behavior

Chat is the only screen held to full parity on mobile. Knowledge Graph, Workflows, Database (ER),
and APIs explorer show a "best viewed on desktop" notice with a simplified read-only list fallback
rather than attempting the full canvas interaction on a touch/small-screen surface.
