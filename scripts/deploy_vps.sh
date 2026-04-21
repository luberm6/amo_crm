#!/usr/bin/env bash
set -Eeuo pipefail

log() { echo; echo "[deploy] $*"; }
warn() { echo; echo "[warn] $*" >&2; }
die() { echo; echo "[error] $*" >&2; exit 1; }

APP_DIR="${APP_DIR:-/opt/amo_crm}"
BRANCH="${BRANCH:-main}"
SERVICE_NAME="${SERVICE_NAME:-amo-crm-api}"
API_HEALTH_URL="${API_HEALTH_URL:-http://127.0.0.1:8000/health}"
FRONTEND_URL="${FRONTEND_URL:-http://127.0.0.1/}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
NPM_BIN="${NPM_BIN:-npm}"
ROLLBACK_ON_FAILURE="${ROLLBACK_ON_FAILURE:-1}"
HEALTH_WAIT_SECONDS="${HEALTH_WAIT_SECONDS:-30}"
ENV_FILE="${ENV_FILE:-/opt/amo_crm/.env}"
FREESWITCH_CONF_DIR="${FREESWITCH_CONF_DIR:-/usr/local/freeswitch/conf}"
FREESWITCH_EXTERNAL_PROFILE="${FREESWITCH_EXTERNAL_PROFILE:-$FREESWITCH_CONF_DIR/sip_profiles/external.xml}"
FREESWITCH_EXTERNAL_PROFILE_OFF="${FREESWITCH_EXTERNAL_PROFILE_OFF:-$FREESWITCH_CONF_DIR/sip_profiles/external.xml.off}"
FREESWITCH_FS_CLI="${FREESWITCH_FS_CLI:-/usr/local/freeswitch/bin/fs_cli}"
VPS_PUBLIC_IP="${VPS_PUBLIC_IP:-84.247.184.72}"

LOCK_FILE="/tmp/amo_crm_deploy.lock"
ROLLBACK_REV=""
DEPLOY_STARTED=0

dump_diagnostics() {
  warn "Collecting diagnostics"
  (cd "$APP_DIR" && git rev-parse --short HEAD) 2>/dev/null | sed 's/^/[git] current rev: /' || true
  sudo systemctl --no-pager --full status "$SERVICE_NAME" || true
  sudo journalctl -u "$SERVICE_NAME" -n 120 --no-pager || true
  sudo nginx -t || true
  curl -fsS "$API_HEALTH_URL" || true
  curl -fsS "$FRONTEND_URL" || true
}

rollback() {
  if [[ "$ROLLBACK_ON_FAILURE" != "1" || -z "$ROLLBACK_REV" || "$DEPLOY_STARTED" != "1" ]]; then
    dump_diagnostics
    return
  fi

  warn "Rolling back to ${ROLLBACK_REV}"
  (
    cd "$APP_DIR"
    git reset --hard "$ROLLBACK_REV"

    if [[ ! -d .venv ]]; then
      "$PYTHON_BIN" -m venv .venv
    fi
    .venv/bin/python -m pip install --upgrade pip setuptools wheel >/dev/null
    .venv/bin/pip install -e .

    if [[ -f admin-panel/package-lock.json ]]; then
      "$NPM_BIN" --prefix admin-panel ci
    else
      "$NPM_BIN" --prefix admin-panel install
    fi
    "$NPM_BIN" --prefix admin-panel run build

    .venv/bin/alembic upgrade head
    sudo systemctl restart "$SERVICE_NAME"
    sudo nginx -t
    sudo systemctl reload nginx
  ) || warn "Rollback steps failed"

  dump_diagnostics
}

on_error() {
  local exit_code=$?
  warn "Deploy failed on line ${BASH_LINENO[0]} with exit code ${exit_code}"
  rollback
  exit "$exit_code"
}

trap on_error ERR

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

require_command "$PYTHON_BIN"
require_command "$NPM_BIN"
require_command git
require_command curl
require_command sudo
require_command flock
require_command systemctl
require_command nginx

wait_for_http() {
  local url="$1"
  local timeout="${2:-30}"
  local started_at
  started_at="$(date +%s)"

  while true; do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi

    if (( "$(date +%s)" - started_at >= timeout )); then
      return 1
    fi

    sleep 1
  done
}

ensure_env_value() {
  local key="$1"
  local value="$2"
  [[ -f "$ENV_FILE" ]] || return 0
  python3 - "$ENV_FILE" "$key" "$value" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
lines = path.read_text().splitlines()
updated = False
result = []
for line in lines:
    if line.startswith(f"{key}="):
        result.append(f"{key}='{value}'")
        updated = True
    else:
        result.append(line)
if not updated:
    result.append(f"{key}='{value}'")
path.write_text("\n".join(result) + "\n")
PY
}

