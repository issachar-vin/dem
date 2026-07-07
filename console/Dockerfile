FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src ./src

EXPOSE 8501
CMD ["uv", "run", "--no-dev", "streamlit", "run", "src/console/app.py", "--server.address=0.0.0.0", "--server.port=8501"]
