.PHONY: setup restart dev up down lint format typecheck test migrate clean version bump-major bump-minor bump-patch agent-build agent-smoke

CONDUCTOR = uv --directory conductor

# First-run: seed .env from the example (only if absent) and install deps into the
# conductor's .venv so your IDE can use them. Does not touch containers.
setup:
	@test -f .env || (cp .env.minimal.example .env && echo "Created .env from .env.minimal.example")
	$(CONDUCTOR) sync
	$(CONDUCTOR) run pre-commit install

# Rebuild the image from scratch (no cache) and bring the dev stack back up.
restart:
	docker compose -f docker-compose.dev.yml down
	docker compose -f docker-compose.dev.yml build --no-cache
	docker compose -f docker-compose.dev.yml up -d
	@echo "Conductor + console: http://localhost:8420"

migrate:
	$(CONDUCTOR) run alembic upgrade head

# Local source build with live reload. Production uses docker-compose.yml (published images).
dev up:
	docker compose -f docker-compose.dev.yml up --build

down:
	docker compose -f docker-compose.dev.yml down

lint:
	$(CONDUCTOR) run ruff format .
	$(CONDUCTOR) run ruff check --fix .

format:
	$(CONDUCTOR) run ruff format .

typecheck:
	$(CONDUCTOR) run mypy src

test:
	$(CONDUCTOR) run pytest

# Build the agent image (Claude Code CLI + toolchain) used by the dispatcher.
agent-build:
	docker build -t dem-agent:latest agent

# Phase 4 acceptance: containerized claude -p, session resume, and kill-on-timeout.
agent-smoke:
	bash scripts/agent-smoke.sh

clean:
	docker compose -f docker-compose.dev.yml down
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .venv -exec rm -rf {} +

# Versioning (root VERSION file drives the Docker image tag). Pre-launch scheme:
# major stays 0, minor = the phase, patch = changes within a phase. Bump by hand.
version:
	@cat VERSION

bump-major:
	@python3 scripts/bump_version.py major

bump-minor:
	@python3 scripts/bump_version.py minor

bump-patch:
	@python3 scripts/bump_version.py patch
