.PHONY: setup restart dev up down lint format typecheck test migrate clean version bump-major bump-minor bump-patch

CONDUCTOR = uv --directory conductor

# First-run: seed .env from the example (only if absent) and install deps into the
# conductor's .venv so your IDE can use them. Does not touch containers.
setup:
	@test -f .env || (cp .env.minimal.example .env && echo "Created .env from .env.minimal.example")
	$(CONDUCTOR) sync
	$(CONDUCTOR) run pre-commit install

# Rebuild the image from scratch (no cache) and bring the stack back up.
restart:
	docker compose down
	docker compose build --no-cache
	docker compose up -d
	@echo "Conductor + console: http://localhost:8420"

migrate:
	$(CONDUCTOR) run alembic upgrade head

dev up:
	docker compose up --build

down:
	docker compose down

lint:
	$(CONDUCTOR) run ruff format .
	$(CONDUCTOR) run ruff check --fix .

format:
	$(CONDUCTOR) run ruff format .

typecheck:
	$(CONDUCTOR) run mypy src

test:
	$(CONDUCTOR) run pytest

clean:
	docker compose down
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
