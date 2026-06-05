#!/bin/bash
cd "$(dirname "$0")/.."

echo "Starting AIP backend..."
uv run uvicorn "aip.adapter.api.app:create_app" --factory \
  --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

sleep 1
echo "Starting AIP GUI (renders immediately — backend connects in background)..."
uv run python -m gui.shell &
GUI_PID=$!

echo "Backend PID: $BACKEND_PID"
echo "GUI PID: $GUI_PID"
echo "Open http://127.0.0.1:8080"

wait