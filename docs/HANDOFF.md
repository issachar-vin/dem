# Build handoff — live status

> Transient companion to [`../CLAUDE.md`](../CLAUDE.md). Read this at session start; update it as
> work progresses; trim finished detail once a phase merges.

**Last updated:** Phase 2 is built through **step 8** — the NiceGUI console migration is **done**
(branch `feat/nicegui-console`, PR open). The console is now mounted inside the conductor
(single process/container); the Streamlit `console/` package and the whole `/api/*` layer are gone.
One Phase 2 step remains:
- **Step 9 — verification.** (a) The user **redeploys barad-dur** to the single-container layout
  (below), then (b) runs the setup wizard on the NiceGUI console, then (c) we run the live Plane
  acceptance test.

Phase 3 design (pipeline intake/ordering/concurrency) is spec'd in `docs/PLAN.md` (PR #9) and waits
behind step 9.
**Active branch:** `feat/nicegui-console` (PR open — awaiting merge).

> **RESUME: after this PR merges, the next task is Phase 2 step 9.** First the barad-dur redeploy
> (drop `dem-console`, simplify Caddy — see below), then the user runs the wizard on the NiceGUI
> console, then the live Plane acceptance test ("resume here" further down).

## Phase 2 step 8 — NiceGUI console migration DONE (PR open, branch `feat/nicegui-console`)

**What & why:** Streamlit forced a second service (its own runtime; can't host the raw-body webhook
receiver; can't share the conductor's process), and that split was the *sole* reason the `/api/*`
management layer existed. **NiceGUI removes the split**: it's built on FastAPI and mounts *into* the
conductor via `ui.run_with(app)`, so UI + webhooks + DB run in one process/container and the pages
call the stores in-process. Reverses/rewrites **CLAUDE.md deviation #4** (updated in this PR).

**Built:**
- **`conductor/ui/`** — `context.py` (module-level `AppContext` singleton: store/mappings/auth/settings,
  populated in the FastAPI lifespan — required because NiceGUI's mounted sub-app does **not** share the
  parent `app.state`), `views.py` (five `@ui.page` screens — login-gate, wizard `/`, config, projects,
  states — plus the `AuthMiddleware`), `__init__.py` (`setup()` → `ui.run_with`). Pages call
  `ConfigStore`/`MappingStore`/`AuthStore`/`verify.py`/`plane.py` directly. Repo `owner/name` validation
  moved from the old router into the projects page.
- **`main.py`** — lifespan now builds the stores, calls `ui.configure(...)`, and stashes them on
  `app.state` (webhooks still read `request.app.state`); `create_app` includes only telemetry +
  webhooks + `/health`, then calls `ui.setup(app, storage_secret=DEM_SECRET_KEY)` **last** so those
  routes match before NiceGUI's root mount (`app.mount('/', core.app)`).
- **Auth** — UI login gate uses `AuthStore.is_initialized/create_admin/verify_credentials`; the session
  is NiceGUI's `storage_secret`-signed cookie (`app.storage.user`), so `AuthStore.issue_token/verify_token`
  + the `secret_key` ctor arg were **deleted** as dead code.
- **Cut:** the entire `console/` package; `api/config.py`, `api/mappings.py`, `api/auth.py` (kept
  `api/webhooks.py`); `docker-compose.yml` `console` service; `release.yml` → single `dem-conductor`
  image; CI `console` job; Makefile console targets; `CONDUCTOR_API_URL`. Export/import (.env + encrypted
  bundle) are now in-process UI actions on the Config page (no base64 hop).
- **Tests:** deleted `test_api.py` + the router halves of `test_mappings.py`/`test_auth.py` (store logic
  still covered); added `test_ui.py` (page registration + mount/`/health` coexistence). 60 tests green;
  ruff + mypy --strict clean.

**Verified live (local uvicorn):** `/health` + `/metrics` → 200; `/` and `/config` unauth → 307 →
`/login`; `/login` → 200 serving the NiceGUI create-admin form (proves the in-process context DI +
DB read work); `POST /webhooks/plane` unsigned → 401 (HMAC gate intact, bypasses UI auth).

**Key NiceGUI facts (verified against docs, not memory):** `ui.run_with(app, storage_secret=…)` calls
`app.mount(mount_path, core.app)` and **wraps the parent app's lifespan** to run NiceGUI's startup;
`@ui.page`/`AuthMiddleware` register on the global `core.app`; the parent `app.state` is *not* shared
with the mounted app (hence the `ui.context` singleton).

