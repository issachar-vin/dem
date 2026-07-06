FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations

EXPOSE 8420
CMD ["sh", "-c", "uv run --no-dev alembic upgrade head && uv run --no-dev uvicorn conductor.main:create_app --factory --host 0.0.0.0 --port 8420"]
