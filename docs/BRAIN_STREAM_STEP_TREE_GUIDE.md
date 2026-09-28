# Brain Stream Step-Tree — Node & Frontend Integration Guide

Audience: Node gateway team and frontend team integrating with the Python
Brain Agent's WebSocket stream (`/api/brain/stream`).

Architecture reminder: **Node is a thin gateway** (JWT auth, request/socket
forwarding). It has no involvement in the step-tree logic — that's entirely
server-side (Python), and the wire format below is **backward compatible**:
existing `status` chunks now sometimes carry a richer `metadata` object, but
`{type, content, session_id, metadata}` is unchanged. If Node already proxies
this WebSocket, **no Node-side change is required** — this doc mainly exists
so the frontend team knows what the new `metadata` shape means. If Node does
NOT yet proxy this endpoint, the Node section below covers it.

## What changed

Previously every progress update was a flat, human-readable sentence — the
client could only append lines, never group or update them. Additions since,
all backward compatible:

1. `status` chunks can now carry `metadata.step_id`, turning the feed into a
   **tree**: a `step_id` is published once as `running`/`pending`, then again
   later with the **same `step_id`** in a terminal state (`ok`/`error`) — the
   client updates that node in place instead of appending a duplicate.
2. Step nodes carry `agent_label` ("Search Agent") and, when a step produced
   entity rows, a `preview` of them — so a step can name its specialist and
   expand into what it actually found.
3. A new `metrics` chunk type streams the run's live counters (tokens, cost,
   model, companies found, pages read) for a metrics card / score card.
4. Steps can carry `lane` (concurrent work, e.g. per-company enrichment
   fan-out, rendered as parallel) and a new `state: "retrying"` (a tool call
   recovering from an error, not a hard failure).
5. Generated exports (CSV/XLSX) are real downloadable artifacts now, not just
   images — announced live mid-run via `status.metadata.artifact` and
   downloadable at `GET /api/brain/exports/{session_id}/{csv|xlsx}`.
6. `GET /api/brain/replay/{session_id}` returns a finished run's chunk
   history (everything except `token`s) for replay through the same reducer.

Nothing about the connection or the auth handshake changed, and the three
original chunk types (`token`, `done`, `error`) are untouched. A client that
ignores `metadata` and drops unknown chunk types behaves exactly as before.

## Wire contract (unchanged parts)

**Connect**: `wss://<host>/api/brain/stream` (nginx/Node forwards this like
any other WebSocket upgrade — see the Node section for the config).

**Client sends one JSON message immediately after connecting:**

```json
{
  "user_query": "find the top 10 defense stocks in India",
  "session_id": "<uuid, client-generated, reused across turns for the same conversation>",
  "token": "<the same JWT used for REST calls>",
  "user_id": "...",
  "company_id": "...",
  "organization_id": "..."
}
```

`message` is accepted as an alias for `user_query`. `token` can also be sent
as a `?token=` query param or an `Authorization: Bearer` header instead of in
the body — pick whichever is easiest for your client; the server checks all
three in that order.

**Server replies with a stream of `StreamChunk` JSON text frames, then closes:**

```ts
type StreamChunk = {
  type: "status" | "token" | "done" | "error" | "metrics";
  content: string;
  session_id: string;
  metadata: Record<string, any> | null;
};
```

- `status` — a progress line. See the new step-tree shape below.
- `token` — a piece of the final answer's text, arriving live as the model
  generates it (or, if nothing streamed, a post-hoc word-split fallback —
  the client can't tell the difference and doesn't need to).
- `done` — always exactly one, always last. `metadata` = `{in_scope,
  target_orchestrators, artifacts}`. `artifacts` is a list of attachments —
  `{type: "image", url, provider, width, height}` or
  `{type: "csv"|"xlsx", url, row_count, columns}` — render these directly,
  don't try to parse image links or export mentions out of the Markdown
  answer. An artifact can also arrive mid-run (see "Artifacts" below) — the
  `done` list is the aggregated final set, not the only place one appears.
- `error` — pipeline failure; the socket closes right after.
- `metrics` — a run-level counter snapshot for a live metrics card. `content`
  is always `""`; everything is in `metadata`. See "Live run metrics" below.
  **Ignoring this type entirely is safe** — it carries no progress text and no
  part of the answer.

