# Build handoff — live status

> Transient companion to [`../CLAUDE.md`](../CLAUDE.md). Read this at session start; update it as
> work progresses; trim finished detail once a phase merges.

**Last updated:** **Phase 3 step 7 — docs & acceptance — is done (PR #29), pending review; this
closes Phase 3's code work.** `docs/SETUP_GITHUB.md` written (machine account, fine-grained PAT
scopes + repo-visibility caveat, per-project webhook walkthrough, branch protection as the approval
gate, webhook-vs-poll, tunnel guidance) and the wizard's GitHub step now links to it; UI guide and
doc were reconciled to match. **The Phase 3 acceptance test itself is a live user step** (needs the
real Plane workspace + GitHub repos + deployment) — checklist is in `docs/SETUP_GITHUB.md` §7 and
`docs/PLAN.md` → Phase 3. Step 6 (PR #28) was **intake only**; the runtime **scheduler** (in-flight
ordering, blocking gate, `MAX_CONCURRENT_AGENTS` semaphore, container dispatch) is **Phase 4** work,
where the agent image it drives is built. **Next up: Phase 4 — agent image & dispatcher** (see the
RESUME box). Phase 2 was fully accepted
(user confirmed, 2026-07-09). Since the step-8 NiceGUI migration (PR #11, merged),
the console shipped several rounds of wizard polish, all merged and not previously logged here:
- **PR #12** — guided tabbed wizard, icon nav, dark theme (console redesign).
- **PR #13** — Plane webhook payload URL derived from the request origin, not hand-typed.
- **PR #14** — manual `0.<phase>.<patch>` version scheme + versioned Docker image tag.
- **PR #15** — version shown at the bottom of the nav drawer.
- **PR #16** — mobile nav fix, live wizard-step completion checks, GitHub webhook UX, masked secrets.
- **PR #17** — copy-to-clipboard on both payload URL fields, generate-secret button for the GitHub
  webhook secret, wizard copy for the 4 GitHub webhook events + SSL verification.

`docs/PLAN.md` Phase 3 is now **"GitHub integration & multi-repo wizard"** — folds in the pipeline
intake/ordering/concurrency design (originally PR #9) plus a new prerequisite: project↔repo mapping
widens from one-repo-per-project to **one project → many repos**, with a project-scoped GitHub
webhook secret (CLAUDE.md deviations #1, #7). Phase 3 is broken into 7 PR-sized steps (a Pydantic
request/response-model pass was inserted as step 2) — see `docs/PLAN.md` for the authoritative
deliverables/acceptance text; the box below tracks live progress.

> **RESUME: Phase 3 is code-complete (steps 1–7 done). Start Phase 4 — agent image & dispatcher.**
> Full spec: `docs/PLAN.md` → "Phase 4". Deliverables: `agent/Dockerfile` (node:22-slim + Claude Code
> CLI + git/gh + Python/pytest, non-root `agent` user); `entrypoint.sh` (fail if both auth vars set;
> seed `~/.claude.json` on a fresh state volume; exec the passed `claude -p`); the **dispatcher**
> module (`docker run` construction: per-ticket volumes, role env, mem/CPU limits, `--rm`, hard
> timeout→kill) and volume lifecycle (create + `git clone --depth 50` + `ticket/<id>` branch; destroy
> on cleanup); agent output contracts (Pydantic). **This is where the intake Jobs from step 6 finally
> get consumed** — the scheduler deferred out of step 6 (below) is built here.
> **Before starting Phase 4, the user still needs to run the Phase 3 acceptance test** (live; checklist
> in `docs/SETUP_GITHUB.md` §7). Steps 1–7 are **done** — see "Phase 3 — steps" below.
>
> **Decided for Phase 4 — git commit authorship (do NOT add manual name/email fields).** When the
> agent commits, git needs a `user.name`/`user.email`. Derive both from the existing `github_token`
> at setup time via `GET /user` (returns `login`, `name`, `id`) — works identically for a personal or
> a bot account, zero extra config. Email: if the account has a public email use it, else fall back to
> the GitHub **noreply** address `{id}+{login}@users.noreply.github.com` (private-email accounts
> return `email: null` from `GET /user`; the noreply form is what GitHub attributes to the account).
> An *optional* manual override field is fine, but the default is derive-from-token, not required
> input. This supersedes an earlier idea to add `github_bot_name`/`github_bot_email` catalog fields.
>
> **The scheduler deferred from step 6, now Phase 4 (the dispatcher/state machine):** the work **scheduler** —
> in-flight-first then oldest-created ordering, the Plane blocking-relationship eligibility gate, the
> `MAX_CONCURRENT_AGENTS` per-role semaphore, and the actual container dispatch. Step 6 only *enqueues*
> Jobs (webhook + poll); nothing consumes/orders them until Phase 4 builds the agent image + dispatcher.
> The two intake entry points and semantic dedupe those depend on **are** built (see step 6 below).
>
> **Future roadmap captured (not started):** a **user-configurable dynamic workflow** — promote
> `WorkflowState` + transitions from a code enum to DB tables and add a console page to reorder/add/
> remove pipeline steps (n8n-style ordered list). Decision: **stay NiceGUI** (ordered-list editor
> fits it; a true canvas would be one embedded JS island, not a React rewrite). The real lift is the
> backend states-as-data model. Full write-up: `docs/PLAN.md` → "Out of scope for v1" (dynamic
> workflow editor). Intake was built decoupled from pipeline shape specifically so this stays additive.
>
> **Note for a new session:** the console UI is no longer one `ui/views.py`. PR #21 split it into
> `ui/{shell,widgets,wizard,pages,auth}.py` (shell = theme/nav/layout/`_origin`; widgets = field
> editors + `_Section` + test rows; wizard = step helpers + panels + `/`; pages = config/projects/
> states; auth = middleware + login). `ui/__init__.py` imports wizard/pages/auth for their
> page/middleware registration side effects.

## Phase 2 step 8 — NiceGUI console migration DONE (PR #11, merged)

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

## Phase 2 step 9 — verification — DONE (user-confirmed, 2026-07-09)
barad-dur redeploy, wizard run, and the live Plane acceptance test (real HMAC signature, live
label→name resolution, project/state mapping, a genuine delivery enqueuing a `Job`) are complete per
the user. **Phase 2 is fully accepted.** Detailed checklist steps that were tracked here are no
longer needed and have been removed — see git history on this file if they're ever needed again.

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

## Phase 3 — GitHub integration & multi-repo wizard (IN PROGRESS — start here)
Full deliverables/acceptance text is the authoritative spec in `docs/PLAN.md` → "Phase 3". This is
the live progress tracker; check steps off as PRs land.

- [x] **Step 1 — Multi-repo mapping schema.** `RepoMapping` table (project_id, repo key, `github_repo`,
      `base_branch`); `ProjectMapping` dropped `repo`/`base_branch`, gained `enabled` + `webhook_secret`
      (encrypted, project-scoped — CLAUDE.md deviation #7); `MappingStore` gained repo CRUD +
      `get_webhook_secret`, and its read methods now return **Pydantic** `ProjectMappingView`/
      `RepoMappingView` (masks the secret) instead of raw dicts; `targets.yml` widened to project→repos
      (dropped the never-consumed `agent_image`/`model_*` overrides). Migration `8c03955898af` (clean
      cut, no data migration). The Plane epic webhook now gates on `enabled` and no longer pins a repo
      in the Job payload (repo is per-ticket, the planner's job). Projects admin page reworked for the
      multi-repo model (polished per-project wizard sections are step 4).
- [x] **Step 2 — Pydantic request/response models (PR #21, merged).** Plane `issue` webhook payload
      modelled as `PlaneWebhook`/`PlaneIssueData` in `api/webhooks.py`, parse-then-validate at the
      boundary (malformed body → 400); `_is_epic` takes a typed `PlaneIssueData`. Store/config
      surfaces converted to Pydantic read models: `ConfigStore.list_config` → `list[ConfigFieldView]`,
      `ConfigStore.status` → `ConfigStatus`, `MappingStore.list_states` → `list[StateMappingView]`;
      `catalog.StepStatus` + `verify.VerifyResult` moved from dataclasses to Pydantic. Internal typing
      only — response JSON shapes unchanged. Also folded in (SRP): the 880-line `ui/views.py` split into
      `ui/{shell,widgets,wizard,pages,auth}.py`.
- [x] **Step 3 — Live repo/project listing.** `PlaneClient.list_projects()` (paginated
      `GET .../workspaces/{slug}/projects/`, unwraps `results` like `list_states`/`list_labels`);
      `verify.list_github_repos(token)` (paginated `GET /user/repos`, follows the `Link: rel="next"`
      header via `httpx.Response.links`, returns `owner/name`, **empty list on any failure** so the
      wizard falls back to a free-typed field — same contract as `list_claude_models`). Shared
      `verify._github_headers` extracted (used by `verify_github` too). Internal API only, not yet
      wired into the UI (step 4). Note for SETUP_GITHUB.md: fine-grained PATs only list repos
      explicitly granted at token creation, so a missing repo = the token's GitHub-side grant needs
      editing, not a conductor bug.
- [x] **Step 4 — Wizard UI (PR #24, merged).** Plane panel gained a "Projects to manage" sub-step
      (checkbox per workspace project from `PlaneClient.list_projects()`, backed by
      `ProjectMapping.enabled`). GitHub panel: dropped the flat global `github_webhook_secret` (catalog
      field + `_conditionally_required` gate removed — secrets are project-scoped now) and added one
      section per **enabled** project: repo picker fed by `verify.list_github_repos()` (live select with
      typeable fallback; 1 slot + "Add another repo"; existing repos removable), project-scoped webhook
      secret field + Generate button (`set_project(webhook_secret=…)`), and the shared `/webhooks/github`
      payload URL + copy control + 4-events/SSL instructions (webhook mode only). `_is_owner_name` moved
      pages.py → widgets.py (shared). `.env.example` drops `GITHUB_WEBHOOK_SECRET`.
- [x] **Step 5 — Structured targets.yml export/import.** `targets.py` gained `parse_targets(text)`
      (factored out of `load_targets`) + `dump_targets(targets)` (inverse; `exclude_none` so an absent
      secret isn't serialized as `null`). `MappingStore` gained `export_targets(workspace)` (builds
      `Target`s from the DB, decrypts each project's webhook secret to plaintext, dumps YAML) and
      `import_targets_text(text, reseed=True)` (UI supplies text, not a path; reseed by default so an
      explicit upload applies over existing mappings — the seed-once boot path stays `reseed=False`);
      the import loop is shared via `_apply_targets`. Config page's export/import section gained
      "Download targets.yml" + "Import targets.yml" controls next to the `.env`/bundle ones. The flat
      `.env` export is unchanged (can't represent a dynamic repo list). Workspace slug comes from
      `store.resolved()['plane_workspace_slug']`; plaintext secrets in the file carry the same
      handle-carefully caveat as the `.env` export.
- [x] **Step 6 — GitHub webhook handler + poll mode (intake layer, PR #28).** Cut to **intake only**
      (see the RESUME box for what deferred to Phase 4). Built:
      - **`db.py` SQLite PRAGMAs** (prerequisite): `journal_mode=WAL`, `busy_timeout=5000`,
        `foreign_keys=ON` via a sqlite-only engine `connect` listener.
      - **`jobs.enqueue_job`** (new `jobs.py`) — the single intake choke point for both webhooks and the
        poll loop. Two dedupe layers: `delivery_id` (unique col, literal re-delivery) + `dedupe_key`
        (semantic — at most one *active* queued/running Job per key; read-then-insert, race-tolerant
        under the single-writer model). New `Job.dedupe_key` column + index (migration `a1b2c3d4e5f6`).
      - **`/webhooks/github`** (`api/webhooks.py`) — typed Pydantic payload models; **verify-after-lookup**
        (unverified `repository.full_name` → `MappingStore.get_project_for_repo` → that project's
        `webhook_secret` → HMAC over `X-Hub-Signature-256: sha256=…`); routes the 4 events via a
        `GITHUB_EVENT_PAYLOADS` map (open/closed — a new event is one entry), deduped by
        `X-GitHub-Delivery`; unmapped repo / missing / wrong secret → 401; other events acked+ignored;
        malformed body → 400.
      - **Second Plane entry point** — `_plane_trigger` now returns `planner` (epic label) **or**
        `engineer` (issue whose `state` == the project's mapped `ready_for_dev`), else ignored; both get
        semantic `dedupe_key = "<project>:<issue>"`. `MappingStore.get_state_id` added.
      - **Poll mode** (`poller.py`) — `GitHubPoller.poll_once` reads each enabled project's repos' open
        PRs (`github.GitHubClient.list_pull_requests`, new read-only client), enqueues on PR-state change;
        first sweep per repo is a silent baseline (no startup storm). Launched from the lifespan via
        `start_if_enabled` iff `GITHUB_EVENT_MODE=poll`; cancelled on shutdown.
      - Refactor: `verify._github_headers` → `github.github_headers` (shared, dedup).
      - **Tests:** +29 (webhook GitHub routing/401s/dedupe, ready_for_dev entry point, `jobs` dedupe
        matrix, `github` client, `poller` baseline/change/error, mappings lookups, db PRAGMAs). 105 green;
        ruff + mypy --strict clean. Verified: full migration chain + no autogenerate drift; app boots
        with poll mode, `/health` 200, both webhooks 401 unsigned, clean lifespan shutdown.
- [x] **Step 7 — Docs & acceptance (PR #29).** `docs/SETUP_GITHUB.md` written: machine account,
      fine-grained PAT scopes (Contents RW / Pull requests RW / Metadata) + the repo-visibility caveat,
      webhook-vs-poll tradeoffs, per-project webhook walkthrough (per repo, same project secret, the 4
      events, `application/json`, SSL on) with the verify-after-lookup explainer, branch protection as
      the human-approval gate (not prompts), and tunnel guidance (Cloudflare Tunnel / Tailscale Funnel).
      The wizard's GitHub step links to it and its inline guide was reconciled to match the doc.
      **Acceptance test = a live user step** (real Plane + GitHub + deployment): checklist in
      `docs/SETUP_GITHUB.md` §7 / `docs/PLAN.md` → Phase 3 — not run in this PR.
      - **Follow-up fix (PR #30), from the live acceptance run:** a repo webhook left on GitHub's
        default form-encoding delivered a non-JSON body → `request.json()` raised an uncaught
        `JSONDecodeError` → **500**. Both webhook handlers now parse via a shared `_parse_json` guard
        that returns **400** with an actionable "set content type to application/json" message. (Live
        acceptance confirmed otherwise: reject paths 401/400, and a correctly-configured repo's signed
        `ping` returns 200 — verify-after-lookup works end to end.)
      - **Follow-up fix (PR #31), from the live acceptance run:** the console's `targets.yml` and
        encrypted-bundle importers used NiceGUI 2.x's `event.content.read()`; NiceGUI 3.x replaced it
        with `event.file` (async `.read()`/`.text()`), so uploads died on an uncaught `AttributeError`
        with no toast and imported nothing. Extracted testable `_apply_targets_upload`/
        `_apply_bundle_upload` adapters (correct API) + a catch-all so an upload can never fail
        silently again. Same PR refactors `/config` into per-step tabs + a Migration tab
        (Export / Import sections).
      - **Follow-up fix (PR #32), from the live acceptance run:** a real Plane state-move delivery
        returned **400** because the `state` field added in step 6 assumed a plain string, but Plane
        can send it as a nested object. Hardened `PlaneIssueData.state` to coerce object/null → id
        string. Also (per request) **every webhook 4XX now carries a field-level reason in the
        response body *and* a `logger.warning`** (via a shared `_reject` helper + `_validation_detail`),
        so a bad delivery is diagnosable from either the sender's delivery log or the conductor logs.
        Same PR also drops the **vestigial global `github_base_branch`** field (wizard step 2) — the
        per-repo `RepoMapping.base_branch` (step 3, seeded from each repo's live default branch) is
        the real source of truth; the global one was never read by any operation.
      - **Follow-up (PR #34), observability:** every accepted webhook delivery now logs identifying
        fields at INFO (`delivery`, `event`, `action`, `issue`/`repo`, `project`, `state`) and the
        full raw body at DEBUG (`_log_delivery`), so two deliveries for one action can be told apart.
        `LOG_LEVEL` (default INFO) raises **only the conductor logger** — DEBUG surfaces payloads
        without aiosqlite/sqlalchemy flooding the logs. (Ordered after PR #33's note; both touch this
        block, so a merge may need a trivial reconciliation depending on merge order.)

**DB decision (confirmed): stay on SQLite** — single-process, single-writer conductor; the
spin-up-anywhere/home-lab goal rewards SQLite's zero-friction. `DATABASE_URL` keeps it pluggable if
that ever changes; no phase requires Postgres.

## Pending from the user
- **Later phases:** barad-dur otel-collector host:port, ntfy/Slack notify target (Phase 6).
- `DEM_SECRET_KEY` for the barad-dur deploy is already generated and set in the Portainer stack env
  (`eroizzy-env` `Barad-dur/Portainer/DEM/dem.env`). A local dev run still needs its own key.
