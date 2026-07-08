# DEM (Deus Ex Machina) — project guide & build handoff

Autonomous SWE pipeline: a Plane epic → planner splits it into tickets → an engineer agent
builds each ticket in an isolated Docker container and opens a PR → reviewer + QA agents critique
and loop feedback until both pass → a human merges. Two human touchpoints: writing the epic and
approving the PR.

**Canonical spec:** [`docs/PLAN.md`](docs/PLAN.md) (originally `instructions.md`, renamed on move
into the repo). Read it for the full architecture and phase-by-phase acceptance criteria. This
file records how the *actual build* diverges from that spec and where the build currently stands.

- **Repo:** github.com/issachar-vin/dem (public, squash-merge only, `main` branch-protected).
- **Everything lives in this folder** (the `dem` dir is the repo root).

## Stack & conventions

- Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic, httpx, Pydantic Settings, cryptography.
- Tooling: **uv** (run everything via `uv --directory conductor …`), **ruff** (format + lint),
  **mypy --strict**, **pytest**. Makefile targets: `setup dev lint format typecheck test migrate`.
- Source is `conductor/src/conductor/` (src layout, own `conductor/pyproject.toml`). Tests in
  root `tests/`. Migrations in `conductor/migrations/`.
- Workflow: never push to `main`; branch → PR (the `/make-change` skill). Squash merges only.
- `.claude/` is gitignored (holds local machine paths).

## Deployment target (barad-dur)

Runs as a Portainer stack, public at **dem.eroizzy.com** via the existing Caddy (`/opt/caddy/
Caddyfile`, `caddy_net`) + Cloudflare. Because it has a public URL, **webhook mode for both Plane
and GitHub** (no polling, no tunnel). Reuses the existing barad-dur observability stack (Grafana/
Prometheus/Loki/Tempo) via `OTEL_EXPORTER_OTLP_ENDPOINT` — we do **not** bundle our own. Plane is
already self-hosted at plane.eroizzy.com — we do **not** bundle Plane either.

## Deviations from docs/PLAN.md (and why)

1. **Multi-repo routing is core, not out-of-scope.** PLAN deferred it to "future". The user needs
   one conductor serving many repos, so routing is keyed on Plane **project_id**. Phase 1 shipped a
   `targets.yml` loader (project_id → repo); Phase 2 moves that mapping into the DB + a UI (below),
   with `targets.yml` demoted to an optional one-time seed import.

2. **Config/secret store instead of env-only settings.** PLAN used one env-driven Pydantic
   `Settings`. We split it:
   - `BootstrapSettings` ([config.py](conductor/src/conductor/config.py)) — env-only, the minimum
     needed before the DB is reachable: `DEM_SECRET_KEY`, `DATABASE_URL`, host/port, reseed flag,
     optional seed file.
   - **DB-backed application config** — Plane/GitHub/Claude creds, models, notifications live in
     `Secret` (encrypted) and `Setting` (plain) tables. The DB is the source of truth; env/yml only
     **seed** it on first boot (seed-once; DB wins thereafter; `RESEED_FROM_ENV=true` re-imports for
     IaC rotation).
   Why: the user wants UI-driven first-time setup **and** env/IaC (Pulumi) provisioning **and**
   portable config export/import — without losing any of them.
   **Secrets are encrypted (Fernet), not hashed** — they must be handed to Plane/GitHub/Anthropic
   verbatim, so they must be reversible. `DEM_SECRET_KEY` is the root of trust and cannot live in
   the DB it decrypts.

3. **Setup wizard replaces `plane-init`; "map states", don't "create states".** PLAN's `plane-init`
   was going to *create* our workflow states inside each Plane project. Instead we keep a fixed set
   of canonical workflow states in-app and **map** them onto whatever states already exist in each
   project (Plane forces 5 fixed groups — Backlog/Unstarted/Started/Completed/Cancelled — but state
   names are custom). Non-invasive, respects existing boards, works on Plane Community edition.
   `plane-init` is dropped.

4. **NiceGUI admin UI mounted inside the conductor (single process).** Originally a separate
   Streamlit service over an HTTP management API; migrated to **NiceGUI** ([ui/](conductor/src/conductor/ui/)),
   which is built on FastAPI and mounts *into* the conductor's app via `ui.run_with(app)`. UI +
   webhooks + DB now run in one process/container: the pages call the stores in-process
   (`ConfigStore`/`MappingStore`/`AuthStore`/`verify.py`) with no HTTP hop, so the whole `/api/*`
   management layer was deleted (only `/webhooks/*`, `/health`, `/metrics` remain as plain FastAPI
   routes, matched before NiceGUI's root mount). The wizard verifies each key with a live connection
   test, shows completed steps, and manages repo↔project + state mappings. Because NiceGUI's mounted
   sub-app does **not** share the parent `app.state`, the UI reaches the stores through a
   module-level `ui.context` singleton populated in the FastAPI lifespan. Auth: Cloudflare Access
   (free ≤50 users) fronts barad-dur, **plus** an app-native login gate (argon2 creds in the DB;
   session held in NiceGUI's `storage_secret`-signed cookie keyed by `DEM_SECRET_KEY`) so anyone who
   clones it gets a protected console. A single `dem-conductor` image serves everything on 8420.

5. **Connection-test + status endpoints** ([verify.py](conductor/src/conductor/verify.py)) power the
   wizard. Claude test uses the cheapest model (`claude-haiku-4-5`, `max_tokens=4`); auth header
   differs by credential — API key → `x-api-key`; OAuth subscription token → `Authorization: Bearer`
   **plus** `anthropic-beta: oauth-2025-04-20`.

6. **Bundled observability/Plane compose profiles dropped** for this deployment (see above).

## Instance-specific config (already provisioned by the user)

- Plane: self-hosted **Community** edition at `PLANE_BASE_URL=https://plane.eroizzy.com` (NOT
  api.plane.so). One workspace **DEM** (slug `dem`), many projects (one per repo). First project is
  **chessbro**. Epic detected by an `epic` **label** (CE has no Epic work-item type).
- `CONDUCTOR_PUBLIC_URL=https://dem.eroizzy.com`; webhook paths `/webhooks/plane`, `/webhooks/github`.
- Claude auth: subscription token (`CLAUDE_CODE_OAUTH_TOKEN`).

## Roadmap (high level)

Phase 1 (conductor skeleton) — **done, merged**. Phase 2a (config & secret store + wizard backend)
— **in progress**. Then Phase 2b (Plane client + webhook + project/state mapping tables), Phase 2-UI
(Streamlit admin console + setup wizard), then Phases 3–7 per `docs/PLAN.md` (GitHub → agent image
→ four roles + review loop → observability → docs/release), adjusted for the deviations above.

## ▶ Current build status & how to resume

**Read [`docs/HANDOFF.md`](docs/HANDOFF.md) at the start of every session** — it holds the live
status: exactly what's built, what's left, which branch, and the numbered "resume here" steps. Keep
it updated as work progresses; it's the transient companion to this durable guide.
