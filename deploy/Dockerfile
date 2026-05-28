# Minimal Dockerfile for AIP 0.1 (Phase 7 9.6)
FROM python:3.12-slim

WORKDIR /app

# System deps for sqlite + vector (laptop profile)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
# In real build: uv sync --frozen
# For scaffold we copy the source
COPY src ./src
COPY config ./config
COPY workflows ./workflows
COPY deploy ./deploy

ENV PYTHONPATH=/app/src
ENV AIP_PROFILE=laptop

EXPOSE 8000

# Health check (used by orchestration in 9.6)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" || exit 1

CMD ["uv", "run", "uvicorn", "aip.adapter.api.app:create_app", "--host", "0.0.0.0", "--port", "8000", "--factory"]
