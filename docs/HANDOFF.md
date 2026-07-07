# Build handoff — live status

> Transient companion to [`../CLAUDE.md`](../CLAUDE.md). Read this at session start; update it as
> work progresses; trim finished detail once a phase merges.

**Last updated:** Phase 2b built on branch `feat/phase-2b-plane` (PR open). Next: Phase 2-UI.
**Active branch:** `feat/phase-2b-plane` (off `main` @ `f50132d`).

## Phase 1 — DONE (merged, PR #1)
Conductor skeleton: `Job` + `Ticket` models, async engine, Alembic initial migration
(`jobs`, `tickets`), FastAPI app factory + lifespan, `/health`, `/metrics`, `targets.yml` loader,
CI (ruff/mypy/pytest). (`config.py` was refactored in Phase 2a.)

## Phase 2a — DONE (merged, PR #2 — config & secret store + setup-wizard backend)
Env `BootstrapSettings` + DB-backed application config. `Secret` (Fernet-encrypted) + `Setting`
(plain) tables; DB is the source of truth, env/YAML seed it once (`RESEED_FROM_ENV` re-imports).
Modules: `crypto.py` (`SecretBox` + passphrase bundle), `catalog.py` (ConfigField registry, wizard
steps, `validate_config`/`step_status`), `store.py` (`ConfigStore`), `verify.py` (live Claude/Plane/
GitHub tests), `api/config.py` (management router). App boots with no app config; gaps reported via
`GET /api/config/status`, not a hard fail. Migration `bf64e81c963c` (`secrets`+`settings`).
**Key patterns to reuse in 2b:** creds come from `store.resolved()` (dict keyed by catalog `name`,
e.g. `plane_base_url`/`plane_api_key`/`plane_workspace_slug`/`plane_webhook_secret`), not env;
httpx clients take an injectable `client=` param for `MockTransport` tests (see `verify.py`);
`ConfigStore` lives on `request.app.state.store`.

## Phase 2b — DONE (branch `feat/phase-2b-plane`, PR open) — Plane integration
Signed Plane webhook for an epic-labelled issue → verified, deduped `Job`; conductor can
read/comment/transition Plane issues; DB-backed project↔repo + canonical-state↔Plane-state maps.

**Confirmed webhook signing scheme** (from Plane source `apps/api/plane/bgtasks/webhook_task.py`
+ `IssueExpandSerializer`, not from memory):
- Headers: `X-Plane-Delivery` (uuid, per-attempt → idempotency key), `X-Plane-Event`
  (`issue`, `issue_comment`, …), `X-Plane-Signature` (hex).
- Signature = `HMAC-SHA256(secret, raw_body).hexdigest()`, timing-safe. Plane signs
  `json.dumps(payload)` == the wire body, so **verify over the raw request body**.
- Envelope: `{event, action(create|update|delete), webhook_id, workspace_id, workspace_slug,
  data, activity}`. Issue `data` (fields `__all__`): `id`, `project` (**UUID**), `workspace`,
  `name`, `state` (nested `{id,name,color,group}`), `labels` (**list of UUIDs**), `assignees`.
  **Labels arrive as UUIDs, not names** → epic detection resolves label names via
  `PlaneClient.list_labels` and matches `name == "epic"`.

**Built:**
- `plane.py` — `PlaneClient` (httpx, `X-API-Key`, injectable `client=`): `get_issue`,
  `create_issue`, `post_comment`, `set_state`, `list_states`, `list_labels`; `PlaneError`;
  `client_from_resolved(resolved)`. Endpoints confirmed against Plane API v1 url modules
  (`/api/v1/workspaces/{slug}/projects/{pid}/issues|states|labels/`).
- `models.py` — `WorkflowState` enum (Backlog→Ready-for-dev→In-progress→In-review→
  Changes-requested→Ready-for-approval→Done), `ProjectMapping` (PK `plane_project_id`→repo,
  base_branch), `StateMapping` (unique `project_id`+`workflow_state`→`plane_state_id`). Migration
  `22f2c570f81f`.
- **BigInteger-PK fix (latent Phase-1 bug):** a `BigInteger` PK is not a rowid alias on SQLite
  (the default backend) so it never autoincrements → Job inserts raised `NOT NULL … jobs.id`. Now
  `BigInteger().with_variant(Integer, "sqlite")` on `Job.id` + `StateMapping.id`, in both models
  and migration DDL (initial + new). Safe to amend the initial migration — nothing deployed yet.
- `mappings.py` — `MappingStore` (project + state CRUD, `import_targets` seed-once). `api/webhooks.py`
  — `POST /webhooks/plane` (raw-body HMAC → 401 on bad/missing; epic check; `ProjectMapping`
  resolve; delivery-id-deduped `Job`). `api/mappings.py` — CRUD + live `state-scan`. Wired into
  `main.py` (`app.state.mappings`; seeds `targets_file` on boot). `config.py` gained `targets_file`.
- `targets.py` demoted: still the yml parser, now feeding `MappingStore.import_targets` (DB wins).
- Tests: `test_plane.py`, `test_mappings.py`, `test_webhooks.py` (valid sig → Job; bad/missing →
  401; replayed delivery → no dup). **71 passed; ruff + mypy --strict green.** Migration chain
  applies on fresh sqlite and Job insert autoincrements.

**Still pending live end-to-end** (needs the user's real Plane creds, see below): confirm the
signature matches a genuine Plane delivery and that `list_labels`/`state-scan` hit the live API.

## Phase 2-UI — Streamlit admin console
Setup wizard (steps from the catalog, live key verification, resumable via `/api/config/status`),
secrets/settings management, repo↔project connect + state scan/mapping, `targets.yml` seed-import.
Behind auth (Cloudflare Access for barad-dur; Streamlit-native login as the portable default).

## Phases 3–7 (per docs/PLAN.md, adjusted per CLAUDE.md deviations)
GitHub client + webhook → agent image & dispatcher → four agent roles + review loop/state machine →
observability wired to the existing barad-dur stack → docs/packaging/release.

## Pending from the user
- **For Phase 2b live-testing:** Plane PAT (`PLANE_API_KEY`) + `PLANE_WEBHOOK_SECRET` from a real
  Plane webhook. Unit tests can synthesize signatures, but an end-to-end check needs these.
- **Later phases:** GitHub machine-account PAT + webhook secret, barad-dur otel-collector host:port,
  ntfy/Slack notify target.
- A `DEM_SECRET_KEY` (Fernet) is required for any real run (generate per `.env.example`).
