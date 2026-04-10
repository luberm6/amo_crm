#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_WORKER="${RUN_WORKER:-1}"
RUN_BEAT="${RUN_BEAT:-0}"

BACKEND_CMD=("$ROOT_DIR/.venv/bin/uvicorn" app.main:app --host 0.0.0.0 --port 8000 --reload)
WORKER_CMD=("$ROOT_DIR/.venv/bin/celery" -A app.workers.celery_app worker --loglevel=info -Q default)
BEAT_CMD=("$ROOT_DIR/.venv/bin/celery" -A app.workers.celery_app beat --loglevel=info)
ADMIN_CMD=(npm --prefix "$ROOT_DIR/admin-panel" run dev -- --host 0.0.0.0 --port 5173)

PIDS=()

cleanup() {
  local pid
  for pid in "${PIDS[@]:-}"; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
  done
}
trap cleanup EXIT INT TERM

cd "$ROOT_DIR"

printf '\n[run-all] starting backend on http://localhost:8000\n'
"${BACKEND_CMD[@]}" &
PIDS+=("$!")

if [ "$RUN_WORKER" = "1" ]; then
  printf '\n[run-all] starting Celery worker\n'
  "${WORKER_CMD[@]}" &
  PIDS+=("$!")
fi

if [ "$RUN_BEAT" = "1" ]; then
  printf '\n[run-all] starting Celery beat\n'
  "${BEAT_CMD[@]}" &
  PIDS+=("$!")
fi

printf '\n[run-all] starting admin panel on http://localhost:5173\n'
"${ADMIN_CMD[@]}" &
PIDS+=("$!")

# Wait until any one managed process exits, then EXIT trap cleans up the rest.
# Uses a polling loop for bash 3.2 compatibility (macOS default shell).
while true; do
    for pid in "${PIDS[@]}"; do
        if ! kill -0 "$pid" 2>/dev/null; then
            exit 0
        fi
    done
    sleep 2
done
