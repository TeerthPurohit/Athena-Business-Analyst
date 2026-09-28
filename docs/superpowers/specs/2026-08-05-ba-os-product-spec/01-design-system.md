# BA OS — Product Specification: 01 Design System

← [00-overview](00-overview.md) · [03-components](03-components.md) →

Dark-mode-first. Every token below is defined as a dark value; light mode is a documented
alternate palette, not an afterthought — but dark ships first and is the default.

## Color

Neutral scale carries the UI (Claude/Linear influence — color is a signal, not decoration).

| Token | Dark value | Light value | Use |
|---|---|---|---|
| `--bg-canvas` | `#0B0C0E` | `#FAFAFA` | App background |
| `--bg-surface` | `#141518` | `#FFFFFF` | Sidebar, cards, panels |
| `--bg-surface-raised` | `#1C1D21` | `#FFFFFF` + shadow | Modals, popovers, dropdowns |
| `--bg-hover` | `#212226` | `#F2F2F3` | Row/item hover |
| `--bg-active` | `#26282D` | `#EDEDEF` | Selected sidebar item, active tab |
| `--border-subtle` | `#232427` | `#E8E8EA` | Card/table borders |
| `--border-strong` | `#33353A` | `#D6D6D9` | Input borders, dividers |
| `--text-primary` | `#EDEDEF` | `#17181A` | Headings, body |
| `--text-secondary` | `#9A9CA3` | `#5C5E66` | Meta, labels, timestamps |
| `--text-tertiary` | `#6B6D74` | `#8A8C93` | Placeholders, disabled |
| `--accent` | `#7C9CFF` | `#3B5BDB` | Primary actions, links, active states |
| `--accent-subtle` | `#7C9CFF1A` (10%) | `#3B5BDB14` | Accent backgrounds (selected chip, active nav) |
| `--success` | `#4ADE80` | `#16A34A` | Approved, healthy |
| `--warning` | `#FBBF24` | `#D97706` | Stale, pending, gap |
| `--danger` | `#F87171` | `#DC2626` | Conflict, error, reject |
| `--info` | `#38BDF8` | `#0284C7` | System/status messages |

Rules: no gradients as decoration (a single subtle gradient is allowed only on the landing-page
hero background, nowhere else). Color communicates state (success/warning/danger/info) — it is
never used to differentiate node types in the Knowledge Graph beyond what's specified in
[02-screens.md § Knowledge Graph](02-screens.md#knowledge-graph).

## Typography

System font stack, matching Claude's approach of "don't ship a custom webfont for chrome":

```css
--font-sans: -apple-system, "Segoe UI", "Inter", system-ui, sans-serif;
--font-mono: "SF Mono", "JetBrains Mono", Consolas, monospace; /* code, fact values, JSON */
```

| Token | Size / line-height | Weight | Use |
|---|---|---|---|
| `--text-display` | 32px / 40px | 600 | Landing hero only |
| `--text-h1` | 22px / 30px | 600 | Page/project title |
| `--text-h2` | 17px / 24px | 600 | Panel/section headers |
| `--text-body` | 14px / 22px | 400 | Default body, table cells |
| `--text-body-medium` | 14px / 22px | 500 | Emphasized body (names, labels) |
| `--text-small` | 13px / 18px | 400 | Meta, timestamps, badges |
| `--text-micro` | 11px / 16px | 500, uppercase, +0.04em tracking | Eyebrows, section dividers |

## Spacing & layout

8px base unit, matching Figma/Apple habits: `4, 8, 12, 16, 20, 24, 32, 48, 64`. No arbitrary
pixel values in component CSS. Three-column workspace: sidebar `240px` fixed (`64px` collapsed),
center flexible, right context panel `320px` fixed (collapsible, off by default under 1440px).

Corner radius: `6px` (inputs, buttons, badges), `10px` (cards, modals), `999px` (pills/avatars).
Never mix radii within one component.

## Elevation & glass

Shadows are subtle and used only to lift interactive-on-top-of-static surfaces (modals, popovers,
dropdown menus, the floating command palette) — never on static cards, matching the "no
unnecessary chrome" brief.

```css
--shadow-sm: 0 1px 2px rgba(0,0,0,0.24);
--shadow-md: 0 4px 16px rgba(0,0,0,0.32);
--shadow-lg: 0 12px 40px rgba(0,0,0,0.4);
```

Glassmorphism is used in exactly one place: the command palette / global search overlay backdrop
(`backdrop-filter: blur(12px)` over a `60%`-opacity scrim). Not on cards, not on the sidebar, not
on modals — those stay opaque. This matches the brief's "glassmorphism only where appropriate."

## Motion

Linear-style: fast, purposeful, never bouncy.

| Token | Duration | Easing | Use |
|---|---|---|---|
| `--ease-out` | 120ms | `cubic-bezier(0.2, 0, 0, 1)` | Hover states, tab switches |
| `--ease-standard` | 180ms | `cubic-bezier(0.4, 0, 0.2, 1)` | Modal/drawer open, panel expand |
| `--ease-emphasis` | 240ms | `cubic-bezier(0.16, 1, 0.3, 1)` | Toast entrance, graph node focus |

Streaming text (chat tokens, fact-extraction lines) has **no** transition — tokens appear
instantly as they arrive; the *illusion* of natural typing comes from real per-token arrival
timing server-side, not CSS animation (see [05-ai-streaming-model.md](05-ai-streaming-model.md)).
A blinking caret (`1px` wide, `--accent` color, 1s step interval) marks an in-progress stream.

## Accessibility

- Minimum contrast: body text 4.5:1, large text/icons 3:1 against their surface, in both themes.
- Every interactive element has a visible focus ring: `2px solid var(--accent)` with `2px` offset
  — never `outline: none` without a replacement.
- All icon-only buttons carry `aria-label`. All streaming regions are `aria-live="polite"` so
  screen readers announce new chat tokens/status without interrupting.
- Color is never the only signal: status badges pair color with text ("Approved", not just green).
- Full keyboard operability — see [07-keyboard-and-responsive.md](07-keyboard-and-responsive.md).

## Iconography

Single icon set only (Lucide — MIT, tree-shakeable, matches the "line icon" look of
Linear/Notion/Vercel). No mixing icon sets. 20px default size, 16px in dense tables, 1.5px stroke.
