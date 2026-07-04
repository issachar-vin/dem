# Plane SWE Agents — Build Plan & Specification

An open-source, self-hostable pipeline that turns a Plane epic into approved pull requests using Claude Code agents. Write an epic in Plane; a planner agent breaks it into tickets; engineer agents build each ticket in an isolated container and open PRs; reviewer and QA agents critique the work and loop feedback back to the engineer until both pass; a human approves and merges. The human touchpoints are exactly two: writing the epic and approving the PR.

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
Planner (epic → tickets) → Engineer (ticket → PR) → Reviewer + QA in parallel → if either fails, findings are posted as ticket comments and fed back to the engineer via `claude -p --resume` → re-review → repeat until both pass → ticket `Ready for approval`, human merges. No iteration cap; instead a stall detector: if the engineer produces an identical diff two rounds in a row (sha256 of `git diff`), the ticket is marked `stalled` and the human is notified.

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

## Phase 3 — GitHub integration

Deliverables: GitHub client (branch push via git in dispatcher, PR create/read via REST, PR review-state read); poll mode (interval loop comparing PR states for tracked tickets) and webhook mode (HMAC-verified endpoint) behind `GITHUB_EVENT_MODE`; docs/SETUP_GITHUB.md: machine account creation, fine-grained PAT scopes, branch protection walkthrough, tunnel guidance for webhook mode (Cloudflare Tunnel / Tailscale Funnel), and why branch protection — not prompts — is the human-approval guarantee.

Acceptance: with poll mode and a manually opened PR on a tracked branch, the conductor notices state changes within one interval; merged PR triggers the cleanup job.

## Phase 4 — Agent image & dispatcher

Deliverables: `agent/Dockerfile` (node:22-slim, Claude Code CLI, git, gh, Python 3.12 + pytest, non-root `agent` user); `entrypoint.sh` that (a) exits loudly if both auth vars present, (b) seeds `~/.claude.json` from `seed-claude.json` (onboarding-complete + trusted `/work`) if the state volume is fresh, (c) execs the passed `claude -p` command; dispatcher module building `docker run` with per-ticket volumes (`psa-repo-<id>` at `/work`, `psa-claude-<id>` at `/home/agent/.claude`), role env (model, OTel resource attrs `agent.role`, `ticket.id`, `loop.round`), memory/CPU limits, `--rm`, named `psa-<role>-<ticket>`, hard timeout → kill; volume lifecycle module (create + clone `--depth 50` + branch `ticket/<id>`; destroy on cleanup); agent output contracts (Pydantic): planner plan JSON, engineer result + session_id, reviewer/QA verdict `{"pass": bool, "findings": [{severity, file, line, comment}]}` with a malformed-output policy (one re-prompt asking for valid JSON, then mark ticket `awaiting_human`).

Acceptance: `make agent-smoke` runs a containerized `claude -p "say hi" --output-format json` successfully with either auth mode; a second run against the same state volume resumes the prior session; kill-on-timeout verified.

## Phase 5 — The four roles

Prompt files with explicit output contracts (deliverable: `agent/prompts/*.md`):
- **Planner** — input: epic title/body. Tools: Read/Glob/Grep on a read-only clone of the base branch (so ticket scoping reflects the real codebase). Must produce tickets that are independently shippable, single-PR sized, with acceptance criteria QA can execute; emits plan JSON (titles, bodies, criteria, dependency order); conductor creates the Plane issues (agent does not call Plane directly — keeps credentials out of agent containers).
- **Engineer** — input: ticket + criteria (+ findings JSON on resume). Tools: full read/write/Bash inside its container at `/work`. Must run the test suite before declaring done; conventional commits referencing the ticket ID; no scope creep. Conductor performs push + PR creation afterward (deterministic, credentialed step stays out of the agent).
- **Reviewer** — input: `git diff <base>...ticket/<id>` (+ full checkout read access). Tools: Read/Glob/Grep + read-only Bash (lint, typecheck). Focus: correctness, security, error handling, repo-consistent style, test coverage. Explicitly instructed not to nitpick what the linter enforces. Verdict JSON.
- **QA** — input: acceptance criteria + the branch. Tools: Read + Bash to build/run/test; no source edits; throwaway scripts to `/tmp` allowed. Focus: do the criteria actually pass; edge cases; adjacent regressions. Verdict JSON with reproduction steps.

Loop mechanics in the state machine: reviewer + QA run in parallel after PR creation (or sequentially when `MAX_CONCURRENT_AGENTS=1`); any fail → findings posted as ticket comments, `loop_round++`, diff-hash stall check (identical hash twice → `stalled` + notify), else `claude -p --resume <engineer_session_id>` with findings; conductor pushes the new commits to the same PR; re-run both checkers. Both pass → `Ready for approval` + notify with PR link.

Acceptance: on a fixture repo (tiny FastAPI todo app included in `tests/fixtures/demo-repo`), a seeded ticket with a deliberate bug goes engineer → fail QA → resume → pass → `Ready for approval` without human input.

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

Multi-repo routing per ticket; human comments on in-flight tickets injected into engineer resumes ("steering"); GitLab/Gitea support; Plane Agents as default once out of beta; egress-restricted agent networks; Kubernetes deployment manifests; a `psa` CLI for local ticket dry-runs.