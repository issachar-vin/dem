# Build handoff — live status

> Transient companion to [`../CLAUDE.md`](../CLAUDE.md). Read this at session start; update it as
> work progresses; trim finished detail once a phase merges. Durable detail lives in the code and
> `docs/PLAN.md`; this file is state + decisions, not a changelog.

**Status (VERSION 0.4.2):** **Phases 1–3 DONE & merged. Phase 4 nearly done — Part 1 (agent image,
PR #39) & Part 2 (dispatcher + volumes + contracts, PR #40) DONE & merged; Part 3 (scheduler + Job
consumer) DONE, PR #41 open.** The conductor now consumes intake `Job`s: the scheduler dispatches an
engineer container for a ticket entering `ready_for_dev`. **Next is Phase 5** (the real role prompts
+ review loop), which also closes the still-open Phase-3 acceptance item ("merged PR → cleanup job
*runs*") — no ticket carries a `pr_number` until Phase 5 creates PRs.

`agent/` image (Part 1): `Dockerfile` + `entrypoint.sh` + `seed-claude.json` + `make agent-smoke`,
accepted live (containerized `claude -p`, same-`session_id` resume, kill-on-timeout).

`conductor/agents/` package (Part 2): `dockerctl.py` (Docker surface behind Protocols +
`run_container` with `ContainerFailed`/`ContainerTimeout`→kill), `dispatcher.py` (`docker run`
construction, per-role `asyncio.Semaphore`, envelope parse), `volumes.py` (`VolumeManager.prepare/
destroy` — clone + `ticket/<id>` branch + git identity + carry-in chown fix + token stripped from
the stored remote), `contracts.py` (Pydantic plan/result/verdict + `MalformedAgentOutput`),
`roles.py`; plus `github.get_user()` for derive-from-token authorship.

Scheduler (Part 3, PR #41): `scheduler.py` worker loop (start/stop mirroring `poller.py`, wired into
the lifespan) selects the next queued **engineer** job — **in-flight-first then oldest-created**,
`_is_blocked` is a **Phase-5 seam** (returns False; the planner doesn't create Plane blocking
relations until Phase 5, and the CE relations endpoint must be validated live first) — claims it
atomically (`jobs.claim_job`/`complete_job`), then drives get-or-create ticket → `prepare` →
`Dispatcher.run(engineer, **placeholder prompt**)` → record `session_id` → ticket `in_review` → job
`done`; failure → job `failed` + ticket `error`. `tickets.py` = small `TicketStore`. Non-engineer
triggers (planner, GitHub) are left **queued** for Phase 5.

**Deploy note (live-tested on barad-dur):** the conductor container must (1) **mount the host Docker
socket** (`- /var/run/docker.sock:/var/run/docker.sock` in the DEM Portainer stack — added; a missing
mount is a `FileNotFoundError` on dispatch, the scheduler runs as root so no docker-group setup is
needed), and (2) use an `agent_image` that host can pull. `release.yml` now publishes
`ghcr.io/<owner>/dem-agent` (`:latest`/`:<version>`/`:sha`) alongside the conductor, so set the
console **Advanced → `agent_image`** to `ghcr.io/issachar-vin/dem-agent:latest` (the catalog default
`dem-agent:latest` is only the locally-built `make agent-build` tag). Each dispatch leaves
`psa-*-<id>` volumes behind (cleanup is Phase 5) and spends tokens. The clone helper runs the agent
image with `entrypoint=["bash","-c"]` to **bypass** the agent entrypoint's Claude-credential
assertion (it clones only, no creds) — PR #43; the engineer dispatch keeps the entrypoint.

---

## ▶ RESUME: Phase 5 — the four role prompts & review loop

Authoritative spec: `docs/PLAN.md` → **Phase 5** (+ the loop mechanics in **"Work intake, ordering &
concurrency"**). Part 3's scheduler is the consumer this builds on — it already dispatches the
engineer with a *placeholder* prompt; Phase 5 makes it real. Roughly:

- **Prompt files** `agent/prompts/{planner,engineer,reviewer,qa}.md` with explicit output contracts
  (the Pydantic models in `agents/contracts.py` already define the shapes).
- **Engineer**: replace the placeholder prompt with the real one (ticket body + criteria, findings
  JSON on resume); conductor does push + PR creation afterward (credentialed step stays out of the
  agent) → ticket `in_review`.
- **Reviewer + QA**: run after PR creation, post findings as **ticket comments**, verdict JSON parsed
  via `contracts.parse_verdict` (this is where the **re-prompt-once** `MalformedAgentOutput` policy
  gets exercised); any fail → `changes_requested`, `loop_round++`, diff-hash **stall check**, resume
  the engineer (`claude -p --resume <engineer_session_id>` — already stored on the ticket) → re-run.
  Both pass → `ready_for_approval` + notify; **no auto-merge**.
- **Planner**: consume the `trigger=="planner"` jobs Part 3 leaves queued — decompose the epic, create
  Plane issues, set blocking relationships (then **wire `_is_blocked`** in `scheduler.py`), drop into
  `ready_for_dev`.
- **Cleanup**: merged-PR job → `VolumeManager.destroy` (closes the open Phase-3 acceptance item).

**Decided for Phase 4 — git commit authorship (do NOT add manual name/email fields).** Derive
`user.name`/`user.email` from `GET /user` on the existing `github_token` (returns `login`, `name`,
`id`) — works for a personal or bot account, zero extra config. Email: public email if set, else the
GitHub **noreply** address `{id}+{login}@users.noreply.github.com` (private accounts return
`email: null`; noreply is what GitHub attributes). Optional manual override is fine; default is
derive-from-token.

---

## Key architecture & design decisions (durable — do not relitigate)

**Stack & data**
- **SQLite + NiceGUI, one process/container.** NiceGUI mounts *into* the conductor's FastAPI app
  (`ui.run_with`), so UI + webhooks + DB share a process; pages call the stores in-process. The
  mounted NiceGUI sub-app does **not** share the parent `app.state`, so the UI reaches stores/
  sessionmaker via the module-level **`ui.context` singleton** (populated in the lifespan). UI is
  split into `ui/{shell,widgets,wizard,pages,auth}.py`. Console pages: **wizard `/`, config (tabs +
  a Migration tab), projects, states, jobs**.
- **Stay SQLite + NiceGUI. Escape hatches are Postgres and single JS-islands — NOT Mongo/React.**
  The data is relational and we rely on it (partial-unique dedupe index, FKs, unique constraints);
  SQLite gives zero-ops / spin-up-anywhere. If writes ever exceed the single writer → **Postgres**
  via `DATABASE_URL` (same SQLAlchemy code). If one page needs a rich widget (node-graph canvas,
  resizable grid) → embed **one JS island on that page**. Mongo is a mismatch (relational data; we
  already store JSON docs in SQLite JSON columns); a React console is overkill (re-introduces the
  HTTP API split deliberately deleted in deviation #4). Confirmed this session after `ui.table`
  proved NiceGUI handles a real data table.
- **Config split** (deviation #2): `BootstrapSettings` (env-only, the minimum before the DB is
  reachable) + DB-backed `Secret` (Fernet) / `Setting`. DB is source of truth; env/YAML seed once
  (`RESEED_FROM_ENV` re-imports). `DEM_SECRET_KEY` is the Fernet root of trust — can't live in the
  DB it decrypts. Auth: argon2 creds in the DB + a NiceGUI session cookie; Cloudflare Access fronts
  barad-dur as an outer layer.

**Multi-repo & webhooks**
- **One Plane project → many repos** (deviation #1): `RepoMapping` (project_id, repo key, github_repo,
  base_branch); the planner assigns each ticket exactly one repo key. Each project owns **one**
  encrypted webhook secret shared by its repos (deviation #7). Base branch is **per-repo** only (the
  old global `github_base_branch` was vestigial and removed).
- **GitHub webhook = verify-after-lookup:** parse the unverified `repository.full_name` → find the
  owning project → HMAC the raw body against **that project's** secret (`X-Hub-Signature-256`).
  Per-project secrets, no global fallback. Unmapped repo / missing / wrong secret → 401.
- **Plane fires one webhook per changed field.** The `activity` block (`field`/`old_value`/
  `new_value`) is the precise signal — dragging a card into a column emits both a `state_id` and a
  `sort_order` event carrying the same current `data.state`. The engineer triggers **only on the
  `state_id` transition into `ready_for_dev`** (or an issue *created* directly in it, for
  planner-created tickets); incidental edits are ignored (not queued, not stored). Epic detection
  resolves label UUID → name via `list_labels` (`plane_epic_signal`: `label` default / `parentless`).

**Intake / Jobs**
- **Intake is decoupled from pipeline shape.** `jobs.enqueue_job` is the single choke point for
  webhooks + poll; it enqueues Jobs and **never encodes transitions** → adding a pipeline step is a
  state-machine concern, not an intake one. Deliberate groundwork for the future dynamic-workflow
  editor.
- **Two dedupe layers.** `delivery_id` (unique column) rejects a literal re-delivery; `dedupe_key`
  `<project>:<issue>` gives semantic dedupe (one *active* Job per key), backstopped by the
  partial-unique index **`ix_jobs_active_dedupe`** on `(source, dedupe_key) WHERE status active` so
  concurrent same-issue deliveries can't double-enqueue. `raw_payloads` (JSON list) caches every raw
  delivery that folded into a Job — for audit, shown in the Jobs-page info modal. Routing lives in
  `Job.payload` JSON (`project_id`, `issue_id`, `trigger`), not columns.
- **SQLite PRAGMAs** (`journal_mode=WAL`, `busy_timeout`, `foreign_keys=ON`) set per-connection in
  `db.py` — required for overlapping webhook/poll/status writes.
- **`LOG_LEVEL`** (default INFO) raises **only the conductor logger**; `DEBUG` surfaces raw webhook
  payloads via `_log_delivery` without aiosqlite/sqlalchemy flooding the logs. Every webhook 4XX
  carries a field-level reason in the body *and* a `logger.warning`.

**Deployment (barad-dur)** — Portainer stack lives in the **`eroizzy-env`** repo
(`Barad-dur/Portainer/DEM/`), public at **dem.eroizzy.com** via Caddy + Cloudflare. A single
**`dem-conductor`** image (GHCR `:latest`, built by `release.yml` on push to `main`) serves UI +
`/webhooks/*` + `/health` on **8420** (host **:8440**). **Webhook mode.** SQLite at
`/data/conductor.db` in a named volume; `DEM_SECRET_KEY` + `DATABASE_URL` via Portainer stack env.
Redeploy after a merge to pull the new image. (If `port is already allocated` with no logs →
orphaned containers from a failed deploy: `docker rm -f dem-conductor` then redeploy.)

---

## Decided for later phases

- **Phase 5 — review feedback is captured conductor-side, agent never fetches.** Creds stay out of
  the container, so the conductor supplies review comments in the prompt. Preferred mechanism:
  **capture comment bodies off the webhook events** (`pull_request_review`/`_review_comment`/
  `_review_thread`, Plane issue-comment events) onto the record so dispatch is a local DB read. Cost:
  must handle the comment **lifecycle** (edited/deleted, thread resolved/unresolved) + a cheap
  **reconcile fetch at dispatch** to cover missed deliveries. Finalize when Phase 5 builds the loop.
- **Future — user-configurable dynamic workflow.** Promote `WorkflowState` + transitions from a code
  enum to DB tables + a console page to reorder/add/remove pipeline steps (n8n-style *ordered list*,
  not a free-form canvas — stays NiceGUI). Real lift is the states-as-data backend model; intake is
  already decoupled so this stays additive. Write-up: `docs/PLAN.md` → "Out of scope for v1".

## Pending from the user
- **Phase 6:** barad-dur otel-collector host:port; ntfy/Slack notify target.
- Phase 3 acceptance is done live except "merged PR → cleanup job *runs*", which needs the Phase 4
  consumer.

<details><summary>Done &amp; merged — one-line provenance</summary>

- **Phase 1** (PR #1) — conductor skeleton: `Job`/`Ticket` models, async engine, Alembic, FastAPI
  app factory + lifespan, `/health`, `/metrics`, CI.
- **Phase 2** — DB-backed config/secret store (2a), Plane client + webhook + mapping tables (2b),
  admin console + setup wizard (originally Streamlit, **migrated to NiceGUI mid-phase** — PR #11 —
  which deleted the `console/` package and the whole `/api/*` management layer, deviation #4),
  DB-backed argon2 auth, plus several wizard-polish PRs. **Fully accepted live, 2026-07-09.**
- **Phase 3** — GitHub integration & multi-repo wizard (steps 1–7, PRs #21–#29): multi-repo schema,
  Pydantic boundary models + `ui/` split, live repo/project listing, per-project wizard UI,
  bidirectional `targets.yml`, GitHub webhook + poll **intake** (PR #28), and `docs/SETUP_GITHUB.md`.
  Then the live-acceptance fixes/additions (PRs #30–#38), all folded into the decisions above:
  non-JSON→400, NiceGUI-3 upload API + tabbed config, 4XX diagnostics + Plane `state` hardening +
  drop global base-branch, dedupe backstop index, webhook delivery logging, **Jobs page + payload
  cache** (`ui.table`, full-width), and the **state_id-transition trigger**.

</details>
