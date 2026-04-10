#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"
ALEMBIC_BIN="$ROOT_DIR/.venv/bin/alembic"
DOCTOR_SCRIPT="$ROOT_DIR/scripts/local_env_doctor.py"
BACKEND_ENV_TEMPLATE="$ROOT_DIR/.env.local.example"
BACKEND_ENV_FILE="$ROOT_DIR/.env"
FRONTEND_ENV_TEMPLATE="$ROOT_DIR/admin-panel/.env.example"
FRONTEND_ENV_FILE="$ROOT_DIR/admin-panel/.env.local"
LOCAL_DB_HOST="127.0.0.1"
LOCAL_DB_PORT="5433"

warn() {
  printf '\n[warn] %s\n' "$1"
}

info() {
  printf '\n[info] %s\n' "$1"
}

warn_if_legacy_local_database_url() {
  if [ ! -f "$BACKEND_ENV_FILE" ]; then
    return 0
  fi
  if grep -Eq '^DATABASE_URL=postgresql\+asyncpg://[^@]+@localhost:5432/|^DATABASE_URL=postgresql\+asyncpg://[^@]+@127\.0\.0\.1:5432/' "$BACKEND_ENV_FILE"; then
    warn ".env still points to local Postgres on :5432. The canonical local DB contract is ${LOCAL_DB_HOST}:${LOCAL_DB_PORT} to avoid conflicts with a system Postgres."
  fi
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf '\n[error] Missing required command: %s\n' "$1" >&2
    exit 1
  fi
}

find_compose_cmd() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    echo "docker compose"
    return 0
  fi
  if command -v docker-compose >/dev/null 2>&1; then
    echo "docker-compose"
    return 0
  fi
  return 1
}

wait_for_port() {
  local host="$1"
  local port="$2"
  local timeout="${3:-30}"
  local i=0
  if ! command -v nc >/dev/null 2>&1; then
    sleep 5
    return 0
  fi
  until nc -z "$host" "$port" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -ge "$timeout" ]; then
      return 1
    fi
    sleep 1
  done
}

require_command python3
require_command node
require_command npm

PY_VERSION="$(python3 --version 2>&1)"
info "Detected: $PY_VERSION (required: >=3.9)"
if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>/dev/null; then
  printf '\n[error] Python 3.9+ is required. Detected: %s\n' "$PY_VERSION" >&2
  printf '[error] On this machine python3.11 is at /usr/local/bin/python3.11 — symlink or use it directly.\n' >&2
  exit 1
fi

if [ ! -d "$ROOT_DIR/.venv" ]; then
  info "Creating Python virtualenv at .venv"
  python3 -m venv "$ROOT_DIR/.venv"
elif ! "$VENV_PYTHON" -m pip --version >/dev/null 2>&1; then
  warn "Existing .venv has a broken pip installation. Recreating .venv."
  rm -rf "$ROOT_DIR/.venv"
  python3 -m venv "$ROOT_DIR/.venv"
fi

info "Installing backend dependencies into .venv"
if ! (
  "$VENV_PYTHON" -m ensurepip --upgrade >/dev/null 2>&1 || true
  "$VENV_PYTHON" -m pip install --upgrade pip >/dev/null
  "$VENV_PYTHON" -m pip install -e '.[dev]'
); then
  warn "Backend dependency installation failed inside the current .venv. Recreating .venv once and retrying."
  rm -rf "$ROOT_DIR/.venv"
  python3 -m venv "$ROOT_DIR/.venv"
  "$VENV_PYTHON" -m ensurepip --upgrade >/dev/null 2>&1 || true
  "$VENV_PYTHON" -m pip install --upgrade pip >/dev/null
  "$VENV_PYTHON" -m pip install -e '.[dev]'
fi

if [ ! -f "$BACKEND_ENV_FILE" ]; then
  info "Creating .env from .env.local.example"
  cp "$BACKEND_ENV_TEMPLATE" "$BACKEND_ENV_FILE"
else
  info ".env already exists — leaving it untouched"
fi
warn_if_legacy_local_database_url

if [ ! -f "$FRONTEND_ENV_FILE" ]; then
  info "Creating admin-panel/.env.local from admin-panel/.env.example"
  cp "$FRONTEND_ENV_TEMPLATE" "$FRONTEND_ENV_FILE"
else
  info "admin-panel/.env.local already exists — leaving it untouched"
fi

info "Installing admin-panel dependencies"
npm --prefix "$ROOT_DIR/admin-panel" install

if COMPOSE_CMD="$(find_compose_cmd 2>/dev/null)"; then
  info "Starting local Postgres and Redis via $COMPOSE_CMD"
  if ! (cd "$ROOT_DIR" && $COMPOSE_CMD up -d postgres redis); then
    warn "Could not start docker-compose services automatically. If Postgres/Redis are already running locally, bootstrap can still continue."
  else
    wait_for_port "$LOCAL_DB_HOST" "$LOCAL_DB_PORT" 45 || warn "Postgres did not become reachable on ${LOCAL_DB_HOST}:${LOCAL_DB_PORT} in time."
    wait_for_port 127.0.0.1 6379 45 || warn "Redis did not become reachable on 127.0.0.1:6379 in time."
  fi
else
  warn "Docker Compose is unavailable. Expecting Postgres on ${LOCAL_DB_HOST}:${LOCAL_DB_PORT} and Redis on 127.0.0.1:6379."
fi

info "Applying Alembic migrations"
if ! (cd "$ROOT_DIR" && "$ALEMBIC_BIN" upgrade head); then
  warn "Alembic migrations failed. Doctor will explain what is still blocked."
fi

info "Running local doctor"
set +e
(cd "$ROOT_DIR" && "$VENV_PYTHON" "$DOCTOR_SCRIPT")
DOCTOR_EXIT=$?
set -e

case "$DOCTOR_EXIT" in
  0)
    info "Bootstrap finished: local stack prerequisites are READY."
    ;;
  1)
    warn "Bootstrap finished in PARTIAL state. This usually means UI/runtime can start, but external voice/provider secrets are still missing."
    ;;
  *)
    warn "Bootstrap finished in BLOCKED state. See doctor output above for the exact blocker."
    ;;
 esac

exit "$DOCTOR_EXIT"
