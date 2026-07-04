.PHONY: setup dev up down lint format test clean

CONDUCTOR = uv --directory conductor

setup:
	$(CONDUCTOR) sync
	$(CONDUCTOR) run pre-commit install

dev up:
	docker compose up --build

down:
	docker compose down

lint:
	$(CONDUCTOR) run ruff format .
	$(CONDUCTOR) run ruff check --fix .

format:
	$(CONDUCTOR) run ruff format .

test:
	$(CONDUCTOR) run pytest

clean:
	docker compose down
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .venv -exec rm -rf {} +
