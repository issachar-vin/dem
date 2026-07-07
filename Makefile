.PHONY: setup dev up down lint format typecheck test migrate clean console-run

CONDUCTOR = uv --directory conductor
CONSOLE = uv --directory console

setup:
	$(CONDUCTOR) sync
	$(CONDUCTOR) run pre-commit install
	$(CONSOLE) sync

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