get_env_value() {
  local key="$1"
  [[ -f "$ENV_FILE" ]] || return 1
  python3 - "$ENV_FILE" "$key" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
for line in path.read_text().splitlines():
    if not line.startswith(f"{key}="):
        continue
    print(line.split("=", 1)[1].strip().strip("\"'"))
    raise SystemExit(0)
raise SystemExit(1)
PY
}

ensure_freeswitch_public_profile() {
  if [[ ! -f "$FREESWITCH_EXTERNAL_PROFILE" && -f "$FREESWITCH_EXTERNAL_PROFILE_OFF" ]]; then
    log "Enabling FreeSWITCH external SIP profile"
    cp "$FREESWITCH_EXTERNAL_PROFILE_OFF" "$FREESWITCH_EXTERNAL_PROFILE"
  fi
  [[ -f "$FREESWITCH_EXTERNAL_PROFILE" ]] || return 0
  python3 - "$FREESWITCH_EXTERNAL_PROFILE" "$VPS_PUBLIC_IP" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
public_ip = sys.argv[2]
text = path.read_text()
for name in ("rtp-ip", "sip-ip", "ext-rtp-ip", "ext-sip-ip"):
    pattern = rf'(<param\s+name="{re.escape(name)}"\s+value=")([^"]*)(")'
    text = re.sub(pattern, rf'\g<1>{public_ip}\g<3>', text)
path.write_text(text)
PY
}

reload_freeswitch_profile() {
  local esl_password
  if [[ ! -x "$FREESWITCH_FS_CLI" ]]; then
    warn "fs_cli not found at $FREESWITCH_FS_CLI; skipping FreeSWITCH reload"
    return 0
  fi
  esl_password="$(get_env_value FREESWITCH_ESL_PASSWORD || true)"
  if [[ -z "$esl_password" ]]; then
    warn "FREESWITCH_ESL_PASSWORD is unavailable; skipping FreeSWITCH reload"
    return 0
  fi
  log "Reloading FreeSWITCH XML and external SIP profile"
  "$FREESWITCH_FS_CLI" -p "$esl_password" -x "reloadxml" || true
  "$FREESWITCH_FS_CLI" -p "$esl_password" -x "sofia profile external restart reloadxml" || \
    "$FREESWITCH_FS_CLI" -p "$esl_password" -x "sofia profile external start" || true
  sleep 2
  ss -ln 2>/dev/null | egrep ':(5080|5081)\b' || die "FreeSWITCH external SIP profile is still not listening on 5080/5081"
}

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  die "Another deploy is already running"
fi

[[ -d "$APP_DIR/.git" ]] || die "Repo not found at $APP_DIR"

cd "$APP_DIR"
ROLLBACK_REV="$(git rev-parse HEAD)"
DEPLOY_STARTED=1

log "Starting deploy in $APP_DIR from branch $BRANCH"
log "Current revision: $ROLLBACK_REV"

log "Fetching latest code"
git fetch origin "$BRANCH" --prune
git reset --hard "origin/$BRANCH"

log "Enforcing colocated FreeSWITCH environment"
ensure_env_value "BACKEND_URL" "http://84.247.184.72"
ensure_env_value "MANGO_PRIMARY_PHONE_NUMBER" "89300350609"
ensure_env_value "MANGO_FROM_EXT" "11"
ensure_env_value "FREESWITCH_ESL_HOST" "127.0.0.1"
ensure_env_value "FREESWITCH_RTP_IP" "127.0.0.1"
grep -E '^(BACKEND_URL|MANGO_PRIMARY_PHONE_NUMBER|MANGO_FROM_EXT|FREESWITCH_ESL_HOST|FREESWITCH_RTP_IP)=' "$ENV_FILE" || true
ensure_freeswitch_public_profile
reload_freeswitch_profile

log "Ensuring Python virtualenv"
if [[ ! -d .venv ]]; then
  "$PYTHON_BIN" -m venv .venv
fi

log "Installing backend dependencies"
.venv/bin/python -m pip install --upgrade pip setuptools wheel >/dev/null
.venv/bin/pip install -e .

log "Installing frontend dependencies"
if [[ -f admin-panel/package-lock.json ]]; then
  "$NPM_BIN" --prefix admin-panel ci
else
  "$NPM_BIN" --prefix admin-panel install
fi

log "Building admin panel"
"$NPM_BIN" --prefix admin-panel run build

log "Running database migrations"
.venv/bin/alembic upgrade head

log "Restarting backend service"
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl --no-pager --full status "$SERVICE_NAME"

log "Validating backend health"
wait_for_http "$API_HEALTH_URL" "$HEALTH_WAIT_SECONDS"
curl -fsS "$API_HEALTH_URL"

log "Validating nginx config"
sudo nginx -t

log "Reloading nginx"
sudo systemctl reload nginx

log "Validating frontend root"
curl -fsS "$FRONTEND_URL" >/dev/null

log "Deploy completed successfully"
echo "[deploy] active revision: $(git rev-parse HEAD)"
