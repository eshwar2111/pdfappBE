#!/usr/bin/env bash
#
# Azure App Service startup command:
#
#     bash startup.sh
#
# Set it under Configuration -> General settings -> Startup Command.

set -euo pipefail

echo "Applying database migrations…"
# Migrations run at boot rather than from CI so the schema can never be ahead
# of the code that is actually serving traffic. Alembic is idempotent — a
# no-op when the database is already current — and a failure here aborts the
# start, which is the correct outcome: better a failed deploy than an app
# running against a schema it does not understand.
python -m alembic upgrade head

# App Service assigns the port; 8000 keeps `bash startup.sh` working locally.
PORT="${PORT:-8000}"

# Two workers on a B1 (1 vCPU): one serves requests while the other handles a
# blocked call. Each worker runs its own event loop and its own connection
# pool, so DB_POOL_SIZE is per worker, not per app.
WORKERS="${WEB_CONCURRENCY:-2}"

echo "Starting gunicorn on :${PORT} with ${WORKERS} worker(s)…"
exec gunicorn app.main:app \
  --worker-class uvicorn.workers.UvicornWorker \
  --workers "${WORKERS}" \
  --bind "0.0.0.0:${PORT}" \
  --timeout 300 \
  --graceful-timeout 30 \
  --keep-alive 65 \
  --access-logfile - \
  --error-logfile -
