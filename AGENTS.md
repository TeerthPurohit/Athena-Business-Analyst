# Athena agent guidance

## Code discovery

Prefer codebase-memory-mcp for code discovery. Use `search_graph`, `trace_path`, `get_code_snippet`, `query_graph`, then `get_architecture`. Index the repository first when its graph is missing or stale. Use text search for literals, configuration, non-code files, or when graph results are insufficient.

## Council review

When asked for an LLM council, independent judges, or a multi-agent quality pass, load [the Athena council skill](.agents/skills/athena-council/SKILL.md). Keep judges read-only and independent, assign consolidated fixes to one fixer, and verify the result. Ordinary changes do not require a council.
