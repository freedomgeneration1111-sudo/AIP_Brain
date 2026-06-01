#!/usr/bin/env bash
# AIP_Brain Startup Script
# This script loads the .env file and starts both the backend and frontend.
#
# Usage:
#   ./start.sh           — start both backend + frontend
#   ./start.sh backend   — start only the backend
#   ./start.sh frontend  — start only the frontend

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load .env file if it exists
if [ -f .env ]; then
    echo "Loading .env file..."
    export $(grep -v '^#' .env | grep -v '^$' | xargs)
    echo "AIP_OPENAI_API_KEY: ${AIP_OPENAI_API_KEY:0:8}...${AIP_OPENAI_API_KEY: -4}"
else
    echo "WARNING: No .env file found. Create one from .env.example with your OpenRouter API key."
fi

# Check for API key
if [ -z "$AIP_OPENAI_API_KEY" ]; then
    echo ""
    echo "⚠️  AIP_OPENAI_API_KEY is not set!"
    echo "   You need an OpenRouter API key to use AIP_Brain."
    echo "   Get one at https://openrouter.ai/keys"
    echo "   Then set it in .env or run: export AIP_OPENAI_API_KEY=sk-or-v1-..."
    echo ""
fi

start_backend() {
    echo "Starting AIP Backend on port 8000..."
    PYTHONPATH=src python -m uvicorn aip.adapter.api.app:create_app --factory --host 0.0.0.0 --port 8000 &
    BACKEND_PID=$!
    echo "Backend PID: $BACKEND_PID"
    # Give backend time to start
    sleep 3
    # Verify it's running
    if curl -sf http://127.0.0.1:8000/api/v1/models/slots > /dev/null 2>&1; then
        echo "Backend is running and responding."
    else
        echo "WARNING: Backend may not be fully started yet."
    fi
}

start_frontend() {
    echo "Starting AIP Frontend (NiceGUI) on port 8080..."
    PYTHONPATH=src python -m gui.main &
    FRONTEND_PID=$!
    echo "Frontend PID: $FRONTEND_PID"
}

cleanup() {
    echo ""
    echo "Shutting down..."
    [ -n "$BACKEND_PID" ] && kill $BACKEND_PID 2>/dev/null || true
    [ -n "$FRONTEND_PID" ] && kill $FRONTEND_PID 2>/dev/null || true
    echo "Done."
}

trap cleanup EXIT INT TERM

case "${1:-all}" in
    backend)
        start_backend
        wait $BACKEND_PID
        ;;
    frontend)
        start_frontend
        wait $FRONTEND_PID
        ;;
    all|*)
        start_backend
        start_frontend
        echo ""
        echo "✅ AIP_Brain is running!"
        echo "   Backend:  http://0.0.0.0:8000"
        echo "   Frontend: http://0.0.0.0:8080"
        echo ""
        echo "Press Ctrl+C to stop."
        # Wait for either process
        wait -n 2>/dev/null || wait
        ;;
esac
