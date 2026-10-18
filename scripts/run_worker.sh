#!/bin/sh
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR"
export PYTHONUNBUFFERED=1
exec "$PROJECT_DIR/.venv/bin/python" -m trainbot.worker
