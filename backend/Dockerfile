FROM python:3.12.11-slim-bookworm AS base

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv

COPY requirements.txt .

RUN uv pip install --system --no-cache-dir -r requirements.txt

FROM base AS test

COPY requirements-test.txt .
RUN uv pip install --system --no-cache-dir -r requirements-test.txt
COPY ./app/ ./app/
COPY ./tests/ ./tests/

ENTRYPOINT ["pytest", "-q"]

FROM base AS runtime

COPY ./app/ ./app/

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/docs || exit 1

ENTRYPOINT ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
