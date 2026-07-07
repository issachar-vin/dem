# Build handoff — live status

> Transient companion to [`../CLAUDE.md`](../CLAUDE.md). Read this at session start; update it as
> work progresses; trim finished detail once a phase merges.

**Last updated:** Phase 2-UI core (steps 1–5) built on branch `feat/phase-2-ui` (PR open). Auth
(step 6) and deploy wiring (step 7) intentionally deferred to a follow-up.
**Active branch:** `feat/phase-2-ui` (off `main` @ `8050992`).

## Done & merged (durable detail lives in the code; summaries only here)
- **Phase 1** (PR #1) — conductor skeleton: `Job`/`Ticket` models, async engine, Alembic, FastAPI
  app factory + lifespan, `/health`, `/metrics`, CI (ruff/mypy/pytest).
- **Phase 2a** (PR #2) — env `BootstrapSettings` + DB-backed app config. `Secret` (Fernet) +
  `Setting` tables; DB is source of truth, env/YAML seed once (`RESEED_FROM_ENV` re-imports).
  `crypto.py`, `catalog.py` (ConfigField registry + wizard **steps** + `validate_config`/
  `step_status`), `store.py` (`ConfigStore`), `verify.py` (live Claude/Plane/GitHub tests),
  `api/config.py`. App boots with no config; gaps via `GET /api/config/status`, non-fatal.
- **Phase 2b** (PR #3) — Plane integration (below).

### Phase 2b recap — what the UI builds on
- **Management API already serving** (Streamlit is a thin client over these):
  - Config: `GET /api/config`, `GET /api/config/status`, `PUT /api/config/secret/{name}`,
    `PUT /api/config/setting/{name}`, `POST /api/config/test/{claude|plane|github}`,
    `GET /api/config/export.env`, `POST /api/config/export-bundle`, `POST /api/config/import-bundle`.
  - Mappings: `GET /api/mappings/workflow-states`, `GET|PUT|DELETE /api/mappings/projects[/{id}]`,
    `GET|PUT /api/mappings/projects/{id}/states`, `GET /api/mappings/projects/{id}/state-scan`.
  - Webhook: `POST /webhooks/plane` (HMAC-verified; not UI-facing).
- **Patterns:** creds via `store.resolved()` (dict keyed by catalog `name`); httpx clients take an
  injectable `client=` for `MockTransport`; stores live on `app.state.store` / `app.state.mappings`;
  wizard steps = `catalog.ConfigStep` (claude/plane/github/notifications/advanced), verifiable =
  claude/plane/github; canonical pipeline states = `models.WorkflowState`.
- **Confirmed Plane webhook scheme** (from Plane source, not memory): headers
  `X-Plane-{Delivery,Event,Signature}`; `HMAC-SHA256(secret, raw_body)` hex; issue `data.labels`
  are **UUIDs**, `data.project` is a UUID; epic detection resolves label names via `list_labels`.

### Phase 2b — decisions made & why (so a fresh session doesn't relitigate them)
1. **Epic detection resolves label UUIDs → names live** (`_is_epic` → `PlaneClient.list_labels`,
   match `name == "epic"`). The webhook payload only carries label **UUIDs**, so a name match is
   impossible without a lookup. Supports `plane_epic_signal` = `label` (default) and `parentless`;
   `type` falls back to label (Plane Community has no Epic work-item type). Costs one API call per
   epic-candidate issue event — acceptable; revisit with a per-project label-id cache if it's hot.
2. **`Job` carries routing in its `payload` JSON** (`project_id`, `issue_id`, `repo`,
   `base_branch`) rather than new columns — no schema churn; the Phase-3 worker reads it from there.
3. **Dedupe is on `X-Plane-Delivery` (unique `Job.delivery_id`).** Caveat: Plane mints a **new**
   uuid per delivery *attempt*, so this catches literal duplicate POSTs, **not** semantic re-fires
   of the same issue (a second `issue.updated` makes a second Job). Phase 3 needs semantic dedupe
   (e.g. one active Job per `project_id`+`issue_id`) — **left for Phase 3**, noted here.
4. **Missing/unconfigured `plane_webhook_secret` → 401** (can't verify ⇒ reject), same path as a
   bad signature.
5. **BigInteger-PK fix (latent Phase-1 bug):** a `BigInteger` PK is not a rowid alias on SQLite
   (the default backend) so it never autoincrements → `Job` inserts raised `NOT NULL … jobs.id`.
   Now `BigInteger().with_variant(Integer, "sqlite")` on `Job.id` + `StateMapping.id` in **both**
   models and migration DDL (initial migration amended — safe, nothing deployed yet).
6. **`targets.yml` demoted, not deleted** — still the parser; now feeds
   `MappingStore.import_targets` (seed-once, DB wins) on boot via the new `TARGETS_FILE` setting.

## ▶ Phase 2-UI — steps 1–5 DONE (branch `feat/phase-2-ui`, PR open)
A `console/` Streamlit service (separate uv package, thin sync client over the management API).
Single writer stays the conductor (SQLite-safe). See `docs/PLAN.md` Phase 2 + CLAUDE.md deviation #4.

**Built:**
- **Scaffold** — `console/` (own `pyproject.toml`, uv lockfile, src layout `src/console/`, ruff +
  mypy --strict + pytest; mypy override ignores streamlit stubs). One env var `CONDUCTOR_API_URL`
  (default `http://localhost:8420`). Makefile folds console into `lint/format/typecheck/test` +
  `console-run`; CI gained a parallel `console` job.
- **`api_client.py`** — `ConductorClient` (sync `httpx.Client`, injectable `client=` for
  `MockTransport` tests) wrapping every needed endpoint; `ConductorError` + dataclasses
  `TestResult`/`StepStatus`/`ConfigStatus`. `_request` maps **both** HTTP 4xx/5xx **and** transport
  errors (conductor down) → `ConductorError`, so views degrade to one "cannot reach API" message.
- **Views** (`views/{fields,wizard,config,projects,states}.py`, each `render(client)`; `app.py`
  = cached client + sidebar nav). Wizard drives off `/status`, renders masked secrets/settings,
  per-verifiable-step "Test connection". Config page = all fields + export(.env/bundle)/import.
  Projects = list/add/delete. States = pick project → live `state-scan` → map each `WorkflowState`;
  gated on the Plane step being complete. **No emoji** (global rule) — text/`st.success` badges.
- **Tests:** `tests/test_api_client.py` (20, MockTransport) — verb/path/body + error mapping incl.
  the transport-error path. ruff + mypy --strict green. **Verified end-to-end** via Streamlit
  `AppTest` against a live local conductor: badges, seeded project, and the states gate all render.

**Local Docker deploy — DONE (this PR):** `console/Dockerfile` + a `console` service in
`docker-compose.yml` (`CONDUCTOR_API_URL=http://conductor:8420`, `depends_on` conductor, port 8501,
src bind-mount + `--server.runOnSave`). Root Makefile: `setup` = seed `.env` from `.env.example`
(no clobber) + `uv sync` both packages + pre-commit (venvs for the IDE; no containers); `restart` =
`down` → `build --no-cache` → `up -d`. `.env.example` carries a throwaway **dev** `DEM_SECRET_KEY`
(valid Fernet) + dummy placeholders so a fresh copy boots and the console opens; not for prod.
**Latent bug fixed:** both `pyproject.toml`s had `readme = "../README.md"`, which is outside each
image's build context → hatchling failed `uv sync` in Docker (conductor image never built either).
Dropped the `readme` field from both. Verified: `make restart` brings both containers up healthy
and the console reaches the conductor over the compose network.

**Still deferred to a follow-up:**
6. **Auth** — Streamlit-native login as the portable default (shipped, so a clone is protected);
   Cloudflare Access in front on barad-dur (infra-level; trust the header, no app logic).
7. **barad-dur deploy** — add the console to the Portainer stack + a Caddy route (own subpath/host);
   keep observability external per CLAUDE.md. (Local compose is done, above.)

**Known gaps to decide during 2-UI (surfaced by 2b, intentionally deferred):**
- **No `targets.yml` import endpoint** — seeding is boot-only via `TARGETS_FILE`. If the UI wants an
  "import targets.yml" button, add `POST /api/mappings/import-targets` calling
  `MappingStore.import_targets`; else drop the yml-import UI and create mappings one by one.
- **Project delete doesn't cascade state mappings** — `MappingStore.delete_states` exists but isn't
  called on `delete_project`. Wire a cascade (API or store) when the UI exposes delete.

## Phases 3–7 (per docs/PLAN.md, adjusted per CLAUDE.md deviations)
GitHub client + webhook → agent image & dispatcher → four agent roles + review loop/state machine →
observability wired to the existing barad-dur stack → docs/packaging/release.
Carry forward from 2b: **semantic Job dedupe** (per project+issue, see decision #3) and the
**live end-to-end Plane check** (needs real creds, below).

## Pending from the user
- **Live Plane check (2b, still unverified end-to-end):** real `PLANE_API_KEY` +
  `PLANE_WEBHOOK_SECRET` from an actual Plane webhook, to confirm the signature matches a genuine
  delivery and `list_labels`/`state-scan` hit the live API. Unit tests synthesize signatures.
- **Later phases:** GitHub machine-account PAT + webhook secret, barad-dur otel-collector
  host:port, ntfy/Slack notify target.
- A `DEM_SECRET_KEY` (Fernet) is required for any real run (generate per `.env.example`).
