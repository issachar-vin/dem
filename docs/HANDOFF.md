# Build handoff — live status

> Transient companion to [`../CLAUDE.md`](../CLAUDE.md). Read this at session start; update it as
> work progresses; trim finished detail once a phase merges. Durable detail lives in the code and
> `docs/PLAN.md`; this file is state + decisions, not a changelog.

**Status (VERSION 0.5.9):** **Phases 1–5 DONE & merged; live-acceptance hardening in progress.** The
full pipeline (planner → engineer → reviewer/QA loop → ready_for_approval → human merge → cleanup) is
wired and merged (Phase 5 PRs #51–#54). Live acceptance on barad-dur then drove: **PR #55 (merged)** —
empty-diff/needs-input parking, clearer GitHub 422s, console stop-job; **PR #56 (merged)** —
agent-run output capture + console log viewer; **PR #57 (merged)** — job-delete cascade, readable log
viewer, EDT/EST display; **PR #58 (merged)** — live agent monitoring, agent write-permission fix,
`blocked` state; **PR #59 (merged)** — verdict JSON parsing fix + review UX; **PR #60 (open, 0.5.9)** —
resume-parked-ticket-with-memory, and (still being added to the same PR) the event timeline + job
failure reasons + multi-repo router (below). Phases 1–4 provenance (PRs #39–#50) in the fold below.

**PR #60 (open, VERSION 0.5.9) — in progress, multiple parts on one PR:**
- **Resume parked ticket with memory (done):** a parked ticket in a resumable status
  (`awaiting_human`/`no_changes`) with a saved `engineer_session_id` and surviving `psa-repo-*` +
  `psa-claude-*` volumes, moved back to `ready_for_dev`, now **resumes** its Claude session
  (`--resume`) over the existing volumes instead of wiping + re-cloning + rescanning. The human's
  answer is pulled from the Plane comment thread (`PlaneClient.list_comments`) into a new
  `engineer_resume` prompt. Falls back to a fresh build if the volumes were pruned
  (`VolumeManager.has_session` gates it). `_build(ctx, resume=…)` carries both paths.
- **Still to add on this PR:** (a) **event timeline** — conductor pipeline steps (prep repos,
  determined repos, opened PR, review passed) recorded and interleaved with agent runs in the run
  window; (b) **job failure reasons** — surface `Job.error` on hover of the failed badge in the jobs
  table + in the job live view; (c) **multi-repo smart router** — text-only router agent (ticket +
  repo catalog → repo keys; no GitHub access) + repo descriptions + multi-PR-per-ticket machinery.
**Next up:** the **multi-repo smart router** (decided) — a text-only router agent (ticket + repo
catalog → JSON list of repo keys; no GitHub access, conductor still does the credentialed clone),
an optional repo **description** field for routing, and the **multi-PR-per-ticket** machinery a
cross-repo ticket needs (`Ticket.pr_number` → many; review loop / cleanup / webhook routing per PR).
Then Phase 6 (observability) — still waiting on the otel-collector host:port and ntfy/Slack target.

**Live-run fixes (PR #59, open, VERSION 0.5.8):** first real epic run surfaced a parser bug that
looked like three bugs. The **reviewer passed** (`{"pass": true, "findings": []}`) but wrapped it in
a ```json fence with prose; `parse_verdict`'s bare `json.loads` failed → `MalformedAgentOutput` → job
**failed**, ticket stuck at **in_review**, no ready_for_approval, no PR comment. Fixes: (1)
`contracts._extract_json` strips a code fence / grabs the outermost `{…}` before parsing **verdicts
and plans** (verified against the two real reviewer outputs — both now pass); (2) the **PR link is
posted as a Plane comment** at PR creation, before review; (3) an **unscorable verdict after a PR
exists parks** the ticket `review_unscored` → `blocked` with the PR link and completes the job `done`
(no longer fails + discards the PR); (4) console log modal: **no idle flicker** (re-renders only when
runs change), **sticky-bottom** scroll (follows the tail, restores position if scrolled up), result
string **word-wraps**. The engineer's write-permission fix (#58) is confirmed working live — the
engineer wrote the README. **Not browser-verified:** the flicker/scroll/wrap behavior.

**Console + pipeline hardening (PR #58, open, VERSION 0.5.7):** first real epic run exposed three
things. (1) **Live agent monitoring** — `agent_runs` now carries `status` (running/done/failed) +
`job_id` (migration `d5e6f7a8b9c0`); runs are scoped/deleted **by job**, not ticket (a re-triggered
ticket no longer collides with an earlier job's logs). `run_container` gained an `on_output` path that
*follows* the container stdout live and batches appends every ~1s; the dispatcher does
`start_run → stream → finish_run`, and the console log modal auto-refreshes every 2s while a run is
`running` (● LIVE badge + partial transcript), stopping on close. The stream is the same one Phase 6
will pipe to Grafana/Loki. (2) **Agent write-permission fix** — headless `claude -p` never entered
bypass mode, so every Write/Edit/Bash-write blocked forever; added `--dangerously-skip-permissions`
(safe: throwaway per-ticket container, non-root `agent`, image already seeds
`bypassPermissionsModeAccepted`). This **supersedes** the earlier "no --dangerously-skip-permissions
needed" finding under PR #55 — that held for Edit on existing files but not for Write. (3) **`blocked`
WorkflowState** — parked (needs-input / no-changes) tickets now mirror onto a Plane "blocked" column;
the state auto-appears in the console mapping form (optional — board-mirroring skips it if unmapped).
The engineer prompt also now tells the agent to raise a hard environmental blocker early via
`NEEDS_INPUT` instead of grinding through workarounds. **Not yet browser/live-verified** — the
NiceGUI auto-refresh and the permission fix need this image deployed.

**Phase 5 Part 4 — merged-PR cleanup (PR #54, open):** the scheduler now selects `source="github"`
jobs (previously filtered out by `_select_job`'s `source=="plane"` clause). `_run_cleanup`: a
**merged** PR (`merged==True` + `pr_number`) → find the ticket by reconstructed `pr_url`
(`https://github.com/<repo>/pull/<n>`, exact so cross-repo PR-number collisions can't clean the wrong
ticket) → mark it **`done`** (the transition that **releases dependents** from Part 3's blocking gate)
→ `VolumeManager.destroy` → job done. Every other GitHub PR delivery (opened, review, comment,
unmerged close) has no consumer today and is **drained** (marked done) so the queue can't grow
unbounded. Closes the long-open Phase-3 acceptance item "merged PR → cleanup job *runs*". VERSION
0.5.3.

**Phase 5 Part 3 — planner (PR #53 merged):** consumes the `trigger=="planner"` jobs (previously left
queued). New `planner.md` (epic + the project's repos cloned read-only under `/work/<key>` →
`Plan` contract). `VolumeManager.prepare_planner()` clones each project repo into `/work/<key>`
(credentialed root helper, token-stripped, no ticket branch). New `Ticket.target_repo` +
`Ticket.blocked_by` columns (migration `a6b7c8d9e0f1`). `_run_planner`: clone → dispatch planner →
create a Plane issue per ticket in `ready_for_dev` (fires the engineer webhook) → pre-create the
local `Ticket` with `target_repo` + resolved `blocked_by` (plan keys → real issue ids). Engineer
dispatch routes off `target_repo` (falls back to the first mapped repo). `_is_blocked` **wired** off
the local graph: queued until every blocker is `done`. **Two scoping calls:** (1) **no Plane-side
relations call** — no validated CE relations endpoint, so build order is enforced by the local
`blocked_by` graph; board surfacing deferred; (2) `_is_blocked` releases when blockers are `done` —
the merge→`done` transition is **Part 4**, so dependent tickets correctly wait until then. VERSION
0.5.2.

**Phase 5 Part 2 — reviewer/QA review loop (PR #52, open):** the whole build→review→resume loop runs
**synchronously within one engineer job** (matches the existing scheduler shape + the
`MAX_CONCURRENT_AGENTS=1` default). New prompts `reviewer.md` / `qa.md` / `engineer_followup.md`
(all use the `Verdict` contract). `Dispatcher.run_parsed(run, parse)` owns the **re-prompt-once**
policy (malformed output → resume the same session for valid JSON → still bad raises → ticket parked
at `error`). `VolumeManager.diff_hash()` = `sha256(git diff <base>...HEAD)` for the stall detector.
After PR creation the scheduler loops reviewer→QA (sequential): **both pass** → `ready_for_approval`
+ notify with PR link, no auto-merge; **any fail** → findings posted as a **Plane ticket comment**,
`loop_round++`, `changes_requested`, resume the engineer with the findings, re-push, re-review;
**identical diff two rounds running** → `stalled` + notify. New `notify.py` = best-effort
ntfy/slack/webhook/none sender (a down notifier never fails a ticket; full observability is Phase 6).
`_run_engineer` refactored to drive a `_Pipeline` context through `_build` → `_review_loop`. VERSION
0.5.1.

**Phase 5 Part 1 — engineer agent real (PR #51, merged):** real engineer prompt + `prompts.render()`
loader; after the container commits, `VolumeManager.push()` (credentialed root helper, token from
`$CLONE_TOKEN`, remote token-stripped) → `github.create_pull_request()` (base = ticket repo
`base_branch`) → `tickets.set_pr()`, and only then `in_review`; a push/PR failure → job `failed` +
ticket `error`. **Deviation from PLAN:** prompt templates live under the **conductor** package
(`conductor/src/conductor/prompts/`), not `agent/prompts/` — the conductor assembles the full prompt
string and passes it to `claude -p` (agent stays credential-free), and only the conductor image
bundles `src/`; the built wheel includes the `.md` (hatchling package-data default, verified).

**Nav icon states (PR #49, merged):** follow-up polish on PR #48's collapsed sidebar. Active page no
longer shows a background pill when the drawer is collapsed — only the orange icon signals it (the
pill only makes sense next to a label, which mini mode hides). Hovering another icon in collapsed
mode also shows no background (Quasar renders hover as a separate `.q-focus-helper` overlay, not
the item's own `background-color` — neutralized directly in `kit.py`). Inactive icons darkened
(`MUTED` → `FAINT`); hovering a non-active icon now lights it white via a `!important` CSS rule
(icon color is set inline per item so it degrades correctly with no CSS, which is why hover needs
`!important` to win); the active icon stays orange regardless of hover. VERSION 0.4.10.

**Console redesign (PR #48, merged):** the whole console rebuilt to `docs/UI_DESIGN.md` ("Modern
Dark Developer SaaS" — Linear/Vercel-style) and the **original UI removed**; the redesigned pages
serve the root paths. `ui/kit.py` is now the full design kit (tokens, global stylesheet, **Lucide**
webfont for interface chrome — FA + Material Symbols stay loaded so stored `fa:`/`ms:` icon-picker
specs keep rendering — buttons/cards/pills/tiles/kebab/dialog helpers), `ui/widgets.py` the form
language (labeled 48px fields, `Section` save semantics, collapsible `bubble` steps, test rows).
Every screen redone with functional parity: login + first-run admin, wizard, config + migration,
projects (the design exemplar), states, jobs. No schema changes — deploys over production data
as-is. Fixed en route: Quasar's default `color=primary` painting secondary/danger buttons orange
(`color=None` in the kit's button factory), mini-drawer icons off-center (Quasar side-section
padding), Lucide baseline drift in buttons (fixed-size flex wrapper in `licon`). VERSION 0.4.9.

**Console UI makeover (PR #47, merged):** Phase-4-closing visual pass. A shared design system
(`ui/kit.py`: tokens, Font Awesome + Inter + Material Symbols, panel/section_header/stat_tile/pill/
icon_tile/kebab/buttons); the projects page rebuilt to the mockup (project card, stat tiles, repo
rows with a ⋮ kebab, dashed add-form); **searchable icon pickers** for both repo and project icons
over a bundled 6,326-icon catalog (`ui/icons_catalog.py`), persisted via new `repo_mappings.icon` /
`project_mappings.icon` columns (migrations `d4e5…` / `e5f6…`); and **wizard↔config parity** by
sharing widgets (`github_repo_field`, `_test_row`, `_Section.model`, mode-driven field visibility).
`"other"` role dropped everywhere (identifier is a free combobox). Icons don't round-trip through
`targets.yml` yet (cosmetic). VERSION 0.4.8.

**Live-hardening + wizard (merged, PR #46):** found while running the pipeline on barad-dur —
- **Board mirroring**: the scheduler now moves the Plane card `ready_for_dev → in_progress →
  in_review` (`scheduler._set_state` → `plane.set_state`, best-effort; unmapped state / Plane error
  logs and continues; no self-trigger since only `ready_for_dev` fires a job).
- **Idempotent `prepare`**: clears any stale `psa-*-<id>` volume before cloning (a prior aborted
  dispatch otherwise blocks retries with "destination path /work already exists").
- **Orphaned-job requeue**: `scheduler.recover()` resets `RUNNING → QUEUED` on startup
  (`jobs.requeue_running`) — a redeploy mid-dispatch left a stuck job that also blocked re-triggers
  via the active-dedupe index.
- **`agent_image` is now required with no catalog default** (`catalog.DEFAULT_AGENT_IMAGE =
  ghcr.io/issachar-vin/dem-agent:latest` is the code fallback + wizard pre-fill); the wizard's
  Advanced step reads incomplete until it's set, so a wrong/blank image is caught before dispatch.
- **Wizard**: every section is a collapsible `_bubble` (check when complete, still expandable); a new
  Plane **Step 5 — Map pipeline states** surfaces the state-mapping form inline with an explanation;
  `agent_image` gets its own prominent bubble in Advanced.

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
assertion (it clones only, no creds) — PR #43; the engineer dispatch keeps the entrypoint. The clone
script also runs `git config --global --add safe.directory /work` (PR #44): the helper clones as root
into an `agent`-owned volume, which git 2.35.2+ rejects as "dubious ownership" until trusted.

**Log access:** barad-dur logs are in Loki (`http://192.168.88.204:3100`, `auth_enabled: false`,
LAN-only) with label `{container="dem-conductor"}`; this dev machine is on that LAN, so the logs are
queryable directly via Loki's `query_range` API (no Grafana token needed).

---

## ▶ RESUME: finish Phase 5 live acceptance (+ agent-run observability), then Phase 6

**Live acceptance is underway on barad-dur.** First real epic/ticket exposed and fixed a batch of
issues (PR #55, open — VERSION 0.5.4):

- **First live finding:** a "Review the README" ticket ran the engineer ~19 min, made **no commits**,
  and the pipeline hard-crashed on PR creation with a GitHub **422** ("No commits between main and
  ticket/…"). Cleared en route: **headless permission mode works** — the engineer edited/committed/
  pushed; only PR creation failed. So `claude -p` isn't blocked on Edit/Bash (no `--dangerously-skip-
  permissions` needed for that).
- **PR #55 fixes:** empty-diff guard (zero commits → post a Plane comment + park `no_changes`, no
  422); surface GitHub `errors[]` in `_error_detail`; engineer `NEEDS_INPUT: <q>` feedback channel
  (post to ticket + park `awaiting_human`); console **stop-job** button that kills a job's
  `psa-*-<ticket>` containers (job → `stopped`, ticket → `stopped`), delete also kills containers.
  `complete_job` is now compare-and-set on `running` so a manual stop isn't clobbered.

**Agent-run observability — console side (PR #56, merged, VERSION 0.5.5):** done. The agent now runs
with `claude -p --output-format stream-json --verbose` (incremental events, visible live in `docker
logs`); `contracts.parse_envelope` reads the final `type:"result"` event out of the JSONL. Each
`claude -p` run's raw output is captured via an injected `Dispatcher` `RunRecorder` and persisted to
a new **`agent_runs`** table (migration `b7c8d9e0f1a2`; tail-capped at 200K chars), keyed by ticket +
role + loop-round. The **Jobs page** got a log button → modal of that ticket's runs. **Phase 6 reuses
this stream** to build the Grafana/Loki "what is the engineer doing right now" drilldown — the console
view is the local counterpart.

**Console polish (PR #57, open, VERSION 0.5.6):** three console fixes. (1) **Cascade job delete** —
`jobs.delete_job` resolves the ticket via the payload `issue_id` and deletes that ticket's `agent_runs`
history **and** its `Ticket` row before the job (no DB FK, so the cascade is explicit in code; GitHub
jobs without `issue_id` are a no-op). (2) **Readable log viewer** — the run modal no longer dumps
event trees: `agent_runs.summarize_output()` (pure, unit-tested) turns the stream-json into sentences
(session start · assistant text · `Used <Tool>: <arg>`) shown per run with an outcome pill, and the
final `result` event renders as a **clickable indicator** (result text + turns/duration/cost) that
opens the raw JSON in a nested modal via `agent_runs.parse_events()`. (3) **EDT/EST display** — new
`conductor/localtime.py` converts every UI datetime to `America/New_York` (`%Z` → EDT/EST by date);
DB stays UTC; `tzdata` added for the slim image. **Not yet live-verified in a browser** — nested-dialog
interaction is import- and unit-verified only.

**Other deferred follow-up:** auto-resume a parked `awaiting_human` ticket when a human replies —
consume Plane **issue-comment** webhooks and `claude -p --resume` the engineer with the answer.

Also still run the **full happy-path live acceptance** — the whole pipeline has only ever been
unit-tested with fakes end-to-end:

- Seed the fixture (`tests/fixtures/demo-repo`, a tiny FastAPI todo app) or a real chessbro epic;
  drive an epic → planner → tickets → engineer → PR → reviewer/QA → ready_for_approval, merge, and
  confirm the **cleanup job runs** (volumes reclaimed, ticket `done`, dependents released).
- Validate the two Part-3/4 assumptions that only live traffic exercises: (1) an **API-created issue
  in ready_for_dev fires the engineer webhook**; (2) **merged-PR webhook/poll** delivers `merged` +
  `pr_number` as the cleanup handler expects.
- **Deferred follow-up:** surface the blocking graph on the **Plane board** (a relations API call) —
  needs the CE relations endpoint validated live first. Build order is already enforced locally via
  `Ticket.blocked_by`, so this is human-visibility polish, not correctness.

**Phase 6 (observability)** — authoritative spec `docs/PLAN.md` → Phase 6. Needs from the user
(still pending): the barad-dur **otel-collector host:port** and the **ntfy/Slack notify target**. The
agent dispatcher already emits OTel resource attributes (`agent.role`/`ticket.id`/`loop.round`) and
`notify.py` already dispatches to ntfy/slack/webhook — Phase 6 wires the conductor's own traces/
metrics/logs to the collector and fills in the notify target.

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
- **Phase 5 live acceptance** on barad-dur (see the RESUME box) — the pipeline is only fake-tested
  end-to-end. The "merged PR → cleanup job *runs*" item is now built (Part 4, PR #54); confirm it live.

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
