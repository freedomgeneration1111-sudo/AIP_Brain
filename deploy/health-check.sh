#!/bin/bash
# Health check orchestration helper for AIP 0.1 (Phase 7 9.6)
set -e

URL=${AIP_HEALTH_URL:-http://localhost:8000/api/v1/health}

echo "Checking AIP health at $URL ..."

if curl -sf "$URL" > /dev/null; then
  echo "AIP is healthy."
  exit 0
else
  echo "AIP health check failed."
  exit 1
fi
