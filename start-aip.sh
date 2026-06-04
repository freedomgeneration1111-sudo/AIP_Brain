#!/bin/bash

echo "=== Starting AIP_Brain ==="

# Kill any old instances
echo "Stopping old processes..."
pkill -f "uvicorn aip.adapter.api.app"
pkill -f "python.*gui/main.py"
sleep 1.5

# Start Backend in a new terminal
echo "Starting AIP Backend..."
gnome-terminal -- bash -c "cd ~/AIP_Brain && uv run uvicorn aip.adapter.api.app:create_app --factory --host 0.0.0.0 --port 8000; exec bash"

sleep 3

# Start GUI in another new terminal
echo "Starting AIP_Brain GUI..."
gnome-terminal -- bash -c "cd ~/AIP_Brain && uv run python -m gui.main; exec bash"

echo "=== AIP_Brain started ==="
