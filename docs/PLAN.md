# Plane SWE Agents — Build Plan & Specification

An open-source, self-hostable pipeline that turns Plane work into approved pull requests using Claude Code agents. Write an epic in Plane; a planner agent breaks it into tickets; engineer agents build each ticket in an isolated container and open PRs; reviewer and QA agents critique the work and loop feedback back to the engineer until both pass; a human approves and merges. Individual tickets can also skip the planner and go straight to an engineer — see [Work intake, ordering & concurrency](#work-intake-ordering--concurrency). The human touchpoints are exactly two: describing the work (an epic, or a single ticket) and approving the PR.

This document is the canonical spec. It lives at `docs/PLAN.md` in the repo and is written to be executable by AI coding agents (Claude Code) as well as humans — every phase has concrete deliverables and acceptance criteria.

**Design goals**
1. Runs anywhere Docker runs — a laptop, a homelab server, a cloud VM. No component assumes a specific host.
2. Shareable: `git clone`, copy `.env.example` to `.env`, fill it in, `docker compose up`. A stranger should reach a working pipeline from the README alone.
3. Solid over easy: proper service architecture (FastAPI + job queue), typed config validation, idempotent webhook handling, HMAC verification, structured JSON contracts between agents and orchestrator.
4. Safe by default: containers jail every agent, branch protection enforces human approval, secrets never touch images or version control.

---

## Architecture

```
        Plane (epic/ticket/comment events)      GitHub (PR events)
                │ webhook (HMAC)                    │ webhook (HMAC) or polling
                ▼                                   ▼
   ┌──────────────────────── conductor (FastAPI) ────────────────────────┐
   │  /webhooks/plane  /webhooks/github  /jobs  /health  /metrics        │
   │  event router → job queue → async workers → state machine           │
   │  Plane API client · GitHub client · container dispatcher            │
   └───────────────────────────────┬──────────────────────────────────────┘
                                   │ Docker API (socket or proxy)
                 ┌─────────────────┼─────────────────┐
                 ▼                 ▼                 ▼
        agent container    agent container    agent container
        planner            engineer PLANE-12  reviewer/qa PLANE-12
        (read-only clone)  (own clone volume) (same ticket volume)
                                   │ OTLP (metrics + logs)
                                   ▼
             OTel Collector → Prometheus + Loki → Grafana
             (bundled compose profile, or point at your existing stack)
```

**Components**
- **conductor** — single FastAPI service: receives webhooks, verifies signatures, routes events, runs an async job queue, dispatches agent containers, updates Plane/GitHub, owns the per-ticket state machine, exposes health + Prometheus metrics.
- **agent-runner image** — one Docker image containing Claude Code CLI + git + gh + the project toolchain. All four roles use it; roles differ only in prompt file, allowed tools, and mounted volumes.
- **Plane** — ticket system and the human UI. Cloud or self-hosted, selected purely by `PLANE_BASE_URL`.
- **GitHub** — code host. Machine account opens PRs; branch protection guarantees human approval.
- **Observability** — Claude Code's native OpenTelemetry export, shipped to a bundled or external Grafana stack.

**Agent roles and loop**
Planner (epic → tickets) → Engineer (ticket → PR) → Reviewer + QA in parallel → if either fails, findings are posted as ticket comments and fed back to the engineer via `claude -p --resume` → re-review → repeat until both pass → ticket `Ready for approval`, human merges. No iteration cap; instead a stall detector: if the engineer produces an identical diff two rounds in a row (sha256 of `git diff`), the ticket is marked `stalled` and the human is notified. By default the pipeline runs **one agent per role at a time**, so exactly one ticket is being built at any moment; the rest queue. See [Work intake, ordering & concurrency](#work-intake-ordering--concurrency) for how tickets enter, in what order they are picked up, and how blocking relationships gate them.

---

## Work intake, ordering & concurrency

Two entry points into the pipeline, both scoped to a project the conductor has mapped:

1. **Epic → planner.** An issue carrying the `epic` label (per `PLANE_EPIC_SIGNAL`) is handed to the planner, which decomposes it into independently-shippable tickets. The conductor creates those tickets in Plane and sets Plane **work-item "blocking" relationships** between them (blocker → blocked) so downstream ordering is enforced by the board itself, not just a hint. The planner's plan JSON therefore carries the blocks/blocked-by graph, not merely a flat list.
2. **Ticket → engineer (no planner).** A single issue is worked directly. The trigger is the ticket entering its mapped **`ready_for_dev`** state (canonical `WorkflowState.READY_FOR_DEV`). Backlog/draft issues are ignored until a human — or the planner — moves the card into that state. This reuses the existing per-project state mapping and gives the human an explicit "hand it to the robot" gesture. Non-epic issues that are *not* in `ready_for_dev` are ignored (no agent runs on arbitrary project noise).

Planner-created tickets are dropped into `ready_for_dev`, so they flow through the **same** engineer path a human-created ticket does — one dispatch code path serves both entry points.

**Ordering.** Work-selection priority is two-tier:

1. **Finish what's in flight first.** Tickets already past the gate — `in_progress`, `in_review`, or `changes_requested` — take priority over pulling any new ticket. Concretely, a reviewer/QA finding on an in-review ticket (which moves it to `changes_requested`) is worked *before* a fresh `ready_for_dev` ticket is picked up. This keeps a ticket moving through the engineer → review → engineer loop to completion instead of accumulating half-finished work.
2. **Then oldest new ticket.** Only when nothing is in flight does the conductor pull a new ticket from `ready_for_dev`, in **order of issue creation** (oldest first).

Before dispatching *any* ticket the conductor reads its Plane blocking relationships: a ticket **blocked by an issue that is not yet `done`** is skipped and retried on the next pass. So in-flight work outranks new work, creation order sets priority among new work, and blocking relationships gate eligibility throughout. This is what makes a planner-decomposed epic build in dependency order without the conductor inventing its own scheduler — the order lives in Plane.

**Concurrency — one agent per role.** `MAX_CONCURRENT_AGENTS=1` is the default and the supported v1 mode: at most one engineer, one reviewer, and one QA container run at a time. Consequently only one ticket is actively built at once; remaining eligible tickets wait and are picked up one-by-one, in the order above, as the active ticket clears the engineer stage. The knob exists for future parallelism, but v1 is deliberately serial — simpler reasoning, predictable cost, and no cross-ticket volume contention.

**Review loop & the human gate** (restated from *Agent roles and loop* for completeness). Engineer declares done → ticket moves to `in_review` → reviewer and QA inspect the diff/branch and post findings as **ticket comments**. Any failure moves the ticket to `changes_requested` and resumes the engineer on those comments (`claude -p --resume <engineer_session_id>`), pushing new commits to the **same** PR; re-review repeats until both pass. On pass the ticket moves to **`ready_for_approval`** and stops — **nothing is merged automatically**. A human reviews the PR and merges; branch protection on the base branch is the hard guarantee that no agent can merge on its own.

---

## Resolved decisions

Every previously open question is now closed. These are final for v1.

| Question | Decision | Rationale |
|---|---|---|
| Orchestrator | **FastAPI service ("conductor"), replacing n8n** | A shareable tool can't require users to install n8n and import workflow JSONs. One typed, tested Python service that receives webhooks directly is the solid route: versioned in the repo, unit-testable, single `docker compose up`. n8n remains possible as an *optional* downstream consumer (conductor emits its own outbound webhooks), but nothing depends on it. |
| n8n → runner transport | **Moot; conductor and runner are the same service** | The FastAPI job queue (asyncio workers + SQLite/Postgres job table) replaces both the SSH option and the separate runner. |
| Plane hosting | **Both, via config** | `PLANE_BASE_URL=https://api.plane.so` for Cloud, or your instance URL for self-hosted. Repo ships an optional compose profile that stands up Plane CE for users who want everything in one stack. |
| Ticket comments | **Plain REST comments (PAT) by default; Plane Agents beta behind a feature flag** | REST works on every edition today. `PLANE_AGENTS_ENABLED=true` upgrades to native bot activities when configured. |
| GitHub events | **Both modes, via `GITHUB_EVENT_MODE=poll\|webhook`, default `poll`** | Polling (30–120s interval) requires zero inbound exposure — essential for the laptop scenario and the safest default. Webhook mode for server deployments that can expose one path (Cloudflare Tunnel / Tailscale Funnel documented in README). |
| Claude auth | **Either subscription token or API key; exactly one, validated at startup** | Personal users bring `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`, Pro/Max). Teams/production bring `ANTHROPIC_API_KEY`. Config validation refuses to start with both or neither set, and the chosen mode is logged loudly — this prevents the silent double-billing footgun. |
| Isolation | **Docker container per agent run, fresh clone per ticket** | Locked earlier. Two named volumes per ticket: `psa-repo-<id>` (the clone) and `psa-claude-<id>` (session state for `--resume`). Destroyed on merge. |
| Iteration cap | **None; stall detector** | Locked earlier. |
| Observability | **Bundled compose profile `observability` + external mode** | Ships otel-collector, Prometheus, Loki, Grafana with pre-provisioned dashboards. Or set `OTEL_EXPORTER_OTLP_ENDPOINT` to an existing collector (e.g. Alloy) and skip the profile. |
| Job/state store | **SQLite by default, Postgres via `DATABASE_URL`** | SQLite keeps the laptop story to zero extra services; the same SQLAlchemy models run on Postgres for servers. Ticket-visible state is mirrored to Plane custom properties so the ticket remains the human source of truth. |
| Language/stack | **Python 3.12, FastAPI, SQLAlchemy, httpx, Pydantic Settings, docker SDK** | Typed config, async-native, familiar to the widest audience. |

---

## Repository structure

```
plane-swe-agents/
├── README.md                    # quickstart + full guide (spec below)
├── LICENSE                      # MIT
├── CONTRIBUTING.md
├── .env.example                 # every variable, commented, with examples (spec below)
├── .gitignore                   # .env, *.db, secrets/, __pycache__, etc.
├── docker-compose.yml           # conductor (core)
├── docker-compose.observability.yml   # profile: otel-collector, prometheus, loki, grafana
├── docker-compose.plane.yml     # profile: self-hosted Plane CE (optional)
├── Makefile                     # make setup / up / up-full / test / logs / clean-ticket T=...
├── docs/
│   ├── PLAN.md                  # this document
│   ├── SETUP_PLANE.md           # Cloud + self-hosted walkthroughs, webhook + PAT + states
│   ├── SETUP_GITHUB.md          # machine account, fine-grained PAT, branch protection, webhook/poll
│   ├── SETUP_CLAUDE_AUTH.md     # setup-token flow vs API key, billing caveats
│   ├── OBSERVABILITY.md         # bundled stack vs external collector, dashboard tour
│   ├── SECURITY.md              # threat model, secrets handling, socket proxy
│   └── RUNBOOK.md               # token rotation, recovering stalled tickets, adding repos
├── conductor/
│   ├── pyproject.toml
│   ├── Dockerfile
│   └── src/conductor/
│       ├── main.py              # FastAPI app factory, lifespan (queue workers, poller)
│       ├── config.py            # Pydantic Settings; auth XOR validation; fail-fast messages
│       ├── webhooks/            # plane.py, github.py — HMAC verify + event parsing
│       ├── events/router.py     # event → job mapping, idempotency (delivery-id dedupe)
│       ├── jobs/                # queue.py, worker.py, models.py (SQLAlchemy)
│       ├── state/machine.py     # ticket lifecycle: states, transitions, stall detection
│       ├── agents/
│       │   ├── dispatcher.py    # docker run construction: volumes, env, limits, timeout
│       │   ├── contracts.py     # Pydantic models for agent JSON output (verdicts, plans)
│       │   └── roles.py         # role → prompt file + allowed-tools + mounts
│       ├── clients/             # plane.py, github.py (httpx, typed)
│       ├── git/volumes.py       # per-ticket volume lifecycle (create/clone/branch/destroy)
│       ├── notify.py            # ntfy/Slack/webhook-out notification adapters
│       └── telemetry.py         # conductor's own OTel + /metrics
├── agent/
│   ├── Dockerfile               # node:22-slim + claude-code + git + gh + toolchain hooks
│   ├── entrypoint.sh            # asserts auth sanity, seeds ~/.claude.json, execs claude -p
│   ├── seed-claude.json         # onboarding-complete pre-seed template
│   └── prompts/
│       ├── planner.md
│       ├── engineer.md
│       ├── reviewer.md
│       └── qa.md
├── observability/
│   ├── otel-collector.yaml
│   ├── prometheus.yml
│   ├── loki-config.yaml
│   └── grafana/provisioning/    # datasources + dashboards (JSON): pipeline overview,
│                                # per-ticket drilldown, cost by role, stall alerts
└── tests/
    ├── test_config.py           # auth XOR, missing-var messages
    ├── test_webhooks.py         # HMAC accept/reject, idempotency
    ├── test_state_machine.py    # transitions, stall detection
    ├── test_contracts.py        # agent JSON parsing, malformed-output handling
    └── fixtures/                # sample Plane + GitHub payloads
```

Project-specific toolchain (what QA needs to run *your* code) is layered on the base agent image: users add a `agent/Dockerfile.project` extending `agent-runner:base` with their deps, referenced by `AGENT_IMAGE` in `.env`. The base image covers Python + Node so most projects work out of the box.

---

## `.env.example` specification

Ship exactly this file (values illustrative). Every field commented, every field with an example. Config validation in `conductor/config.py` must produce a human-readable error naming the missing/conflicting variable.

```dotenv
###############################################################################
# plane-swe-agents configuration
# Copy to .env and fill in. Never commit .env. See README for full setup docs.
###############################################################################

# ── Claude authentication ───────────────────────────────────────────────────
# Set EXACTLY ONE of the following two. The conductor refuses to start with
# both or neither (prevents silent API billing when you meant subscription).
#
# Option A — Claude Pro/Max subscription (personal use):
#   Run `claude setup-token` on a machine with a browser, paste the result.
#   Token looks like sk-ant-oat01-... and lasts ~1 year. See docs/SETUP_CLAUDE_AUTH.md
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-xxxxxxxxxxxxxxxxxxxxxxxx
#
# Option B — Anthropic API key (teams / production / pay-per-token):
#   Create at console.anthropic.com. Uncomment and comment out Option A.
# ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxx

# Models per role. Engineer benefits from the strongest model; reviewer/QA can
# run a cheaper one. Any valid Claude Code --model value.
CLAUDE_MODEL_ENGINEER=claude-sonnet-4-6
CLAUDE_MODEL_PLANNER=claude-sonnet-4-6
CLAUDE_MODEL_REVIEWER=claude-haiku-4-5
CLAUDE_MODEL_QA=claude-haiku-4-5

# ── Plane ────────────────────────────────────────────────────────────────────
# Cloud:       https://api.plane.so
# Self-hosted: https://plane.yourdomain.com (your instance's API base)
PLANE_BASE_URL=https://api.plane.so
# Personal Access Token: Plane → Profile Settings → Personal Access Tokens
PLANE_API_KEY=plane_api_xxxxxxxxxxxxxxxx
# Workspace slug — the segment in your Plane URL: app.plane.so/<slug>/...
PLANE_WORKSPACE_SLUG=my-workspace
# Project ID (UUID) the pipeline watches. Find it in project settings or via API.
PLANE_PROJECT_ID=9a28bd00-ed9c-4f5d-8be9-fc05cbb1fc57
# Webhook secret — generated by Plane when you create the webhook pointing at
# http(s)://<conductor>/webhooks/plane . Used to verify X-Plane-Signature.
PLANE_WEBHOOK_SECRET=whsec_xxxxxxxxxxxxxxxx
# How the pipeline recognizes an epic: "type" (work item type named Epic),
# "label" (label named epic), or "parentless" (any issue with no parent).
PLANE_EPIC_SIGNAL=label
# Optional: Plane Agents beta for native bot comments (default false = REST
# comments via PAT, works everywhere). Requires OAuth app + bot token.
PLANE_AGENTS_ENABLED=false
# PLANE_BOT_TOKEN=bot_xxxxxxxxxxxxxxxx

# ── GitHub ───────────────────────────────────────────────────────────────────
# Target repository, owner/name form.
GITHUB_REPO=izzy/my-project
# Fine-grained PAT for a dedicated MACHINE ACCOUNT (recommended) with
# Contents:rw, Pull requests:rw, Metadata:r on the target repo.
GITHUB_TOKEN=github_pat_xxxxxxxxxxxxxxxx
# Base branch PRs target. Protect this branch (require review) — that is the
# hard guarantee a human approves everything.
GITHUB_BASE_BRANCH=main
# Event delivery: "poll" (default; no inbound exposure needed, interval below)
# or "webhook" (requires GITHUB_WEBHOOK_SECRET and a reachable conductor URL).
GITHUB_EVENT_MODE=poll
GITHUB_POLL_INTERVAL_SECONDS=60
# GITHUB_WEBHOOK_SECRET=ghwh_xxxxxxxxxxxxxxxx

# ── Conductor service ────────────────────────────────────────────────────────
CONDUCTOR_HOST=0.0.0.0
CONDUCTOR_PORT=8420
# Public base URL of the conductor — only needed for webhook modes / Plane
# webhook target. Laptop+poll mode can leave the LAN address here.
CONDUCTOR_PUBLIC_URL=http://localhost:8420
# Job/state store. Default SQLite (file inside the conductor volume).
# For servers/teams use Postgres, e.g. postgresql+asyncpg://user:pass@db/psa
DATABASE_URL=sqlite+aiosqlite:////data/conductor.db
# Max simultaneous agent containers. Start at 1 (subscription limits + RAM).
MAX_CONCURRENT_AGENTS=1
# Hard timeout per agent run before docker kill (minutes).
AGENT_TIMEOUT_MINUTES=30

# ── Agent containers ─────────────────────────────────────────────────────────
# Image used for agent runs. Default base image; point at your project-
# extended image (see agent/Dockerfile.project) if QA needs extra toolchain.
AGENT_IMAGE=plane-swe-agents/agent-runner:latest
AGENT_MEMORY_LIMIT=4g
AGENT_CPU_LIMIT=2
# Docker access for the dispatcher: path to socket, or a socket-proxy URL
# (recommended for production; see docs/SECURITY.md).
DOCKER_HOST=unix:///var/run/docker.sock

# ── Observability (optional but recommended) ────────────────────────────────
# true = agents and conductor export OTel metrics/logs.
OTEL_ENABLED=true
# Bundled stack (docker compose --profile observability): leave as-is.
# Existing stack (Alloy/collector): point at your OTLP gRPC endpoint.
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_SERVICE_NAME=plane-swe-agents

# ── Notifications ────────────────────────────────────────────────────────────
# Where "ready for approval" and "stalled" pings go. One of: ntfy, slack,
# webhook, none.
NOTIFY_MODE=ntfy
NOTIFY_NTFY_URL=https://ntfy.sh/my-secret-topic
# NOTIFY_SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T000/B000/xxxx
# NOTIFY_WEBHOOK_URL=https://example.com/hook   # generic POST, JSON body
```

---

## Phase 1 — Repo scaffolding & conductor skeleton

Deliverables: repo tree above; `config.py` with full validation; FastAPI app with `/health`, `/metrics`; SQLAlchemy job + ticket-state models with Alembic migrations; docker-compose.yml running the conductor; Makefile; CI (GitHub Actions: lint ruff, typecheck mypy, pytest).

Acceptance: `cp .env.example .env` with dummy values → `docker compose up` → `/health` returns 200 and logs clearly state auth mode; starting with both/neither Claude credentials fails with an actionable message; `make test` green.

## Phase 2 — Plane integration

Deliverables: typed Plane client (issues CRUD, comments, states, custom properties); webhook endpoint with HMAC verification and delivery-id idempotency; state bootstrap command `make plane-init` that creates the workflow states (`Backlog → Ready for dev → In progress → In review → Changes requested → Ready for approval → Done`) and custom properties (`pr_number`, `pr_url`, `engineer_session_id`, `loop_round`, `last_diff_hash`, `agent_status`) in the target project, or prints manual instructions where the edition lacks API support (state then falls back to the conductor DB only); docs/SETUP_PLANE.md covering Cloud and self-hosted paths + webhook creation; docker-compose.plane.yml profile for users who want bundled Plane CE.

Acceptance: creating an issue in Plane produces a verified, deduplicated event row in the conductor DB; comment posting works via PAT; unsigned webhook deliveries are rejected with 401.

**Note:** at the time Phase 2 shipped, project↔repo mapping was **one project → one repo**. Phase 3 widens this to one project → many repos (see below) — the schema and UI described here were superseded, not this phase's original acceptance test.

## Phase 3 — GitHub integration & multi-repo wizard

GitHub-focused: the mapping schema and wizard UI it depends on, then the GitHub client and webhook/poll intake. Each step below is a PR-sized unit, in build order.

**Step 1 — Multi-repo mapping schema.** Project↔repo mapping becomes **one project → many repos** (deviation #1 in `CLAUDE.md`): a `RepoMapping` table, one row per (project_id, repo key), holding a short key/role (`ui`, `backend`, …), `github_repo`, and `base_branch`. `ProjectMapping` gains `enabled` (bool — a project is only worked once a human opts it in) and `webhook_secret` (encrypted, project-scoped — see step 3 for why). `MappingStore` gains repo CRUD scoped by project. No back-compat shim: this is a clean schema cut, not an additive migration alongside the old single-repo shape — there is one live deployment and its one mapped project moves over directly. `targets.yml`'s schema widens to a repo list per project entry (same YAML file, wider shape) and stays the seed-once importer.

**Step 2 — Live repo/project listing.** `PlaneClient.list_projects(workspace_slug)` (new) — powers the wizard's "which projects should the conductor manage" checklist. GitHub `list_repos(token)` (new, paginated `GET /user/repos`) — powers a live repo picker instead of free-typed `owner/name`. Note for docs/SETUP_GITHUB.md: a fine-grained PAT only lists repos it was explicitly granted at creation time, so a repo missing from the picker means the token's own GitHub-side grant needs editing, not a conductor bug.

**Step 3 — Wizard UI: project enable + per-project GitHub sections.** The Plane step's project list becomes a checkbox per workspace project (backed by step 1's `enabled` flag) instead of typing a project ID blind. The GitHub step becomes **one section per enabled project**: a repo picker fed by step 2's live list, defaulting to one slot with an "Add another repo" button for the rest; a project-scoped webhook secret field with a **Generate secret** button (pattern already shipped for the flat field in PR #17); and the shared `/webhooks/github` payload URL (one URL serves every repo — routing is resolved server-side per delivery in step 5) with the copy-to-clipboard control from PR #17, plus instructions to add it, with the same secret, to every repo's GitHub webhook settings (SSL verification enabled, `application/json`, and the four events from step 5).

**Step 4 — Structured export/import.** The flat `.env` export stays scalar-only (unchanged) — it was never able to represent a dynamic repo list. `targets.yml`'s widened schema (step 1) becomes bidirectional: add the counterpart **export** (today only import exists) so the full project→repos mapping, including secrets, round-trips through one YAML file — reusing the existing loader/format instead of inventing a second one. Same "plaintext secrets, handle carefully" caveat as the existing `.env` export.

**Step 5 — GitHub webhook handler + poll mode.** GitHub client: branch push via git in the dispatcher, PR create/read via REST, PR review-state read. `GITHUB_EVENT_MODE=webhook`: `/webhooks/github` parses the (unverified) body's `repository.full_name`, looks up which project's `RepoMapping` owns it, fetches **that project's** `webhook_secret` (step 1), and HMAC-verifies against it — the multi-tenant-webhook pattern (verify-after-lookup), needed because secrets are now per-project, not global. Subscribes to and routes four events: **`pull_request`** (opened/closed/merged/synchronize — merge-cleanup and ready-for-review transitions), **`pull_request_review`** (submitted approved/changes_requested/commented — review-loop verdict), **`pull_request_review_comment`** (inline diff comments — fed to the engineer as review feedback), **`pull_request_review_thread`** (resolved/unresolved — tracks outstanding feedback); each deduped by `X-GitHub-Delivery`; any other delivered event type is acknowledged and ignored. `GITHUB_EVENT_MODE=poll`: interval loop comparing PR state across every mapped repo.

**Step 6 — Docs & acceptance.** docs/SETUP_GITHUB.md: machine account creation, fine-grained PAT scopes (+ the step-2 repo-visibility caveat), per-project webhook walkthrough (repeat per repo, same project secret), branch protection walkthrough, poll vs. webhook tradeoffs, tunnel guidance for non-public deployments (Cloudflare Tunnel / Tailscale Funnel), and why branch protection — not prompts — is the human-approval guarantee.

Acceptance: a workspace with 2+ Plane projects renders a checkbox per project in the wizard, and enabling one reveals a GitHub section that accepts 2+ repos from a live-fetched list plus a generated per-project secret; the mapping round-trips through a `targets.yml` export/import; with poll mode and a manually opened PR on a tracked repo, the conductor notices state changes within one interval; in webhook mode, deliveries of all four subscribed event types are HMAC-verified against the correct project's secret, deduped, and routed (unsigned or wrong-secret deliveries rejected 401); merged PR triggers the cleanup job.

## Phase 4 — Agent image & dispatcher

Deliverables: `agent/Dockerfile` (node:22-slim, Claude Code CLI, git, gh, Python 3.12 + pytest, non-root `agent` user); `entrypoint.sh` that (a) exits loudly if both auth vars present, (b) seeds `~/.claude.json` from `seed-claude.json` (onboarding-complete + trusted `/work`) if the state volume is fresh, (c) execs the passed `claude -p` command; dispatcher module building `docker run` with per-ticket volumes (`psa-repo-<id>` at `/work`, `psa-claude-<id>` at `/home/agent/.claude`), role env (model, OTel resource attrs `agent.role`, `ticket.id`, `loop.round`), memory/CPU limits, `--rm`, named `psa-<role>-<ticket>`, hard timeout → kill; volume lifecycle module (create + clone `--depth 50` + branch `ticket/<id>`; destroy on cleanup); agent output contracts (Pydantic): planner plan JSON, engineer result + session_id, reviewer/QA verdict `{"pass": bool, "findings": [{severity, file, line, comment}]}` with a malformed-output policy (one re-prompt asking for valid JSON, then mark ticket `awaiting_human`).

The dispatcher enforces a **per-role concurrency limit** (`MAX_CONCURRENT_AGENTS`, default and v1-supported value `1`): a role semaphore ensures at most one engineer / reviewer / QA container runs at a time, so tickets are built serially (see [Work intake, ordering & concurrency](#work-intake-ordering--concurrency)).

Acceptance: `make agent-smoke` runs a containerized `claude -p "say hi" --output-format json` successfully with either auth mode; a second run against the same state volume resumes the prior session; kill-on-timeout verified; with two tickets eligible and `MAX_CONCURRENT_AGENTS=1`, only one engineer container runs at a time and the second starts only after the first clears the engineer stage.

## Phase 5 — The four roles

Prompt files with explicit output contracts (deliverable: `agent/prompts/*.md`):
- **Planner** — input: epic title/body **plus the project's repo set** (each repo's key/role, `github_repo`, and a read-only clone of its base branch, so ticket scoping reflects the real codebase across every repo). Tools: Read/Glob/Grep on those clones. Must produce tickets that are independently shippable, single-PR sized, with acceptance criteria QA can execute; **each ticket is tagged with exactly one target repo key** from the project's set (a change spanning repos is split into separate per-repo tickets, linked via the blocking graph); emits plan JSON (titles, bodies, criteria, **target repo**, and a **blocks/blocked-by graph** referencing the plan's own ticket keys). The conductor creates the Plane issues, records each ticket's target repo, drops them in `ready_for_dev`, and **sets the corresponding Plane work-item blocking relationships** so the board enforces build order (agent does not call Plane directly — keeps credentials out of agent containers). Every downstream per-ticket step — clone volume, `ticket/<id>` branch, PR, and GitHub-webhook routing — keys off that ticket's target repo. When the project has a single repo, the target is implicit.
- **Engineer** — input: ticket + criteria (+ findings JSON on resume). Tools: full read/write/Bash inside its container at `/work`. Must run the test suite before declaring done; conventional commits referencing the ticket ID; no scope creep. Conductor performs push + PR creation afterward (deterministic, credentialed step stays out of the agent).
- **Reviewer** — input: `git diff <base>...ticket/<id>` (+ full checkout read access). Tools: Read/Glob/Grep + read-only Bash (lint, typecheck). Focus: correctness, security, error handling, repo-consistent style, test coverage. Explicitly instructed not to nitpick what the linter enforces. Verdict JSON.
- **QA** — input: acceptance criteria + the branch. Tools: Read + Bash to build/run/test; no source edits; throwaway scripts to `/tmp` allowed. Focus: do the criteria actually pass; edge cases; adjacent regressions. Verdict JSON with reproduction steps.

Intake & scheduling in the state machine (full semantics in [Work intake, ordering & concurrency](#work-intake-ordering--concurrency)): an `epic`-labelled issue dispatches the planner; a ticket entering `ready_for_dev` dispatches an engineer. The scheduler prefers **in-flight tickets** (`in_progress`/`in_review`/`changes_requested`) over pulling a new `ready_for_dev` ticket, then takes the oldest-created among new ones, skipping any blocked by a not-yet-`done` issue.

Loop mechanics: reviewer + QA run in parallel after PR creation (or sequentially when `MAX_CONCURRENT_AGENTS=1`, the default); any fail → findings posted as ticket comments, ticket → `changes_requested`, `loop_round++`, diff-hash stall check (identical hash twice → `stalled` + notify), else `claude -p --resume <engineer_session_id>` with findings; conductor pushes the new commits to the same PR; re-run both checkers. Both pass → `ready_for_approval` + notify with PR link. **No auto-merge** — a human merges behind branch protection.

Acceptance: on a fixture repo (tiny FastAPI todo app included in `tests/fixtures/demo-repo`), a seeded ticket with a deliberate bug goes engineer → fail QA → resume → pass → `ready_for_approval` without human input, and stops there (not merged); with one ticket in `changes_requested` and a newer ticket in `ready_for_dev`, the conductor works the `changes_requested` ticket first.

## Phase 6 — Observability

Deliverables: docker-compose.observability.yml (otel-collector, Prometheus, Loki, Grafana) with provisioned datasources and three dashboards — pipeline overview (tickets by state, active agents, loop rounds), cost/tokens by `agent.role` and `ticket.id`, per-ticket drilldown (Loki tool-call event stream: "what is the engineer doing right now"); conductor's own metrics (jobs queued/running/failed, webhook deliveries, poll latency); alert rules: `loop_round > 5`, billing/limit error events, conductor down; docs/OBSERVABILITY.md including the external-collector path (set `OTEL_EXPORTER_OTLP_ENDPOINT`, skip the profile) with an Alloy OTLP-receiver example.

Acceptance: `docker compose --profile observability up` → Grafana at :3000 shows live data from an agent smoke run with role/ticket labels.

## Phase 7 — Docs, packaging, release

Deliverables:
- **README.md** — the front door. Structure: what it is (30-second pitch + the architecture diagram), demo GIF placeholder, prerequisites (Docker, a Plane workspace, a GitHub repo, Claude Pro/Max or API key), Quickstart (clone → `cp .env.example .env` → fill five required sections → `make up` → `make plane-init` → write an epic), first-run walkthrough (what you'll see in Plane at each stage), configuration reference table (every env var, linked to the deep-dive docs), deployment scenarios (laptop with poll mode; home server; cloud VM with webhook mode + tunnel), FAQ (billing modes, limits, model selection, "why did my ticket stall"), troubleshooting, contributing, license.
- docs/SECURITY.md — threat model: agent containers never see the Docker socket or credentials for Plane/GitHub (only the conductor holds those); socket-proxy recommendation (`tecnativa/docker-socket-proxy`, container+volume endpoints only); prompt injection note — ticket/epic bodies are untrusted input the moment anyone other than you can write them, and engineer output is reviewed by two agents + a human gate, which is the mitigation; secrets hygiene (.env in .gitignore, no secrets in images or compose files).
- docs/RUNBOOK.md — subscription token rotation (calendar the ~1-year expiry), recovering a `stalled` ticket, replaying a webhook, cleaning orphaned volumes (`make clean-ticket T=PLANE-123`, plus a scheduled janitor job in the conductor for orphans and >24h-stuck tickets), switching Pro → Max → API.
- MIT LICENSE, CONTRIBUTING.md, versioned releases with prebuilt images pushed to GHCR so quickstart doesn't require local image builds.

Acceptance: a fresh machine (or teammate) reaches a passing end-to-end demo run using only README + docs.

---

## Milestone order & who builds what

Phases 1→7 in order; each phase is a PR-sized unit with its acceptance test. This doc plus the repo skeleton is sufficient context for Claude Code to implement phase by phase — point it at `docs/PLAN.md`, one phase per session, acceptance criteria as the definition of done. (Yes, the tool builds itself before it can build other things. Bootstrapping is the tradition.)

## Cost & limits reality check

Subscription mode: Pro limits are shared with claude.ai chat and are the tightest constraint — `MAX_CONCURRENT_AGENTS=1`, cheaper models for reviewer/QA, stall detection, and halt-on-limit-error (never retry-loop a rate limit; that's a self-DoS) are the mitigations. Max is the natural upgrade; API key mode removes limits but pays per token. Note: Anthropic announced (May 2026) then paused moving headless `claude -p` off subscription pools to a metered credit — currently headless still draws from subscription, but re-verify against current docs before release and document the current state in SETUP_CLAUDE_AUTH.md. The conductor must classify billing/limit errors distinctly and surface them in notifications and metrics.

## Out of scope for v1 (tracked as future issues)

Human comments on in-flight tickets injected into engineer resumes ("steering"); GitLab/Gitea support; Plane Agents as default once out of beta; egress-restricted agent networks; Kubernetes deployment manifests; a `psa` CLI for local ticket dry-runs.

**Note:** *Multi-repo routing per ticket* was originally listed here as out of scope; it has been promoted to a **core requirement** — a Plane project maps to one or more repos and the planner assigns each ticket a target repo. See deviation #1 in [`CLAUDE.md`](../CLAUDE.md) and the mapping/planner notes in Phases 3 and 5.