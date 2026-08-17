#!/usr/bin/env bash
#
# Azure App Service startup command:
#
#     bash startup.sh
#
# Set it under Configuration -> Stack settings -> Startup Command.

set -euo pipefail

cd "${HOME}/site/wwwroot" 2>/dev/null || cd "$(dirname "$0")"

VENV_DIR="antenv"

# Oryx normally builds this virtualenv at deploy time and activates it before
# running this script. That build is easy to lose — it needs both
# SCM_DO_BUILD_DURING_DEPLOYMENT and ENABLE_ORYX_BUILD set, and a OneDeploy
# push silently skips it otherwise, leaving an app with no dependencies that
# fails as an opaque 503.
#
# So: use the venv if it exists, and build it here if it does not. /home is
# persistent storage on App Service, so this cost is paid once, not per restart.
if [ -d "${VENV_DIR}/bin" ]; then
  # shellcheck disable=SC1091
  source "${VENV_DIR}/bin/activate"
fi

if ! python -c "import alembic" >/dev/null 2>&1; then
  echo "Dependencies not present — installing into ${VENV_DIR} (first boot only)…"
  python -m venv "${VENV_DIR}"
  # shellcheck disable=SC1091
  source "${VENV_DIR}/bin/activate"
  python -m pip install --upgrade pip --quiet
  python -m pip install -r requirements.txt --quiet
  echo "Dependencies installed."
fi

echo "Applying database migrations…"
# Migrations run at boot rather than from CI so the schema can never be ahead of
# the code serving traffic. Alembic is idempotent — a no-op when the database is
# already current — and a failure here aborts the start, which is the correct
# outcome: better a failed deploy than an app running against a schema it does
# not understand.
python -m alembic upgrade head

# App Service assigns the port; 8000 keeps `bash startup.sh` working locally.
PORT="${PORT:-8000}"

# Two workers on a B1 (1 vCPU): one serves requests while the other handles a
# blocked call. Each worker runs its own event loop and its own connection pool,
# so DB_POOL_SIZE is per worker, not per app.
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
