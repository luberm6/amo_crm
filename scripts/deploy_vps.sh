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
FREESWITCH_MANGO_GATEWAY_FILE="${FREESWITCH_MANGO_GATEWAY_FILE:-$FREESWITCH_CONF_DIR/sip_profiles/external/mango_primary.xml}"
FREESWITCH_INBOUND_DIALPLAN_FILE="${FREESWITCH_INBOUND_DIALPLAN_FILE:-$FREESWITCH_CONF_DIR/dialplan/public/00_amo_primary_inbound.xml}"
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
  log "Reloading FreeSWITCH XML and mod_sofia"
  "$FREESWITCH_FS_CLI" -p "$esl_password" -x "reloadxml" || true
  "$FREESWITCH_FS_CLI" -p "$esl_password" -x "reload mod_sofia" || true
  "$FREESWITCH_FS_CLI" -p "$esl_password" -x "sofia profile external start" || true
  sleep 3
  ss -ln 2>/dev/null | egrep ':(5080|5081)\b' || die "FreeSWITCH external SIP profile is still not listening on 5080/5081"
}

ensure_mango_gateway_config() {
  local sip_login sip_password sip_server sip_username sip_login_domain
  sip_login="$(get_env_value MANGO_SIP_LOGIN || true)"
  sip_password="$(get_env_value MANGO_SIP_PASSWORD || true)"
  sip_server="$(get_env_value MANGO_SIP_SERVER || true)"
  sip_username="${sip_login%@*}"
  if [[ "$sip_username" == "$sip_login" ]]; then
    sip_username="$sip_login"
  fi
  sip_login_domain=""
  if [[ "$sip_login" == *"@"* ]]; then
    sip_login_domain="${sip_login#*@}"
  fi
  if [[ -z "$sip_server" && -n "$sip_login_domain" ]]; then
    sip_server="$sip_login_domain"
  fi

  if [[ -z "$sip_username" || -z "$sip_password" || -z "$sip_server" ]]; then
    log "MANGO_SIP_* are not fully configured; skipping FreeSWITCH Mango gateway bootstrap"
    return 0
  fi

  mkdir -p "$(dirname "$FREESWITCH_MANGO_GATEWAY_FILE")"
  cat > "$FREESWITCH_MANGO_GATEWAY_FILE" <<EOF
<include>
  <gateway name="mango_primary">
    <param name="username" value="${sip_username}"/>
    <param name="realm" value="${sip_server}"/>
    <param name="from-user" value="${sip_username}"/>
    <param name="from-domain" value="${sip_server}"/>
    <param name="password" value="${sip_password}"/>
    <param name="extension" value="${sip_username}"/>
    <param name="proxy" value="${sip_server}"/>
    <param name="register-proxy" value="${sip_server}"/>
    <param name="expire-seconds" value="60"/>
    <param name="register" value="true"/>
    <param name="register-transport" value="udp"/>
    <param name="retry-seconds" value="30"/>
    <param name="ping" value="25"/>
    <param name="caller-id-in-from" value="true"/>
    <param name="extension-in-contact" value="true"/>
    <param name="contact-params" value="transport=udp"/>
  </gateway>
</include>
EOF
  log "Wrote FreeSWITCH Mango gateway config: $FREESWITCH_MANGO_GATEWAY_FILE"
}

