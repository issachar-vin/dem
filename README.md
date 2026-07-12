# DEM — Deus Ex Machina

**A self-hostable, autonomous software-development pipeline you can stand up in minutes.**

Write an epic in [Plane](https://plane.so); DEM does the rest. A planner agent breaks the epic into
tickets, engineer agents build each ticket in an isolated Docker container and open pull requests,
and reviewer + QA agents critique the work and loop feedback back to the engineer until both pass.
Then it hands you a PR to approve.

The goal is a batteries-included tool that turns intent into reviewed pull requests with only **two
human touchpoints**: **writing the epic** and **approving the PR**. Everything in between — planning,
building, reviewing, retrying, cleanup — runs on its own, powered by Claude Code agents.

- One project can span **many repos**; the planner routes each ticket to the repo(s) it needs.
- Runs as a **single container** (conductor + admin console + webhooks + DB in one process).
- Config lives in an **encrypted DB**, driven by a setup wizard — no sprawling env files.
- Deploys anywhere Docker runs; SQLite by default, Postgres when you outgrow it.

> Full architecture and phase-by-phase spec: [`docs/PLAN.md`](docs/PLAN.md). Live build status:
> [`docs/HANDOFF.md`](docs/HANDOFF.md).

## How to set it up

DEM needs only **two** environment variables before it can boot; everything else is entered through
the admin console's setup wizard and stored encrypted in the DB.

### 1. Bootstrap env

```bash
make setup      # copies .env.minimal.example → .env
```

That gives you the only two mandatory variables:

- `DEM_SECRET_KEY` — the root of trust that encrypts every stored secret. It can't live in the DB it
  decrypts. The copied file ships a throwaway dev key so the stack boots on the spot; **generate a
  fresh one for any real deployment**:
  ```bash
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
- `DATABASE_URL` — job/state + config store. SQLite (a file in a named volume) by default; point it
  at Postgres for a server: `postgresql+asyncpg://user:pass@db/dem`.

### 2. Start it

Two ways to run, both landing on the same console at `http://localhost:8420`.

#### Setup with Docker (recommended)

The bundled [`docker-compose.yml`](docker-compose.yml) pulls the images published to GHCR — no build:

```bash
docker compose up -d
```

This runs the `dem-conductor` image, mounts the host Docker socket (so the conductor can launch the
`dem-agent` container per ticket), and persists the DB in the `conductor_data` volume. It expects the
`.env` from step 1 in the same directory. To take a new release, `docker compose pull && docker
compose up -d`.

#### Build manually

To build the conductor from source (developing on it, or running an unreleased revision), use the dev
compose — it builds the image locally and mounts `conductor/src` with live reload:

```bash
make dev        # docker compose -f docker-compose.dev.yml up --build
```

You'll also need the agent image the dispatcher runs per ticket: `make agent-build` (tags
`dem-agent:latest` locally), then set the console's **Advanced → agent image** to `dem-agent:latest`.

---

`GET /health` returns `{"status": "ok"}`. Open the console and you'll land on the **setup wizard**.

### 3. Run the wizard

The wizard walks you through everything the pipeline needs, verifying each key with a live
connection test as you go:

- **Claude** — one credential: a Pro/Max subscription token (`claude setup-token`) or an
  `ANTHROPIC_API_KEY`, plus the model per pipeline role (planner / engineer / reviewer / QA).
- **Plane** — base URL, workspace slug, API token, and a webhook secret; pick how an epic is
  recognized (a label named `epic` works on Community edition).
- **GitHub** — a machine-account token (Contents + Pull-requests + Metadata), and each project's
  **repos** with their base branches. Each project owns one webhook secret shared by its repos.
- **Notifications** (optional) — ntfy / Slack / webhook.
- **Advanced** — public URL (webhook targets are built from it), agent image, concurrency, timeouts.

Point your Plane and GitHub webhooks at `<public-url>/webhooks/plane` and `/webhooks/github`
(see [`docs/SETUP_GITHUB.md`](docs/SETUP_GITHUB.md)). Once the wizard reads complete, you're live:
drop the `epic` label on a Plane work item and the pipeline takes over.

> Prefer to provision everything from env (IaC / Pulumi)? Every wizard field has a first-boot seed
> variable — see the fully annotated [`.env.example`](.env.example). The DB is always the source of
> truth after first boot; set `RESEED_FROM_ENV=true` to re-import env over it for rotation.

### Back up your config so re-setup is instant

Once you've configured a deployment, don't do it by hand twice. In the console, **Config →
Migration** lets you export the whole configuration:

- **Encrypted bundle** — passphrase-protected, safe to store anywhere. Re-import it on a fresh
  instance (same tab) to restore every setting and secret in one shot.
- **`.env`** — plaintext seed of every value, for IaC or a known-good baseline.
- **`targets.yml`** — the full project→repos mapping.

Import runs in the same Migration tab: upload the bundle with its passphrase and the new instance
comes up fully configured. This is the fast path to re-provision after a rebuild, a move, or spinning
up a second environment.

## How it works

DEM is one FastAPI process ([`conductor/`](conductor/)) that hosts the admin console (NiceGUI mounted
into the same app), receives webhooks, and drives the pipeline:

1. **Intake** — Plane and GitHub webhooks land at `/webhooks/*`, are signature-verified against
   per-project secrets, deduped, and enqueued as **Jobs**. Intake never encodes pipeline shape — it
   just records what happened.
2. **Planner** — an epic fans out to a planner agent that reads the project's repos (cloned
   read-only) and emits a set of tickets, each routed to a target repo, with a build-order graph.
3. **Engineer** — each ticket runs a Claude Code agent inside a throwaway Docker container on its own
   `ticket/<id>` branch, over per-repo work volumes. Credentials never enter the container; the
   conductor clones, commits, pushes, and opens **one PR per repo** the ticket touched.
4. **Review loop** — reviewer and QA agents critique every repo the ticket changed. If either
   requests changes, the findings are posted back and the engineer resumes its session to address
   them; a stalled (unchanged) diff is parked. When both pass, the ticket goes to
   **ready-for-approval** and you get a notification with the PR link.
5. **Cleanup** — when you merge, the merged-PR webhook marks the ticket done, reclaims its volumes,
   and releases any dependent tickets that were waiting on it.

Design highlights: **secrets are encrypted (Fernet), never hashed**, because they're handed to
Plane/GitHub/Anthropic verbatim; the **admin console** verifies every credential live and manages
repo↔project and workflow-state mappings; a **live console** streams each agent run and a per-job
event timeline so you can watch what an agent is doing in real time. Observability exports to your
existing OTel collector — DEM bundles no monitoring stack of its own.

## Development

```bash
make setup      # deps + pre-commit hooks, .env
make dev        # docker compose up (conductor at :8420)
make test
make lint
make typecheck
```

Stack: Python 3.12, FastAPI, SQLAlchemy 2 (async) + Alembic, NiceGUI, httpx, Pydantic Settings,
cryptography. Tooling via [uv](https://docs.astral.sh/uv/), ruff, mypy `--strict`, pytest.

Changes follow the branch → PR workflow; `main` is protected and squash-merge only. Bump the root
`VERSION` before opening a PR (`make bump-patch` / `make bump-minor`).

## License

MIT
