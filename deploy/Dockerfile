# AIP 0.1 Dockerfile — Phase 5 Hardened
FROM python:3.12-slim

WORKDIR /app

# System deps for sqlite + vector (laptop profile)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv for dependency management
RUN pip install --no-cache-dir uv

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-dev

# Copy source code
COPY src ./src
COPY config ./config
COPY workflows ./workflows
COPY deploy ./deploy
COPY gui ./gui

# Create non-root user for security
RUN groupadd -r aip && useradd -r -g aip -d /app -s /sbin/nologin aip \
    && mkdir -p /app/db && chown -R aip:aip /app

ENV PYTHONPATH=/app/src
ENV AIP_PROFILE=laptop

# Switch to non-root user
USER aip

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" || exit 1

# NOTE: The bind host is controlled by config/api.host, NOT the uvicorn --host flag.
# Production config sets host=0.0.0.0 with auth_enabled=true.
# Laptop config sets host=127.0.0.1 with auth_enabled=false (local-only).
# The application validates unsafe combinations at startup (e.g. 0.0.0.0 + auth_enabled=false).
# Use 0.0.0.0 here so Docker port forwarding works; the app reads host from its config.
CMD ["uv", "run", "uvicorn", "aip.adapter.api.app:create_app", "--host", "0.0.0.0", "--port", "8000", "--factory"]
