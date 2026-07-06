# Build handoff — live status

> Transient companion to [`../CLAUDE.md`](../CLAUDE.md). Read this at session start; update it as
> work progresses; trim finished detail once a phase merges.

**Last updated:** Phase 2a complete — toolchain green, app boots, PR opening.
**Active branch:** `feat/phase-2a-config-secret-store`.

## Phase 1 — DONE (merged, PR #1 on `main`)
Conductor skeleton: `Job` + `Ticket` models, async engine, Alembic initial migration
(`jobs`, `tickets`), FastAPI app factory + lifespan, `/health`, `/metrics`, `targets.yml` loader,
`.env.example`, CI (ruff/mypy/pytest), tests. (`config.py` has since been refactored in Phase 2a.)

## Phase 2a — DONE (config & secret store + setup-wizard backend)

All items below built and verified: ruff + mypy --strict + pytest (47) green; migration chain
(`f4ff4cd58b95 → bf64e81c963c`) applies cleanly (`jobs, tickets, secrets, settings`); app boots
with **no** app config and `/api/config/status` reports per-step completeness (200). PR opening.

### Built
- `conductor/src/conductor/crypto.py` — `SecretBox` (Fernet encrypt/decrypt), `generate_key`,
  passphrase-based `encrypt_bundle`/`decrypt_bundle` (portable config export).
- `conductor/src/conductor/catalog.py` — declarative `ConfigField` registry with wizard **steps**
  (Claude/Plane/GitHub/Notifications/Advanced), `required` flags, `validate_config()`,
  `step_status()` (drives wizard resumability).
- `conductor/src/conductor/config.py` — refactored to `BootstrapSettings` (env-only: `DEM_SECRET_KEY`,
  `DATABASE_URL`, host/port, `RESEED_FROM_ENV`, `CONFIG_SEED_FILE`; validates the Fernet key).
- `conductor/src/conductor/models.py` — added `Secret` (encrypted) + `Setting` (plain) tables.
- `conductor/src/conductor/store.py` — `ConfigStore`: set/get secret+setting, `resolved()`,
  `seed_from_env()` (seed-once unless reseed), masked `list_config()`, `status()`, `export_env()`,
  `export_bundle()`/`import_bundle()`.
- `conductor/src/conductor/verify.py` — `verify_claude`/`verify_plane`/`verify_github` (httpx,
  injectable client for tests). Claude test: `claude-haiku-4-5`, `max_tokens=4`; OAuth token →
  `Authorization: Bearer` + `anthropic-beta: oauth-2025-04-20`; API key → `x-api-key`.
- `conductor/pyproject.toml` — added `cryptography`, `types-pyyaml`.
- `conductor/src/conductor/api/__init__.py` — empty package created.

- `conductor/src/conductor/api/config.py` — management router (`GET /api/config`, `GET
  /api/config/status`, `PUT /secret/{name}`, `PUT /setting/{name}`, `POST /test/{service}`, `GET
  /export.env`, `POST /export-bundle`, `POST /import-bundle`), pulling `ConfigStore` from
  `app.state.store`.
- `main.py` — lifespan builds `SecretBox` + `ConfigStore` on `app.state.store`, seeds from
  env/YAML (`_seed_env`), logs incomplete-config issues (non-fatal), includes the config router.
  Phase 1's hard auth-XOR fail-fast is gone — reported via `catalog.validate_config`, not fatal.
- Alembic migration `bf64e81c963c_config_store` (`secrets` + `settings`).
- Tests rewritten/added: `test_config.py` (BootstrapSettings), `test_crypto.py`, `test_catalog.py`,
  `test_store.py`, `test_verify.py` (httpx `MockTransport`), `test_api.py`; conftest gained
  `secret_key`/`box`/`sessionmaker`/`store` fixtures + reworked `make_env`.
- `.env.example` — added bootstrap section (`DEM_SECRET_KEY`, `RESEED_FROM_ENV`, `CONFIG_SEED_FILE`)
  and the "first-boot seed; DB wins after" note; corrected the stale Claude hard-fail comment.

## Phase 2b — NEXT (Plane integration)
Typed Plane client (read issue/epic, create issues, post comments, set state, **scan states**);
`POST /webhooks/plane` with HMAC verification + delivery-id idempotency → verified deduped `Job` row;
DB tables `ProjectMapping` (project↔repo) + `StateMapping` (canonical→Plane state) + management API.
Acceptance: Plane issue event → verified deduped Job row; comment posting works; unsigned webhook → 401.

## Phase 2-UI — Streamlit admin console
Setup wizard (steps from the catalog, live key verification, resumable via `/api/config/status`),
secrets/settings management, repo↔project connect + state scan/mapping, `targets.yml` seed-import.
Behind auth (Cloudflare Access for barad-dur; Streamlit-native login as the portable default).

## Phases 3–7 (per docs/PLAN.md, adjusted per CLAUDE.md deviations)
GitHub client + webhook → agent image & dispatcher → four agent roles + review loop/state machine →
observability wired to the existing barad-dur stack → docs/packaging/release.

## Pending from the user (not blocking Phase 2a)
Plane PAT, GitHub machine-account PAT + webhook secret, the barad-dur otel-collector host:port,
ntfy/Slack notify target. And they must generate a `DEM_SECRET_KEY` (Fernet) for any real run.
