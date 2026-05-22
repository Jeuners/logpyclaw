#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
source .venv/bin/activate

PORT=${PORT:-6060}
HOST=${HOST:-0.0.0.0}

exec uvicorn backend.app:app --host "$HOST" --port "$PORT" --reload
