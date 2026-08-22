#!/usr/bin/env bash
set -Eeuo pipefail

# Install the resident Assistant Lab worker on the already-provisioned Oracle DDS3 VM.
# This script is intentionally fail-closed and does not create or modify the OCI VM,
# firewall, school canon, production bridge rules, or student/profile data.
#
# Required protected input:
#   ASSISTANT_LAB_DATABASE_URL  dedicated Neon login DSN
# Optional:
#   ASSISTANT_LAB_EXPECTED_DB_USER=assistant_lab_worker_principal
#   ASSISTANT_LAB_ACTIVATE=1    enable/start worker after all checks pass

LAB_USER="${ASSISTANT_LAB_UNIX_USER:-assistant-lab}"
LAB_GROUP="${ASSISTANT_LAB_UNIX_GROUP:-assistant-lab}"
LAB_DIR="${ASSISTANT_LAB_DIR:-/opt/bridge-school/assistant-lab}"
REPO_DIR="${ASSISTANT_LAB_REPO_DIR:-/opt/bridge-school/bridge-video-free}"
SERVICE_NAME="${ASSISTANT_LAB_SERVICE_NAME:-assistant-lab.service}"
SERVICE_SRC="$REPO_DIR/deploy/oracle-assistant-lab/assistant-lab.service"
SERVICE_DST="/etc/systemd/system/$SERVICE_NAME"
DDS3_ENV="${DDS3_RUNTIME_ENV_FILE:-/opt/bridge-school/dds3-runtime.env}"
EXPECTED_DB_USER="${ASSISTANT_LAB_EXPECTED_DB_USER:-assistant_lab_worker_principal}"
ACTIVATE="${ASSISTANT_LAB_ACTIVATE:-0}"

log(){ printf '\n[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "run as root on the existing Oracle DDS3 host"
[[ -n "${ASSISTANT_LAB_DATABASE_URL:-}" ]] || die "ASSISTANT_LAB_DATABASE_URL is required as protected input"
[[ "$ACTIVATE" =~ ^[01]$ ]] || die "ASSISTANT_LAB_ACTIVATE must be 0 or 1"
[[ -d "$REPO_DIR/.git" ]] || die "expected repository checkout not found at $REPO_DIR"
[[ -f "$REPO_DIR/assistant_lab/worker.py" ]] || die "Assistant Lab worker code missing from repository checkout"
[[ -f "$SERVICE_SRC" ]] || die "systemd unit template missing: $SERVICE_SRC"
[[ -f "$DDS3_ENV" ]] || die "DDS3 runtime environment file missing: $DDS3_ENV"
command -v curl >/dev/null 2>&1 || die "curl is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required"
command -v systemctl >/dev/null 2>&1 || die "systemd is required"

log "Verify hot localhost DDS3 before installing worker"
READY_JSON="$(curl -fsS --max-time 10 http://127.0.0.1:8080/readyz)" || die "local DDS3 /readyz is unavailable"
printf '%s' "$READY_JSON" | python3 -c '
import json,sys
x=json.load(sys.stdin)
assert x.get("status")=="ready", x
assert x.get("engine")=="DDS3", x
assert x.get("fallback_used") is False, x
' || die "local DDS3 readiness provenance is invalid"

log "Create isolated Unix identity and working directory"
if ! getent group "$LAB_GROUP" >/dev/null 2>&1; then
  groupadd --system "$LAB_GROUP"
fi
if ! id "$LAB_USER" >/dev/null 2>&1; then
  useradd --system --gid "$LAB_GROUP" --home-dir "$LAB_DIR" --shell /usr/sbin/nologin "$LAB_USER"
fi
install -d -m 0750 -o "$LAB_USER" -g "$LAB_GROUP" "$LAB_DIR"

log "Create bounded Python runtime"
python3 -m venv "$LAB_DIR/.venv"
"$LAB_DIR/.venv/bin/python" -m pip install --disable-pip-version-check --no-cache-dir \
  'psycopg[binary]==3.3.4' >/dev/null
chown -R "$LAB_USER:$LAB_GROUP" "$LAB_DIR/.venv"

log "Validate dedicated Neon DSN before persisting it"
ASSISTANT_LAB_EXPECTED_DB_USER="$EXPECTED_DB_USER" \
ASSISTANT_LAB_DATABASE_URL="$ASSISTANT_LAB_DATABASE_URL" \
PYTHONPATH="$REPO_DIR" \
"$LAB_DIR/.venv/bin/python" - <<'PY'
import os
import psycopg
from assistant_lab.worker import validate_neon_dsn

dsn=validate_neon_dsn(os.environ["ASSISTANT_LAB_DATABASE_URL"])
with psycopg.connect(dsn, connect_timeout=10, application_name="assistant-lab-install-preflight") as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT current_user, has_schema_privilege(current_user,'assistant_lab','USAGE'), has_table_privilege(current_user,'assistant_lab.job','SELECT'), has_table_privilege(current_user,'assistant_lab.job','UPDATE'), has_table_privilege(current_user,'assistant_lab.job','INSERT')")
        user, usage, can_select, can_update, can_insert=cur.fetchone()
        expected=os.environ["ASSISTANT_LAB_EXPECTED_DB_USER"]
        assert user==expected, (user, expected)
        assert usage and can_select and can_update and not can_insert, (usage,can_select,can_update,can_insert)
print("ASSISTANT_LAB_DB_PREFLIGHT_PASS")
PY

log "Verify dedicated Unix user can read immutable application code"
runuser -u "$LAB_USER" -- test -r "$REPO_DIR/assistant_lab/worker.py" \
  || die "$LAB_USER cannot read repository code at $REPO_DIR"

log "Write root-owned secret environment without printing credentials"
umask 077
cat >"$LAB_DIR/assistant-lab.env" <<EOF
ASSISTANT_LAB_DATABASE_URL=$ASSISTANT_LAB_DATABASE_URL
ASSISTANT_LAB_EXPECTED_DB_USER=$EXPECTED_DB_USER
ASSISTANT_LAB_WORKER_ID=oracle-assistant-lab-1
ASSISTANT_LAB_WAKE_TIMEOUT_SECONDS=2
ASSISTANT_LAB_STALE_AFTER_SECONDS=900
ASSISTANT_LAB_HEARTBEAT_INTERVAL_SECONDS=30
ASSISTANT_LAB_DDS3_URL=http://127.0.0.1:8080/v1/compute
ASSISTANT_LAB_DDS3_TIMEOUT_SECONDS=25
EOF
chown root:root "$LAB_DIR/assistant-lab.env"
chmod 0600 "$LAB_DIR/assistant-lab.env"

log "Install hardened systemd unit"
install -m 0644 -o root -g root "$SERVICE_SRC" "$SERVICE_DST"
systemctl daemon-reload
systemd-analyze verify "$SERVICE_DST" >/dev/null

if [[ "$ACTIVATE" == "1" ]]; then
  log "Enable resident Assistant Lab worker"
  systemctl enable --now "$SERVICE_NAME"
  sleep 2
  systemctl is-active --quiet "$SERVICE_NAME" || {
    journalctl -u "$SERVICE_NAME" -n 40 --no-pager >&2 || true
    die "Assistant Lab service failed to become active"
  }
  printf 'ASSISTANT_LAB_INSTALL_PASS activated=1\n'
else
  log "Stage complete; service intentionally not enabled"
  systemctl disable --now "$SERVICE_NAME" >/dev/null 2>&1 || true
  printf 'ASSISTANT_LAB_INSTALL_PASS activated=0\n'
fi
