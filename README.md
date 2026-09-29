# Athena

Athena is a tenant-isolated, evidence-backed business analyst agent with an append-only fact store, deterministic planning, quality checks, and reproducible deliverables.

## Run

Set `DATABASE_URL`, a stable random `JWT_ACCESS_SECRET` (at least 32 bytes), and LLM credentials in `.env` or your shell environment, then run:

```powershell
uvicorn app.main:app --reload --port 8080
```

The API is available at `/docs`; Athena endpoints are under `/api/ba` and `/api/auth`. On startup, the Python app creates the local account table if needed.

## Frontend

The React workspace lives in `frontend/`. Run `npm install` and `npm run dev` there,
then open `http://localhost:5173/app/`. Vite proxies `/api` to the Python API
on port 8080. Build with `npm run build` before starting the Python app to serve
the workspace at `http://localhost:8080/app/`.

Use **Sign in** or **Create account** in the workspace. The Python API hashes passwords with Argon2 and issues a 12-hour JWT containing the account ID and its isolated organization ID. The token stays in browser session storage. Create or open a project, then add
project context, upload sources, inspect facts, and answer clarifications. **Investigate project** uses project-scoped tools to search the active project's facts and uploaded source text, read excerpts, trace fact provenance, and inspect its overview. It uses `OPENROUTER_API_KEY` when available (or existing OpenAI/NVIDIA chat credentials). OpenRouter defaults to `z-ai/glm-5.3-flash` with `openai/gpt-6-luna` as the model fallback; set `BA_LLM_FALLBACK_MODEL` to change it. Evidence search defaults to `google/gemini-embedding-001` at 3,072 dimensions; set `BA_EMBEDDING_MODEL` and `BA_EMBEDDING_DIMENSIONS` to override the embedding model and vector size. Model or dimension changes automatically refresh cached project vectors. Semantic search is combined with keyword matches, and keyword search remains available if embeddings are unavailable. Set `BA_ANALYST_MODEL` to override the investigation reasoning model. Investigation answers are judged against the request and project evidence on relevance, grounding, completeness, instruction following, and clarity. When the judge says a material improvement is needed, Athena makes one evidence-bounded revision and scores that revision again; the rubric scores and feedback are shown with the answer and saved in the project record. Set `BA_EVALUATOR_MODEL` to run the evaluator on a separate model (defaults to the reasoning tier). **Record context** adds facts to the project; investigation reads existing evidence without adding facts.

## Jev conversation decisions

Before each project chat turn, Athena sends the current message, pending question,
and up to four recent turns to Jev in one Decisions API request. Jev returns
bounded choices for the action (including greetings), user tone, topic shift, and
whether to probe, move on, or investigate an unresolved concern more deeply. The
chat model writes greeting replies and investigation answers; greetings do not run
project fact extraction. Low-confidence or unavailable decisions fall back to the
existing chat routing, and a simple greeting still receives a greeting reply.

Set `OPENROUTER_API_KEY` in the environment or local `.env` to let Jev 1.13
choose among the top three eligible clarification gaps. Athena calls OpenRouter's
Decisions API only when more than one gap is available. If the key is absent,
the service fails, or the decision is uncertain, it asks the highest-ranked gap
from the existing deterministic scorer. The Jev question is seeded as
`ba_jev_clarification_choice_v1` in the BA prompt catalog.
