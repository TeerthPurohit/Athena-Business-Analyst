# Athena council review — 2026-09-24

## Scope

The active-project journey: add material, review findings, answer questions, and create/read/download a document. Three independent, read-only judges reviewed user experience, frontend behavior/accessibility, and backend/API integrity. One fixer implemented the consolidated changes. Judges then re-checked their findings.

## Decisions and outcomes

| Finding | Result |
| --- | --- |
| Mobile menu backdrop intercepted drawer taps | Fixed: backdrop and drawer share a stacking context. |
| Responses from another project or sign-in could appear in the current project | Fixed: auth/project scopes and request sequence guards reject stale responses. |
| Finding approval lacked inspectable evidence | Fixed for cited requirements: source excerpts appear with findings; the project-scoped source download remains available. Other fact types may have no cited span. |
| Clarification conflicts were hidden | Fixed: conflict notices appear in Questions and recent activity. |
| Modal keyboard focus was unmanaged | Fixed: focus enters the dialog, Tab remains inside, Escape closes it, and focus returns. |
| Uploaded facts did not feed question ranking | Fixed: current requirement and gap facts are projected for question selection. |
| Asked questions could disappear after refresh; answered questions could repeat | Fixed: pending questions resume; answered gaps are skipped; newly asked REST questions commit. |
| Source semantic analysis could be skipped because any fact existed | Fixed: a stage-specific completion marker controls the guard. |
| Extraction failure could leave partial facts | Fixed: extraction runs in a nested transaction. |
| Tenant deliverable overrides were ignored by staleness checks | Fixed: staleness uses the effective tenant specification. |
| Old draft could be downloaded without clear context | Improved: stale drafts show a warning and confirmation, including from the preview. |

## Verification

- Vite production build passed.
- Changed Python modules compiled.
- Skill validation passed for `.agents/skills/athena-council`.
- Backend tests could not start because the local PostgreSQL instance rejected a passwordless connection.
- No authenticated live-browser walkthrough was available. The judges' final verdicts are based on current source and bounded build checks.

## Remaining usability work

Document preview still displays Markdown source. Formatting it as a readable document is a lower-priority improvement. Findings without a cited source span can still offer only their source file, so source inspection remains important before confirmation.