**barad-dur redeploy (user step, step 9a):** the stack drops the `dem-console` service; **Caddy**
simplifies to `dem.eroizzy.com → 192.168.88.204:8440` for everything (root UI + `/webhooks/*`); the
`:8441` console route is removed. (Caddy + compose live in the `eroizzy-env` repo, per the 7b record
below.) The `dem-conductor` image now serves the UI on 8420.

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

## Phase 2 step 6 — Auth DONE (PR #5)
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

## Phase 2 step 9 — verification (wizard run + live Plane acceptance test)
**(Step 8 has shipped, so the wizard is now the NiceGUI console.)** After the barad-dur redeploy
(step 9a above), ask whether the user has completed the setup wizard at https://dem.eroizzy.com and
route on the answer:

### If the wizard is NOT done yet — help finish it
The console gates first-run behind create-admin, then the wizard drives config. Checklist (each item
maps to what's built in Phase 2a/2b/2-UI):
1. Open https://dem.eroizzy.com → **create the admin account** (first-run; `GET /api/auth/status` still
   reports `initialized:false` as of deploy).
2. Wizard steps with a live **Test connection** button: **Claude** (subscription token per CLAUDE.md),
   **Plane** (`PLANE_API_KEY`, `PLANE_BASE_URL=https://plane.eroizzy.com`), **GitHub** (machine PAT).
   Each must go green before moving on.
3. Set **`plane_webhook_secret`** to match the secret from the Plane webhook (next item).
4. In Plane, create a **webhook → `https://dem.eroizzy.com/webhooks/plane`** (issue events), using the
   same secret as step 3.
5. **Map the `chessbro` project** (Projects page) and **map its workflow states** (States page: live
   `state-scan` → assign each canonical `WorkflowState`, incl. `ready_for_dev`).

### If the wizard IS done — run the Phase 2 end-to-end acceptance test
This is the **live Plane check** that has been pending since 2b (unit tests only synthesize signatures).
It validates the real HMAC signature, live label→name resolution, and project/state mapping. **Scope
note:** Phase 3 (dispatcher/engineer) is NOT built, so a ticket will **not** get "worked" yet — success
here = **the conductor accepts a genuine Plane delivery and enqueues a `Job`.**
1. In the mapped `chessbro` project, create an issue and add the **`epic`** label.
2. Plane fires `issue.*` → `POST /webhooks/plane`. Expected: HMAC verifies, `_is_epic` resolves the
   label UUID→name via `list_labels`, a `Job` row is inserted.
3. **Confirm it landed:** `docker logs dem-conductor` should show `Queued job for epic issue <id>
   (project <pid> → <repo>)`. (There is no jobs API/UI yet; log line or the SQLite `jobs` table is the
   check.) A non-epic issue should log/return `ignored`.
4. If signature fails (401): the wizard's `plane_webhook_secret` ≠ the Plane webhook's secret — re-check
   step 3/4 above. If `project ... is not mapped`: finish the Projects mapping.
