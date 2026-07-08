# Build handoff — live status

> Transient companion to [`../CLAUDE.md`](../CLAUDE.md). Read this at session start; update it as
> work progresses; trim finished detail once a phase merges.

**Last updated:** Phase 2 step 6 **Auth** built on branch `feat/phase-2-auth` (PR open). Next:
step 7 **barad-dur deploy** (needs barad-dur access). See "▶ resume here" below.
**Active branch:** `feat/phase-2-auth` (off `main` @ `3a11342`).

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

## Phase 2-UI — steps 1–5 + local Docker wiring MERGED (PR #4)
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
src bind-mount + `--server.runOnSave`). Root Makefile: `setup` = seed `.env` from
`.env.minimal.example` (no clobber) + `uv sync` both packages + pre-commit (venvs for the IDE; no
containers); `restart` = `down` → `build --no-cache` → `up -d`.
**Two env templates:** `.env.minimal.example` = the two mandatory bootstrap fields only
(`DEM_SECRET_KEY` [throwaway dev Fernet key] + `DATABASE_URL`) — what `make setup` copies, so a
fresh boot has empty app config and the **wizard drives first-run setup**. `.env.example` = the full
annotated reference (mirrors a wizard `Export .env`): mandatory two on top, then an `OPTIONAL`
divider and groups in wizard order (bootstrap-opts, claude, plane, github, notifications, advanced),
one header comment per group. Its dummy values boot *and* mark the wizard complete (presence, not
validity — Test-connection still fails on dummies); not for prod.
**Latent bug fixed:** both `pyproject.toml`s had `readme = "../README.md"`, which is outside each
image's build context → hatchling failed `uv sync` in Docker (conductor image never built either).
Dropped the `readme` field from both. Verified: `make restart` brings both containers up healthy
and the console reaches the conductor over the compose network.

## Phase 2 step 6 — Auth DONE (branch `feat/phase-2-auth`, PR open)
**DB-backed, conductor-enforced, single admin** (the DB is conductor-owned and the console is a thin
client, so auth lives in the conductor; the console is just a login UI). Supersedes CLAUDE.md
deviation #4's "Streamlit-native login" wording.

**Built:**
- **Conductor:** `User` model (`id`/`username` unique/`password_hash`/`created_at`) + migration
  `9ff29322a03f`. `auth.py` `AuthStore` — **argon2** (`argon2-cffi`) hashing; stateless session
  tokens via **Fernet keyed by `DEM_SECRET_KEY`** (no new token dep, Fernet's timestamp gives a 7-day
  TTL). `api/auth.py`: `GET /api/auth/status` (open), `POST /register` (409 once initialized),
  `POST /login` (401 on bad creds), plus the `require_user` dependency (Bearer token → username / 401).
- **Enforcement decision:** gated the **entire** `/api/config` and `/api/mappings` routers at the
  router level, not just writes — `GET /api/config/export.env` returns plaintext secrets, so a
  writes-only gate would leak. Open surface is now only `/api/auth/*`, `/health`, `/metrics`,
  `/webhooks/*` (webhooks keep their own HMAC). Writes thread the authed username as `source=`
  (`set_secret`/`set_setting`/`set_project` → `source=<username>`; verified `source=izzy` live).
- **Console:** `ConductorClient` gained a mutable `token` (injected as `Authorization: Bearer`) +
  `auth_status`/`register`/`login`. `views/auth.py` = create-admin (first run) vs login gate, token in
  `st.session_state`; `app.py` gates all pages + a Sign out button.
- **Also folded in** the 2b cleanup: `delete_project` now cascades `delete_states` (UI exposes delete).
- **Tests:** conductor `test_auth.py` (store + API: register-once→409, login good/bad, token round-trip,
  password hashed) + `test_api.py`/`test_mappings.py` gained authed fixtures, 401-without-token, and a
  `source=<user>` assertion. Console `test_api_client.py` gained auth methods, Bearer-header, 401 path.
  84 conductor + 26 console tests green; ruff + mypy --strict clean. **Verified live**: booted the
  conductor, ran the full flow via curl (status→register→409→authed write→source recorded→login
  good/bad→bad-token 401→export.env 401→mappings 401) — all as expected.

**Deferred / not done here:** Cloudflare Access still fronts barad-dur as an outer infra layer
(defense-in-depth) — that's infra wiring, part of step 7, not app code. No console `AppTest` gate test
(the client-level auth is covered by `test_api_client.py`; the Streamlit gate is thin glue).

## ▶ Finish Phase 2 — resume here
Step 6 is on `feat/phase-2-auth` (PR open). One chunk remains.

**7. barad-dur deploy (needs barad-dur access):**
- Add the console to the **Portainer stack** + a **Caddy route** (own subpath/host) alongside the
  conductor. Local `docker-compose.yml` already has both services; this is the production wiring.
- Keep observability external per CLAUDE.md (point at the existing barad-dur otel-collector).

**Optional cleanup still open (fold into step 7 or drop):**
- **`targets.yml` import endpoint** — seeding is boot-only via `TARGETS_FILE`. For a UI "import
  targets.yml" button, add `POST /api/mappings/import-targets` calling `MappingStore.import_targets`;
  else leave mappings created one-by-one and drop the idea.
- ~~Project delete cascade~~ — **done** in the step-6 PR (`delete_project` now cascades `delete_states`).

## Phases 3–7 (per docs/PLAN.md, adjusted per CLAUDE.md deviations)
GitHub client + webhook → agent image & dispatcher → four agent roles + review loop/state machine →
observability wired to the existing barad-dur stack → docs/packaging/release.
Carry forward from 2b: **semantic Job dedupe** (per project+issue, see decision #3) and the
**live end-to-end Plane check** (needs real creds, below).
**DB decision (confirmed): stay on SQLite** — the conductor is a single-process, single-writer, and
the spin-up-anywhere/home-lab goal rewards SQLite's zero-friction (no extra container, creds, or
tuning). No future phase requires Postgres; `DATABASE_URL` keeps it pluggable if that ever changes.
**Phase 3 prerequisite (before concurrent writes land):** `db.py` currently sets no SQLite PRAGMAs
— add `journal_mode=WAL`, a `busy_timeout`, and `foreign_keys=ON` on connect (sqlite-only, via an
engine `connect` event). Without it, overlapping webhook-ingest + dispatcher + status writes will
raise `database is locked`. Not needed in Phase 2 (nothing writes concurrently yet).

## Pending from the user
- **Live Plane check (2b, still unverified end-to-end):** real `PLANE_API_KEY` +
  `PLANE_WEBHOOK_SECRET` from an actual Plane webhook, to confirm the signature matches a genuine
  delivery and `list_labels`/`state-scan` hit the live API. Unit tests synthesize signatures.
- **Later phases:** GitHub machine-account PAT + webhook secret, barad-dur otel-collector
  host:port, ntfy/Slack notify target.
- A `DEM_SECRET_KEY` (Fernet) is required for any real run (generate per `.env.example`).
