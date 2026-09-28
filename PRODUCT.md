# Athena

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

React + Vite, confirmed by the user. The frontend lives in this repository.

## Users

Business analysts and project stakeholders working through requirements, clarifications, evidence, and deliverables.

## Product Purpose

Athena is a tenant-isolated business analyst workspace. It stores evidence-backed facts, supports clarification, uses deterministic planning and quality checks, and produces reproducible deliverables.

## Operating Context

The Python API exposes account registration and login under `/api/auth`, plus projects, sources, chat, facts, clarifications, runs, and deliverables under `/api/ba`. Each new account receives a distinct organization ID; the Python API issues its JWT.

## Capabilities and Constraints

- Facts are append-only.
- Planning and evidence scoring remain deterministic.
- Jev may select among already eligible clarification gaps when its OpenRouter decision is confident; deterministic ranking remains the fallback.
- A signed-in account with no projects opens on a project creation screen. Analyst work opens only inside an active project.
- Project references include uploaded documents, Markdown, and selected source code files. Recorded facts show their source when available.
- Athena's investigation action calls project-scoped tools to search evidence, read source excerpts, trace fact provenance, and inspect the project overview. The active Athena project is the inspection boundary.
- A project's primary surface is one persistent chat. Jev routes bounded intents and clarification choices; the configured BA model synthesizes the project narrative and answers evidence-backed questions through project-scoped tools.
- Uploaded text is analyzed in bounded chunks for requirements and wider project facts. The fixed Findings scope records narrative, outcomes, priorities, delivery context, and approval state.

## Brand Commitments

The name is Athena. The user supplied an Athena illustration and requested a soft glass interface, using the attached interfaces as composition references.

## Evidence on Hand

The API implementation and README in this repository provide product behavior. The attached images provide visual references; they do not supply product copy or usage claims.

## Product Principles

- Make the next analyst action clear.
- Keep evidence and decisions easy to inspect.
- Preserve the distinction between machine suggestions and recorded facts.
