# Model Config & Super Admin API — Node/Frontend Integration Guide

Audience: Node gateway team and frontend team integrating with the Python
agent service's model-configuration and super-admin endpoints.

Architecture reminder: **Node is a thin gateway** (JWT auth, request
forwarding). It has no DB tables of its own for prompts, providers, or model
catalog — all of that lives in Python/Postgres. Node's only job here is (1)
forward these routes untouched, and (2) eventually stamp a super-admin claim
on the JWT (see [Enforcing the super-admin gate](#enforcing-the-super-admin-gate)).

## Data model (Python-owned, for context)

| Table | Purpose |
|---|---|
| `provider_credentials` | One row per (category, provider) API key. `category` ∈ `llm` \| `image` \| `scraping`. Key is Fernet-encrypted at rest; API never returns plaintext, only `api_key_masked` (`...last4`). |
| `model_catalog` | One row per model a provider exposes. `is_enabled` = the on/off toggle; `is_default` = which enabled model a category falls back to. Only one `is_default=true` per category. |
| `system_setting` (global rows, `user_id IS NULL`) | Admin-tunable knobs: temperature, token limits, default model strings. |
| `system_setting` (per-user rows, `user_id` set) | A user's own model pick, stored as `config.llm_model` / `config.image_model` (`{provider, model, quality?}`). |

`scraping` category is backend-only — never exposed to end users, only to
super admins via `/api/admin/*`.

## Auth

All routes decode the JWT Node issues, using the shared secret
`JWT_ACCESS_SECRET` (HS256). User id comes from `sub` / `id` / `userId`.

- **User-facing routes** (`/api/brain/models`, `/api/brain/model-preference`):
  any authenticated user, acting on their own preference only. No role check.
- **Super-admin routes** (`/api/admin/providers`, `/api/admin/models`):
  gated by `require_super_admin`, currently **dormant**.

### Enforcing the super-admin gate

Right now `SUPER_ADMIN_ENFORCED=false` in Python's `.env` — every
`/api/admin/providers*` and `/api/admin/models*` call is open to anyone with
a valid (or even missing) bearer token. This was left open deliberately
because Node doesn't issue a super-admin claim yet.

To turn on real enforcement, Node must:

1. Add a claim to the JWT for super-admin users — either `role: "SUPER_ADMIN"`
   or `is_super_admin: true`.
2. Tell Python to flip `SUPER_ADMIN_ENFORCED=true` (and optionally
   `SUPER_ADMIN_ROLE` if the role string isn't `SUPER_ADMIN`).

No endpoint code changes needed on the Python side — the check is already
implemented, just inactive. `GET /api/admin/settings`, `PUT /api/admin/settings`,
`DELETE /api/admin/settings/{id}`, `GET /api/admin/main-agents`, and all of
`/api/admin/prompts/*` are **not** gated by `require_super_admin` at all today
(no `dependencies=[Depends(require_super_admin)]` on those routes) — if those
also need super-admin-only access, that's a Python-side change to request
separately, not something Node can enforce by itself.

## Super Admin Endpoints

Base path: `/api/admin`. Bearer JWT required once enforcement is on.

### Provider credentials (`/api/admin/providers`)

| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/api/admin/providers` | `{category, provider, api_key, base_url?, label?}` | Upserts by `(category, provider)`. Returns `api_key_masked`, never the key. |
| GET | `/api/admin/providers?category=` | — | List all, or filter by category. |
| PATCH | `/api/admin/providers/{id}` | `{api_key?, base_url?, is_active?, label?}` | Partial update. |
| DELETE | `/api/admin/providers/{id}` | — | Removes the credential. |
| POST | `/api/admin/providers/{id}/sync` | — | Pulls the provider's live model list into `model_catalog` (new models land `is_enabled=false` until an admin turns them on). |

`category` ∈ `llm` \| `image` \| `scraping`. Known `provider` values today:
`openai`, `nvidia` (llm), `openai`, `nvidia_nim` (image), `serper`,
`tinyfish`, `exa`, `tavily`, `rayobyte`, `scrapedo`, `apify`, `brightdata`
(scraping) — but the field is a free string, not an enum; a new provider just
needs a credential row + catalog rows.

### Model catalog (`/api/admin/models`)

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/api/admin/models?category=` | — | Full catalog (enabled + disabled), unlike the user-facing `GET /api/brain/models` which only returns enabled ones. |
| POST | `/api/admin/models` | `{category, provider, model_name, display_name?}` | Manually add a model row; created `is_enabled=false`. |
| PATCH | `/api/admin/models/{id}` | `{is_enabled?, is_default?, display_name?}` | Setting `is_default=true` auto-clears the flag on every other model in that category. |
| DELETE | `/api/admin/models/{id}` | — | Removes the row. |

**This is the primary "enforce model config" lever**: toggling `is_enabled`
here is what makes a model appear/disappear from `GET /api/brain/models` for
every end user, immediately. There's no separate publish step.

### System settings (`/api/admin/settings`)

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/api/admin/settings` | — | Current merged settings (DB override + `.env` defaults). |
| PUT | `/api/admin/settings` | Any subset of `temperature`, `EMBEDDING_MODEL`, `DEFAULT_LLM_MODEL`, `LLM_MODEL_NANO`, `LLM_MODEL_MINI`, `IMAGE_OPENAI_MODEL`, `MAX_SCRAPE_TOKENS`, `CONTEXT_TOKEN_LIMIT`, `MAX_INPUT_TOKENS` | Validated ranges: `temperature` 0–2, `MAX_SCRAPE_TOKENS` ≥ 100,000, `CONTEXT_TOKEN_LIMIT` ≥ 500,000, `MAX_INPUT_TOKENS` > 0. Writes a new versioned row (append-only). |
| DELETE | `/api/admin/settings/{config_id}` | — | Rolls back to `.env` defaults. |

### Other admin reads

- `GET /api/admin/main-agents` — enabled orchestrator agents (`id`, `name`,
  `type`, `details`).
- `GET /api/admin/prompts/agents` — distinct `(agent_id, agent_name,
  prompt_key)` catalog, use as a lookup before the calls below.
- `GET /api/admin/prompts/list?agent_id=&prompt_key=` — all prompt versions.
- `POST /api/admin/prompts/new` — new versioned prompt, `activate_immediately`
  defaults true.
- `POST /api/admin/prompts/activate` — switch the active version for an
  `(agent_id, prompt_key)`.
- `DELETE /api/admin/prompts/delete?agent_id=&prompt_key=&version=` — `v1` is
  permanently protected; the active version can't be deleted directly.

## User-Facing Model Preference (`/api/brain`)

Not admin-gated — any authenticated user manages only their own preference.
Two independent categories: `llm` (chat) and `image`. `scraping` is
intentionally not exposed here.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/brain/models?category=llm\|image` | Enabled models only, each with `{provider, model_name, is_default}`. Drive the picker dropdown from this — don't hardcode model names in the frontend. |
| GET | `/api/brain/model-preference?category=llm\|image` | Returns the user's explicit pick if they have one (`is_explicit: true`), else the category default (`is_explicit: false`). Never 404s. |
| PUT | `/api/brain/model-preference` | `{category, provider, model_name, quality?}`. `quality` ∈ `low`\|`medium`\|`high`\|`auto`, image-only. Server re-validates the pick is still in the enabled list — a disabled model always returns 400 with a pointer back to `GET .../models`. |

A stored preference pointing at a model an admin later disabled is **not**
deleted — `get_brain_model_preference` silently falls back to the category
default. So a frontend that caches the last-known preference client-side
should still re-fetch on load rather than trusting a stale local copy.

## Frontend Integration

### End-user "AI Model" settings screen

1. On mount: `GET /api/brain/model-preference?category=llm` and `category=image`
   in parallel to show current selection.
2. Populate each dropdown from `GET /api/brain/models?category=...` — do not
   assume any specific provider/model exists.
3. For `image`, show a quality selector (`low`/`medium`/`high`/`auto`) only
   when the picker is on image.
4. On change: `PUT /api/brain/model-preference`. Handle 400 (model no longer
   enabled) by refetching the model list and re-rendering — this happens
   whenever an admin disables the model mid-session.

### Super-admin console

1. **Providers tab**: form → `POST /api/admin/providers` (category, provider,
   api key, optional base URL/label). List view reads `GET
   /api/admin/providers`; never render more than `api_key_masked`. A "Sync
   models" button per row calls `POST /api/admin/providers/{id}/sync`.
2. **Model catalog tab**: table from `GET /api/admin/models` grouped by
   category, with an enable/disable toggle (`PATCH .../models/{id}`,
   `is_enabled`) and a "set default" radio per category (`is_default`).
   Toggling here is the actual enforcement mechanism — it takes effect for
   all users on their next `GET /api/brain/models` call, no cache to bust.
3. **System settings tab**: form bound to `GET`/`PUT /api/admin/settings`,
   client-side mirroring the same validation ranges as the server so bad
   input fails fast instead of round-tripping a 400.
4. Gate the whole console client-side on the user's role/claim too — but
   remember the *server* doesn't actually enforce this yet
   (`SUPER_ADMIN_ENFORCED=false`), so this is currently UX polish, not
   security, until the JWT claim work above ships.

## Checklist for Node

- [ ] Proxy `/api/admin/*` and `/api/brain/models`, `/api/brain/model-preference`
      pass-through, unchanged — no Node-side business logic or caching of
      model/provider data.
- [ ] Add `role: "SUPER_ADMIN"` (or `is_super_admin: true`) to JWTs for
      admin users, then coordinate flipping `SUPER_ADMIN_ENFORCED=true` on
      the Python side.
- [ ] Do not persist provider credentials, model catalog, or prompts in any
      Node/Prisma table — Postgres via Python is the single source of truth
      (see the separate, unrelated Prisma `Agent` table used for per-org
      custom agents — don't confuse the two).
