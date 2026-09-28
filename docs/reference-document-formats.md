# Reference BRD and FRD formats

BRD generation follows the structure reviewed from `brd.pdf`: document information and control, distribution list, source references, contents, introduction and scope, definitions, business purpose and system overview, reporting principles, data exchange and validation, requirements register, non-functional requirements, and a data-field annex.

FRD generation follows the structure reviewed from `frd.pdf`: document information, revision and approval histories, contents, introduction and scope, references, methodology, solution overview and users, detailed requirement tables, context/data-flow/model sections, data dictionary, interface requirements, security and safety, performance, retention, error handling, validation and standards, assumptions/dependencies, glossary, and architecture appendix.

Project scope uses `project_summary.in_scope_features`, `functional_scope`, and the recorded priority lists; exclusions use `out_of_scope_features` and `wont_have_features`. Requirement IDs retain the existing shared `REQ-001` ordering across artifacts. Entity facts populate the data dictionary. Unknown fields remain explicitly unspecified, and diagrams require recorded design material. Example banking features, numeric targets, company names, and branding are not project evidence.

Optional document administration fields live under project `settings.document_control`: `version`, `owner`, `author`, `created_at`, `updated_at`, `updated_by`, `approved_by`, `approval_date`, `revision_history` (version/date/author/description records), and `approvals` (role/name/signature/date records). Missing approval information remains unspecified. Source references are tenant- and project-scoped and display source names instead of local file paths.

BRD and FRD are generated, stored, previewed, and downloaded as PDFs. The PDF layout includes a cover, document control tables, automatic contents with page numbers, numbered sections, blue table headers, and running headers and footers. Previously stored Markdown BRD/FRD artifacts are converted to PDF when opened or downloaded. Generate a new BRD or FRD to store the new PDF format. Other deliverable formats are unchanged.

`brd sample.pdf` was empty when supplied and contributed no content or format requirements.
