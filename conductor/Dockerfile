FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src ./src

EXPOSE 8420
CMD ["uv", "run", "--no-dev", "uvicorn", "conductor.main:app", "--host", "0.0.0.0", "--port", "8420"]
