# Athena interface

## Surface

The first surface is an operating workspace at `/app/`. It connects to the existing `/api/ba` routes for projects, chat extraction, source upload, facts, clarifications, and deliverables.

## Visual direction

The user supplied soft glass dashboard references and an Athena illustration. The interface uses a pale lavender workspace shell over a warm ambient background. The supplied engraving appears in the brand mark and sidebar detail. Inside an active project, task clarity takes priority over decorative status cards.

## Layout

- Desktop: navigation sidebar, guided project work area, and a compact next-step rail.
- Medium widths: sidebar and work area; the next-step rail drops away.
- Mobile: full-width work area, drawer menu, and stacked workflow steps.

## Type and color

- DM Sans carries controls, labels, and reading copy.
- Cormorant Garamond carries display headings and the Athena wordmark.
- Ink: `#292636`; plum action: `#a67192`; lavender selection: `#eae5f5`; apricot illustration field: `#ffddc7`.

## Interaction contract

The signed-out state asks the user to sign in or create an account. After sign-in, an account with no projects sees an inline **Create project** form. Opening a project lands in a single persistent conversation. Athena greets the user, accepts project files and free-form messages, asks the next clarification automatically, and can answer questions using project-scoped evidence tools. Jev chooses among bounded chat actions and eligible clarification gaps when confident. The Findings screen presents a fixed business analysis scope structure with an explicit approval action; changes discussed in chat return it to draft. The Documents screen lists project-scoped drafts, while deliverables can also be requested and read in chat. Upload progress and partial analysis failures remain visible in the conversation.

## Assets

`frontend/public/athena-illustration.png` and `frontend/public/athena-engraving.png` are copies of the images supplied by the user for this task.
