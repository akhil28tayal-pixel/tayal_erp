#!/bin/sh
# Applies migrations and base seed data, then starts the API. Safe to run on every start.
set -e
alembic upgrade head
python -m app.cli seed-base
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 ${UVICORN_EXTRA:-}
