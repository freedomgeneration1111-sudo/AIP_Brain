#!/bin/bash
cd /home/z/my-project/AIP_Brain
export PYTHONPATH=src
# Load .env
if [ -f .env ]; then
    export $(grep -v '^#' .env | grep -v '^$' | xargs)
fi
exec python3 -m uvicorn aip.adapter.api.app:create_app --factory --host 0.0.0.0 --port 8000