ensure_freeswitch_inbound_dialplan() {
  local provider_secret primary_number backend_number sip_login sip_user route_regex
  provider_secret="$(get_env_value PROVIDER_SETTINGS_SECRET || true)"
  primary_number="$(get_env_value MANGO_PRIMARY_PHONE_NUMBER || true)"
  backend_number="$primary_number"
  if [[ "$primary_number" =~ ^8[0-9]{10}$ ]]; then
    backend_number="+7${primary_number#8}"
  elif [[ "$primary_number" =~ ^7[0-9]{10}$ ]]; then
    backend_number="+${primary_number}"
  fi
  sip_login="$(get_env_value MANGO_SIP_LOGIN || true)"
  sip_user="${sip_login%@*}"
  if [[ "$sip_user" == "$sip_login" ]]; then
    sip_user="$sip_login"
  fi

  if [[ -z "$provider_secret" || -z "$primary_number" || -z "$sip_user" ]]; then
    log "Inbound dialplan bootstrap skipped: PROVIDER_SETTINGS_SECRET, MANGO_PRIMARY_PHONE_NUMBER, or MANGO_SIP_LOGIN missing"
    return 0
  fi

  mkdir -p "$(dirname "$FREESWITCH_INBOUND_DIALPLAN_FILE")"
  route_regex="^(${primary_number}|7${primary_number#8}|\\+7${primary_number#8}|${sip_user}|11)$"

  cat > "$FREESWITCH_INBOUND_DIALPLAN_FILE" <<EOF
<include>
  <extension name="amo_primary_inbound">
    <condition field="destination_number" expression="${route_regex}">
      <action application="set" data="amo_primary_number=${primary_number}"/>
      <action application="set" data="hangup_after_bridge=false"/>
      <action application="set" data="continue_on_fail=true"/>
      <action application="answer"/>
      <action application="sleep" data="150"/>
      <action application="system" data="/usr/bin/curl -fsS -m 5 -X POST -H 'Content-Type: application/json' -H 'x-provider-settings-secret: ${provider_secret}' -d '{\"call_uuid\":\"\${uuid}\",\"to_number\":\"${backend_number}\",\"from_number\":\"\${caller_id_number}\",\"provider\":\"mango\",\"line_phone_number\":\"${backend_number}\"}' http://127.0.0.1:8000/v1/webhooks/freeswitch/inbound-sip > /tmp/amo_freeswitch_inbound_\${uuid}.log 2>&1"/>
      <action application="park"/>
    </condition>
  </extension>
</include>
EOF
  log "Wrote FreeSWITCH inbound dialplan: $FREESWITCH_INBOUND_DIALPLAN_FILE"
}

verify_mango_gateway_status() {
  local esl_password
  esl_password="$(get_env_value FREESWITCH_ESL_PASSWORD || true)"
  [[ -x "$FREESWITCH_FS_CLI" ]] || return 0
  [[ -n "$esl_password" ]] || return 0
  if [[ ! -f "$FREESWITCH_MANGO_GATEWAY_FILE" ]]; then
    return 0
  fi

  log "Checking FreeSWITCH Mango gateway status"
  "$FREESWITCH_FS_CLI" -p "$esl_password" -x "sofia status gateway mango_primary" || true
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

if [[ "${DEPLOY_REEXECED:-0}" != "1" ]]; then
  log "Re-executing deploy script from updated checkout"
  exec env DEPLOY_REEXECED=1 \
    APP_DIR="$APP_DIR" \
    BRANCH="$BRANCH" \
    SERVICE_NAME="$SERVICE_NAME" \
    API_HEALTH_URL="$API_HEALTH_URL" \
    FRONTEND_URL="$FRONTEND_URL" \
    PYTHON_BIN="$PYTHON_BIN" \
    NPM_BIN="$NPM_BIN" \
    ROLLBACK_ON_FAILURE="$ROLLBACK_ON_FAILURE" \
    HEALTH_WAIT_SECONDS="$HEALTH_WAIT_SECONDS" \
    ENV_FILE="$ENV_FILE" \
    FREESWITCH_CONF_DIR="$FREESWITCH_CONF_DIR" \
    FREESWITCH_EXTERNAL_PROFILE="$FREESWITCH_EXTERNAL_PROFILE" \
    FREESWITCH_EXTERNAL_PROFILE_OFF="$FREESWITCH_EXTERNAL_PROFILE_OFF" \
    FREESWITCH_FS_CLI="$FREESWITCH_FS_CLI" \
    VPS_PUBLIC_IP="$VPS_PUBLIC_IP" \
    bash "$0"
fi

log "Enforcing colocated FreeSWITCH environment"
ensure_env_value "BACKEND_URL" "http://84.247.184.72"
ensure_env_value "MANGO_PRIMARY_PHONE_NUMBER" "89300350609"
ensure_env_value "MANGO_FROM_EXT" "11"
ensure_env_value "FREESWITCH_ESL_HOST" "127.0.0.1"
ensure_env_value "FREESWITCH_RTP_IP" "127.0.0.1"
grep -E '^(BACKEND_URL|MANGO_PRIMARY_PHONE_NUMBER|MANGO_FROM_EXT|FREESWITCH_ESL_HOST|FREESWITCH_RTP_IP)=' "$ENV_FILE" || true
ensure_freeswitch_public_profile
ensure_mango_gateway_config
ensure_freeswitch_inbound_dialplan
reload_freeswitch_profile
verify_mango_gateway_status

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