**This is a one-shot connection** — one message in, then a stream out until
`done`. There is no resume/reconnect: if the client drops mid-run, the server
keeps working (the run isn't cancelled), but nothing is watching it anymore.
If you need "reconnect and keep watching," that's a real gap today, not
something to work around client-side.

## The new part: step-tree metadata on `status` chunks

When `metadata.step_id` is present, this chunk is a **node** in a tree, not
just a line of text. When it's **absent**, treat the chunk exactly as before
(and see "Metadata without step_id" below — that key still means something).

```ts
type StepState = "pending" | "running" | "ok" | "error" | "retrying";
type StepKind = "tool" | "narration" | "plan" | "plan_item" | "node" | "agent";

type StepMetadata = {
  step_id: string;
  parent: string | null;      // another step_id, or null = top-level
  state: StepState;
  kind: StepKind;
  lane?: string;                // present when this step runs CONCURRENTLY with sibling steps — see "Parallel lanes"
  tool?: string;               // real tool name, e.g. "search_web"
  agent_label?: string;        // display name of the specialist doing the work, e.g. "Search Agent"
  args?: Record<string, any>;  // the tool's actual call arguments
  summary?: string | null;     // filled only on the terminal (ok/error) message
  preview?: Record<string, any>[];  // terminal-only: rows this step actually produced
  started_at?: number;         // unix seconds — compute a live "12s" timer client-side, no polling needed
  step?: number;                // which executor round this belongs to
  budget?: number;              // total steps this run is allowed — only sent once, on the first "running"
};
```

**`agent_label`** is the user-facing name of the specialist running the step
("Search Agent", "Browser Agent", "Contact Agent", "Verification Agent",
"Report Generator", "Discovery Agent"). Prefer it over `tool` for display —
`tool` is the internal identifier and should stay in a debug/developer view.
Always present on `tool`/`plan_item` nodes; treat it as optional anyway.

**`state: "retrying"`** means a tool call hit an error and is about to retry
after a backoff delay (typically 30-60s) — NOT a hard failure. Render it as a
distinct "recovering" visual (e.g. amber/spinner), not the same red as
`error`. `summary` carries the error that triggered the retry. The SAME
`step_id` gets published again once the retry itself starts/finishes
(`running` → `ok`/`error`), same merge-in-place rule as everything else.

### Parallel lanes

`lane` groups steps that are running **concurrently** with each other — e.g.
one lane per company during a per-entity contact-enrichment fan-out (the
Lead-Gen "find decision-makers" phase runs several companies' lookups at
once, bounded by a concurrency limit, not one after another). It's a
different axis from `parent`: `parent` is about tree *nesting*, `lane` is
about *concurrency*. Steps that share a `lane` value have no ordering
relationship — render them as parallel columns/rows, each with its own
running→ok/error lifecycle and its own `started_at` timer. Steps without a
`lane` are sequential as before (the default — most of the tree, including
the entire executor tool loop, is NOT concurrent).

**`preview`** appears only on a terminal (`ok`) message and only when the step
actually produced entity rows. Each row is a sparse object drawn from
already-extracted data (`company_name`, `industry`, `hq_location`, `website`,
`employees`, `email`, `confidence`) — **every key is optional**, so render
whatever is present rather than a fixed column set. It's capped at 5 rows and
is a glance, not the result: the complete table still arrives in the answer.
This is what lets a finished step expand into what it found instead of just a
count.

**The identity rule**: a node is published 1–2 times with the *same*
`step_id`. First `pending`/`running` (may include `args`, `started_at`,
`budget`), later `ok`/`error` (adds `summary`). Key your UI state off
`step_id` and merge, don't append.

### `kind` values, what they mean, and what to expect

| `kind` | `parent` | Lifecycle | Notes |
|---|---|---|---|
| `plan` | `null` | Single `ok` message, no follow-up | The model's own "here's what I'm about to do" header. Only appears on some runs (round 1, and only if the model wrote a plan list) — don't require it. |
| `plan_item` | `"plan"` | `pending` → **maybe** `ok`/`error` | A planned step. **Can stay `pending` forever** — the model is allowed to drift from its own plan. Render stuck-pending items as inactive/grayed, not as an error. |
| `tool` | a `narration`/`plan` step_id, or `null` | `running` → `ok`/`error` | A real tool call. If it was one of the planned items, its `step_id` **is** that `plan_item`'s id — you'll never see the same action as two separate nodes. |
| `narration` | `null` | Single `ok` message | One short line the model wrote explaining what it's about to do this round. Parent node for that round's `tool` children (when there was no plan for this round). |
| `node`, `agent` | `null` | Usually a single message, occasionally two if paired | From the marketing/social-agent path (classify → run sub-agents → synthesize), flatter than the scraping path. `agent` nodes use the real agent name in `tool`. |

### Metadata without `step_id`

A `status` chunk's `metadata` can be non-null but have no `step_id` — that's
**older, still-valid metadata**, not a step node:

- `{"phase": "scraping", "current": N, "total": M, "percent": P}` — a plain
  scrape-progress bar. Render as a progress bar, not a tree node.
- `{"agent_id": ..., "orchestrator_agent_id": ...}` — internal routing info,
  safe to ignore client-side.

**Rule of thumb**: only build a tree node when `metadata?.step_id` exists.
Every other `status` chunk (including the heartbeat, which sends
`metadata: null`) renders exactly like before — a flat line.

### Example sequence (abridged, one realistic scrape run)

```jsonc
{"type":"status","content":"Analyzing query intent and scope...","metadata":null}
{"type":"metrics","content":"","metadata":{"tokens":1840,"input_tokens":1720,"output_tokens":120,"cost_usd":0.00067,"model":"gpt-5-mini","step":1,"budget":12}}
{"type":"status","content":"Here's my plan:","metadata":{"step_id":"plan","parent":null,"state":"ok","kind":"plan"}}
{"type":"status","content":"Look up top defense stocks","metadata":{"step_id":"plan-0","parent":"plan","state":"pending","kind":"plan_item","tool":"search_web","agent_label":"Search Agent"}}
{"type":"status","content":"Read the most promising result","metadata":{"step_id":"plan-1","parent":"plan","state":"pending","kind":"plan_item","tool":"scrape_urls","agent_label":"Browser Agent"}}
// plan-0 gets claimed by the matching real tool call — SAME step_id, now "running":
{"type":"status","content":"Searching the web for \"top defense stocks India\"","metadata":{"step_id":"plan-0","parent":"plan","state":"running","kind":"tool","tool":"search_web","agent_label":"Search Agent","args":{"query":"top defense stocks India"},"started_at":1753689600.0,"step":1,"budget":12}}
{"type":"status","content":"Searching the web for \"top defense stocks India\"","metadata":{"step_id":"plan-0","parent":"plan","state":"ok","kind":"tool","tool":"search_web","agent_label":"Search Agent","summary":"Found 10 results"}}
// round 2 has no plan match — a fresh top-level tool node:
{"type":"metrics","content":"","metadata":{"tokens":4310,"input_tokens":4050,"output_tokens":260,"cost_usd":0.00153,"model":"gpt-5-mini","step":2,"budget":12}}
{"type":"status","content":"Digging into the top source","metadata":{"step_id":"round-2","parent":null,"state":"ok","kind":"narration","step":2}}
{"type":"status","content":"Reading example.com","metadata":{"step_id":"s2-call_abc","parent":"round-2","state":"running","kind":"tool","tool":"scrape_urls","agent_label":"Browser Agent","args":{"urls":["https://example.com"]},"started_at":1753689605.0,"step":2}}
{"type":"status","content":"Reading example.com","metadata":{"step_id":"s2-call_abc","parent":"round-2","state":"ok","kind":"tool","tool":"scrape_urls","agent_label":"Browser Agent","summary":"Read 1 page(s)"}}
{"type":"token","content":"Here"}
{"type":"token","content":" are"}
{"type":"token","content":" the top defense stocks..."}
{"type":"done","content":"","metadata":{"in_scope":true,"target_orchestrators":["scraper"],"artifacts":[]}}
```

Note `plan-1` never got a terminal update in this example — the model moved
on without calling `scrape_urls` a second time. That's expected; render it as
still-pending, not stuck/broken.

### Example sequence (the discovery path — a "find N companies" run)

A lead-generation/list objective runs a different engine (budgeted discovery in
waves) and produces a flatter tree: one narration parent, one node per wave.
This is also the path that emits `preview` and the `companies_found`/`pages_read`
counters:

```jsonc
{"type":"status","content":"Searching for up to 10 companies...","metadata":{"step_id":"discovery-plan","parent":null,"state":"ok","kind":"narration","agent_label":"Discovery Agent"}}
{"type":"status","content":"Reading 6 more source(s)...","metadata":{"step_id":"discovery-wave-1","parent":"discovery-plan","state":"running","kind":"tool","tool":"discover_companies","agent_label":"Discovery Agent","started_at":1753689600.0}}
{"type":"metrics","content":"","metadata":{"companies_found":4,"pages_read":6}}
{"type":"status","content":"Reading 6 more source(s)...","metadata":{"step_id":"discovery-wave-1","parent":"discovery-plan","state":"ok","kind":"tool","tool":"discover_companies","agent_label":"Discovery Agent","summary":"Found 4 new companies — 4 total so far","preview":[{"company_name":"Acme AI","industry":"Healthcare AI","website":"https://acme.ai","confidence":0.91},{"company_name":"Widgetco"}]}}
// a wave that found nothing still closes out — the UI keeps moving, no frozen line:
{"type":"status","content":"Reading 6 more source(s)...","metadata":{"step_id":"discovery-wave-2","parent":"discovery-plan","state":"running","kind":"tool","tool":"discover_companies","agent_label":"Discovery Agent","started_at":1753689640.0}}
{"type":"metrics","content":"","metadata":{"companies_found":4,"pages_read":12}}
{"type":"status","content":"Reading 6 more source(s)...","metadata":{"step_id":"discovery-wave-2","parent":"discovery-plan","state":"ok","kind":"tool","tool":"discover_companies","agent_label":"Discovery Agent","summary":"No new companies found in this batch"}}
```

Note the second `preview` row (`{"company_name":"Widgetco"}`) has only one key —
previews are sparse by design. Render present fields, skip absent ones.

## Live run metrics (`metrics` chunks)

A `metrics` chunk is a snapshot of the run's counters, for a live "tokens /
cost / found so far" card. `content` is always empty; read `metadata`:

```ts
type RunMetrics = {
  // published by the tool-calling executor, after every LLM round
  tokens?: number;          // total tokens this run
  input_tokens?: number;
  output_tokens?: number;
  cost_usd?: number;        // estimated; ABSENT for an unpriced model — never a fake 0
  model?: string;           // the model that served the round, e.g. "gpt-5-mini"
  step?: number;            // current round
  budget?: number;          // max rounds this run is allowed

  // published by the discovery planner, after every crawl wave
  companies_found?: number;
  pages_read?: number;
};
```

Three rules, and the rest follows:

1. **Every value is an absolute cumulative total, never a delta.** Never add
   them up — just display the latest value for each key.
2. **Each publisher sends only the keys it owns**, so a chunk is a partial
   update. Merge into your existing metrics object by key
   (`{...prev, ...meta}`); don't replace it, or the token counts will vanish
   the moment a discovery wave reports.
3. **A missing key means "not known", not zero.** `cost_usd` is omitted for a
   model with no price entry — show a dash, not `$0.00`.

Because the values are cumulative, **the last `metrics` chunk of a run is also
the end-of-run summary** — an "accuracy/cost/duration" score card needs no
extra request and no separate rollup event.

Run duration is deliberately *not* in here: track it client-side from the
socket opening (same reasoning as the per-step `started_at` timer — a ticking
clock shouldn't cost network traffic).

```ts
// in the reducer switch, alongside "status"/"token"/"done"
case "metrics":
  return { ...state, metrics: { ...state.metrics, ...(chunk.metadata ?? {}) } };
```

## Artifacts (mid-run, not just on `done`)

A generated CSV/XLSX export is announced the moment it's ready — a `status`
chunk with **no `step_id`** but a `metadata.artifact` (singular) object,
same shape as one entry in `done.metadata.artifacts` (plural):

```jsonc
{"type":"status","content":"CSV export ready (42 rows)","metadata":{"artifact":{"type":"csv","url":"/api/brain/exports/<session_id>/csv","row_count":42,"columns":["company_name","email","website"]}}}
```

Treat this exactly like the "Metadata without `step_id`" rule above — it's
not a tree node — except this one key (`artifact`) IS meaningful: push it
into an artifacts list immediately rather than waiting for `done`. The SAME
artifact also appears in `done.metadata.artifacts` at the end (belt-and-
suspenders — a client that only reads `done` still gets it), so de-duplicate
by `url` if you're accumulating both live.

`GET /api/brain/exports/{session_id}/{csv|xlsx}` (bearer-authenticated, same
as the REST endpoints) downloads the actual file — the `url` field above is
that path.

## Replay

`GET /api/brain/replay/{session_id}` (bearer-authenticated) returns
`{"session_id": ..., "chunks": StreamChunk[]}` — every chunk this session
published, in order, **except `token` chunks** (the answer text itself isn't
replayed word-by-word; it's already in whatever the client stored from the
live run, or in the final report). Feed the array through the SAME
`applyChunk` reducer used for the live socket, one call per chunk, and you
get the identical step tree / metrics / artifacts state a live viewer would
have ended up with — no separate replay code path needed. Available for 24h
after a run; an expired or unknown `session_id` just returns an empty list,
not an error.

## Frontend implementation

### Building the tree client-side

Framework-agnostic reducer — call `applyChunk` for every incoming message,
keep the returned state in a `useState`/`useReducer`/store of your choice:

```ts
interface StepNode {
  step_id: string;
  parent: string | null;
  state: "pending" | "running" | "ok" | "error" | "retrying";
  kind: string;
  lane?: string;
  content: string;
  tool?: string;
  agent_label?: string;
  args?: Record<string, any>;
  summary?: string | null;
  preview?: Record<string, any>[];
  started_at?: number;
  step?: number;
  budget?: number;
  children: string[];
}

interface StreamState {
  nodes: Map<string, StepNode>;
  rootOrder: string[];        // top-level step_ids, first-seen order
  flatLines: string[];        // status chunks with no step_id (heartbeats, legacy)
  answer: string;             // accumulated token content
  metrics: Record<string, any>;  // merged run counters (see "Live run metrics")
  done: boolean;
  artifacts: any[];
  error: string | null;
}

function initStreamState(): StreamState {
  return { nodes: new Map(), rootOrder: [], flatLines: [], answer: "", metrics: {}, done: false, artifacts: [], error: null };
}

function applyChunk(state: StreamState, chunk: { type: string; content: string; metadata: any }): StreamState {
  switch (chunk.type) {
    case "status": {
      const meta = chunk.metadata;
      if (meta?.artifact) {
        // Mid-run artifact announcement — no step_id, not a tree node. De-dupe
        // by url since the SAME artifact reappears in done.metadata.artifacts.
        const url = meta.artifact.url;
        const already = state.artifacts.some((a: any) => a.url === url);
        return already ? state : { ...state, artifacts: [...state.artifacts, meta.artifact] };
      }
      if (!meta || !meta.step_id) {
        // Heartbeat, or legacy scrape-progress/routing metadata — flat line.
        return { ...state, flatLines: [...state.flatLines, chunk.content] };
      }
      const existing = state.nodes.get(meta.step_id);
      const node: StepNode = {
        step_id: meta.step_id,
        parent: meta.parent ?? null,
        state: meta.state,
        kind: meta.kind,
        lane: meta.lane ?? existing?.lane,
        content: chunk.content,
        tool: meta.tool ?? existing?.tool,
        agent_label: meta.agent_label ?? existing?.agent_label,
        args: meta.args ?? existing?.args,
        summary: meta.summary ?? existing?.summary ?? null,
        preview: meta.preview ?? existing?.preview,
        started_at: meta.started_at ?? existing?.started_at,
        step: meta.step ?? existing?.step,
        budget: meta.budget ?? existing?.budget,
        children: existing?.children ?? [],
      };
      state.nodes.set(meta.step_id, node);
      if (!existing) {
        if (node.parent) {
          const parentNode = state.nodes.get(node.parent);
          if (parentNode) parentNode.children.push(node.step_id);
        } else {
          state.rootOrder.push(node.step_id);
        }
      }
      return { ...state, nodes: new Map(state.nodes), rootOrder: [...state.rootOrder] };
    }
    case "token":
      return { ...state, answer: state.answer + chunk.content };
    case "metrics":
      // Cumulative absolute totals, partial per publisher — merge by key, never sum.
      return { ...state, metrics: { ...state.metrics, ...(chunk.metadata ?? {}) } };
    case "done":
      return { ...state, done: true, artifacts: chunk.metadata?.artifacts ?? [] };
    case "error":
      return { ...state, error: chunk.content };
    default:
      return state;
  }
}
```

Rendering: walk `rootOrder`, recursively render each node's `children`. A
`tool`/`plan_item` node in `state: "running"` can show a live elapsed timer
from `started_at` (`Date.now()/1000 - started_at`) with a local `setInterval`
— no extra network traffic needed for that. `state: "pending"` nodes render
inactive. `answer` is Markdown — render it live as it grows.

### Connecting

```ts
const ws = new WebSocket(`wss://${host}/api/brain/stream`);
ws.onopen = () => {
  ws.send(JSON.stringify({
    user_query: query,
    session_id: sessionId,   // generate once per conversation, reuse across turns
    token: authToken,
  }));
};
ws.onmessage = (event) => {
  const chunk = JSON.parse(event.data);
  setStreamState((prev) => applyChunk(prev, chunk));
};
ws.onclose = () => { /* server always closes after `done` or `error` — nothing to reconnect */ };
```

### Frontend checklist

- [ ] Key step-tree nodes by `step_id`, merge in place — never append a
      duplicate node for a repeated `step_id`.
- [ ] Only treat a `status` chunk as a tree node when `metadata?.step_id`
      exists; render everything else (including `metadata: null`) as a flat
      line, exactly like before this change.
- [ ] Render `plan_item`/`tool` nodes left in `"pending"`/`"running"` at
      `done` time as incomplete, not as errors — a run can finish with
      unclaimed plan items or, on a hard failure, an `error` chunk arriving
      instead of `done`.
- [ ] Don't assume `token` chunks are whole words — concatenate raw, don't
      insert spaces between them.
- [ ] `done.metadata.artifacts` renders directly (e.g. generated images) —
      don't scrape image URLs out of the Markdown answer text.
- [ ] No reconnect/resume — one socket per turn, closes after `done`/`error`.
- [ ] `metrics` chunks are MERGED by key into one metrics object, never summed
      and never replaced wholesale — each chunk is a partial update of absolute
      totals. An absent key means unknown (show a dash), not zero.
- [ ] Display `agent_label` rather than `tool` in the main view; keep `tool`
      and `args` for a developer/debug view.
- [ ] Treat `preview` rows as sparse — render the keys that are present, don't
      assume a fixed column set, and don't treat it as the final result.
- [ ] Run duration and per-step elapsed time are client-side timers
      (`started_at` / socket open) — the server sends no ticking clock.
- [ ] `state: "retrying"` renders distinct from `"error"` — it's recovering,
      not failed. The same `step_id` will publish again.
- [ ] Steps sharing a `lane` value are concurrent, not sequential — render
      them as parallel, each with its own timer; most steps have no `lane`
      and stay in the normal sequential tree.
- [ ] A `status` chunk with `metadata.artifact` (singular, no `step_id`) is a
      mid-run artifact announcement — push it into your artifacts list
      immediately, de-duplicated by `url` against what `done` sends later.
- [ ] Replay (`GET /api/brain/replay/{session_id}`) reuses the exact same
      `applyChunk` reducer as the live socket — don't write a second parser.

## Node

If `/api/brain/stream` (and the sibling WS endpoints — `/api/orchestrator/stream`,
`/chat/orchestrator/stream`, `/stream`) aren't already proxied, they need a
transparent WebSocket passthrough to the Python service, same as any REST
route Node forwards today — **no message inspection or transformation**,
Node never needs to understand the step-tree payload.

Example with `http-proxy-middleware` (Express) — adjust to your actual stack:

```js
const { createProxyMiddleware } = require("http-proxy-middleware");

app.use(
  "/api/brain",
  createProxyMiddleware({
    target: PYTHON_SERVICE_URL,   // e.g. http://python-api:8000
    ws: true,                     // required — this is what upgrades the connection
    changeOrigin: true,
  })
);

// If you terminate the WS upgrade on the same HTTP server instance,
// make sure the 'upgrade' event is wired to the proxy:
const server = app.listen(PORT);
const proxy = createProxyMiddleware({ target: PYTHON_SERVICE_URL, ws: true });
server.on("upgrade", proxy.upgrade);
```

Auth: forward whatever the client already sends — Node doesn't need to
pre-validate the JWT for this endpoint; Python decodes it itself (accepts it
in the initial JSON message, a `?token=` query param, or an `Authorization`
header — see the wire contract above). If Node currently strips or rewrites
the `Authorization` header on proxy, make sure that isn't happening here.

Timeouts: a scrape run can legitimately take minutes. Python's own nginx
sits at `proxy_read_timeout 3600s` / `proxy_send_timeout 3600s` for exactly
this reason — set Node's proxy (and whatever sits in front of Node) at least
that generous, or long runs will get cut off mid-stream with no `done`/`error`
ever arriving.

### Node checklist

- [ ] Confirm `/api/brain/stream` is proxied with `ws: true` (or equivalent)
      — if it already works today, nothing here needs to change; the payload
      just got richer, not different in shape.
- [ ] Auth header/token passthrough unchanged — don't inspect or require the
      JWT to be pre-validated before the upgrade.
- [ ] Read/idle timeouts generous enough for multi-minute runs (match or
      exceed Python's `3600s`).
- [ ] No caching, no message transformation, no state — Node stays a pure
      pipe for this endpoint, same as the rest of the gateway's role.
