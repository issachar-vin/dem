.PHONY: setup restart dev up down lint format typecheck test migrate clean console-run

CONDUCTOR = uv --directory conductor
CONSOLE = uv --directory console

# First-run: seed .env from the example (only if absent) and install deps into each
# package's .venv so your IDE can use them. Does not touch containers.
setup:
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example")
	$(CONDUCTOR) sync
	$(CONDUCTOR) run pre-commit install
	$(CONSOLE) sync

# Rebuild every image from scratch (no cache) and bring the stack back up.
restart:
	docker compose down
	docker compose build --no-cache
	docker compose up -d
	@echo "Conductor: http://localhost:8420  ·  Console: http://localhost:8501"

migrate:
	$(CONDUCTOR) run alembic upgrade head

dev up:
	docker compose up --build

down:
	docker compose down

console-run:
	$(CONSOLE) run streamlit run src/console/app.py

lint:
	$(CONDUCTOR) run ruff format .
	$(CONDUCTOR) run ruff check --fix .
	$(CONSOLE) run ruff format .
	$(CONSOLE) run ruff check --fix .

format:
	$(CONDUCTOR) run ruff format .
	$(CONSOLE) run ruff format .

typecheck:
	$(CONDUCTOR) run mypy src
	$(CONSOLE) run mypy src tests

test:
	$(CONDUCTOR) run pytest
	$(CONSOLE) run pytest

clean:
	docker compose down
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .venv -exec rm -rf {} +
