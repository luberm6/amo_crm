#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/amo_crm}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
API_PORT="${API_PORT:-8000}"
ENV_FILE="${ENV_FILE:-$APP_DIR/.env}"
SERVICE_TEMPLATE_SOURCE="${SERVICE_TEMPLATE_SOURCE:-$APP_DIR/deploy/amo-crm-api-freeswitch.service.example}"
SERVICE_TARGET="/etc/systemd/system/amo-crm-api.service"

info() {
  printf '\n[info] %s\n' "$1"
}

die() {
  printf '\n[error] %s\n' "$1" >&2
  exit 1
}

require_file() {
  [ -f "$1" ] || die "Missing required file: $1"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

require_command "$PYTHON_BIN"
require_command systemctl
require_file "$ENV_FILE"
require_file "$SERVICE_TEMPLATE_SOURCE"

cd "$APP_DIR"

if [ ! -d ".venv" ]; then
  info "Creating Python virtualenv"
  "$PYTHON_BIN" -m venv .venv
fi

info "Installing backend dependencies"
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/pip install -e '.[dev]'

info "Running database migrations"
.venv/bin/alembic upgrade head

info "Installing systemd unit"
sudo cp "$SERVICE_TEMPLATE_SOURCE" "$SERVICE_TARGET"
sudo systemctl daemon-reload
sudo systemctl enable amo-crm-api.service

info "Restarting backend service"
sudo systemctl restart amo-crm-api.service

info "Checking service status"
sudo systemctl --no-pager --full status amo-crm-api.service || true

info "Verifying localhost health"
curl -fsS "http://127.0.0.1:${API_PORT}/health" || die "Backend health check failed on localhost:${API_PORT}"

info "Deployment finished"