Once this passes, **Phase 2 is fully accepted** and Phase 3 starts (design in `docs/PLAN.md`, PR #9).

<details><summary>Superseded: 7a/7b detail (kept for provenance)</summary>

**7a. Image-publish GitHub Actions workflow — DONE (PR #7).**
`.github/workflows/release.yml`: on push to `main`, a matrix job builds **both** images and pushes to
GHCR — `conductor/` → `ghcr.io/issachar-vin/dem-conductor`, `console/` → `.../dem-console`. Tags each
`latest` + `sha-<commit-sha>` (metadata-action). Auth via built-in `GITHUB_TOKEN` +
`permissions: packages: write` (no extra secrets); first publish creates the packages.
- **User TODO before 7b can pull:** confirm both tags landed under the repo's Packages, then set the
  packages **public** (or grant barad-dur a read token) so the host can pull.

**7b. barad-dur instance — DEPLOYED & verified reachable.**
Stack lives **outside this repo**, in the `eroizzy-env` repo at
`Barad-dur/Portainer/DEM/{docker-compose.yml, dem.env}` (mirrors the house convention — cf. Plane/
ChessLearner stacks). Specifics:
- **Images:** `ghcr.io/issachar-vin/dem-conductor:latest` + `dem-console:latest` (house convention is
  `:latest`, not the SHA pin; Watchtower-style updates). No bind-mounts / `--reload` (prod).
- **Host ports:** conductor **8440**:8420, console **8441**:8501. (First tried 8091/8092 — collided;
  8440/8441 are clear. If a redeploy ever errors `port is already allocated` with no logs, it's
  orphaned containers from a failed deploy: `docker rm -f dem-conductor dem-console` then redeploy.)
- **Config:** conductor reads `${DEM_SECRET_KEY}` + `${DATABASE_URL}` via **Portainer stack env vars**
  (the compose uses `environment:` interpolation, not `env_file`); paste `dem.env` into Portainer's
  Environment variables section. A **fresh Fernet `DEM_SECRET_KEY` was generated** for this deploy and
  lives in `dem.env`. SQLite in the `conductor_data` volume at `/data/conductor.db`.
- **Caddy** (in `eroizzy-env` `Barad-dur/Portainer/Caddy/Caddyfile.Caddyfile`, deployed to
  `/opt/caddy/Caddyfile`): `dem.eroizzy.com` → `/api/*` and `/webhooks/*` to `192.168.88.204:8440`
  (conductor), everything else to `:8441` (console). No path stripping (routes are already prefixed).
- **Verified live:** `GET https://dem.eroizzy.com/` → 200 (Streamlit); `GET /api/auth/status` →
  `{"initialized":false}` (conductor reachable through Caddy, no admin yet).

**Optional cleanup still open (fold into a later phase or drop):**
- **`targets.yml` import endpoint** — seeding is boot-only via `TARGETS_FILE`. For a UI "import
  targets.yml" button, add `POST /api/mappings/import-targets` calling `MappingStore.import_targets`;
  else leave mappings created one-by-one and drop the idea.
- ~~Project delete cascade~~ — **done** in the step-6 PR (`delete_project` now cascades `delete_states`).

</details>

## Phases 3–7 (per docs/PLAN.md, adjusted per CLAUDE.md deviations)
GitHub client + webhook → agent image & dispatcher → four agent roles + review loop/state machine →
observability wired to the existing barad-dur stack → docs/packaging/release.
**Phase 3 intake design is now spec'd** in `docs/PLAN.md` → "Work intake, ordering & concurrency"
(PR #9): two entry points (`epic` label → planner; `ready_for_dev` state → engineer), in-flight-first
then oldest-created ordering, a Plane **blocking-relationship** eligibility gate, one-agent-per-role
(`MAX_CONCURRENT_AGENTS=1`), planner **sets** the Plane blocks graph, and **no auto-merge** (human
approves behind branch protection). The current webhook is **epic-only** (`_is_epic` gate drops
everything else) — Phase 3 must widen it to the two-trigger router above.
Carry forward from 2b: **semantic Job dedupe** (per project+issue, see decision #3) and the
**live end-to-end Plane check** (now the Phase 2 acceptance test above).
**DB decision (confirmed): stay on SQLite** — the conductor is a single-process, single-writer, and
the spin-up-anywhere/home-lab goal rewards SQLite's zero-friction (no extra container, creds, or
tuning). No future phase requires Postgres; `DATABASE_URL` keeps it pluggable if that ever changes.
**Phase 3 prerequisite (before concurrent writes land):** `db.py` currently sets no SQLite PRAGMAs
— add `journal_mode=WAL`, a `busy_timeout`, and `foreign_keys=ON` on connect (sqlite-only, via an
engine `connect` event). Without it, overlapping webhook-ingest + dispatcher + status writes will
raise `database is locked`. Not needed in Phase 2 (nothing writes concurrently yet).

## Pending from the user
- **Run the setup wizard** at https://dem.eroizzy.com, then the **Phase 2 acceptance test** above
  (live Plane check — real `PLANE_API_KEY` + a real Plane webhook + the matching `plane_webhook_secret`).
- **Later phases:** GitHub machine-account PAT + webhook secret, barad-dur otel-collector
  host:port, ntfy/Slack notify target.
- `DEM_SECRET_KEY` for the barad-dur deploy is already generated and set in the Portainer stack env
  (`eroizzy-env` `Barad-dur/Portainer/DEM/dem.env`). A local dev run still needs its own key.
