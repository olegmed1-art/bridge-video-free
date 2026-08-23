#!/usr/bin/env bash
set -Eeuo pipefail

# Install the outbound Neon-backed bridge that links ChatGPT's Neon connector to
# the localhost-only Assistant Lab Control API. No public Oracle port is opened.
# Requires the control_command schema to already exist in Neon.
# The bridge deliberately reuses the canonical Assistant Lab DB environment file
# instead of creating a second copy of the Neon credential.

OBS_USER="${ASSISTANT_LAB_OBSERVER_UNIX_USER:-assistant-lab-observer}"
OBS_GROUP="${ASSISTANT_LAB_OBSERVER_UNIX_GROUP:-assistant-lab-observer}"
OBS_DIR="${ASSISTANT_LAB_OBSERVER_DIR:-/opt/bridge-school/assistant-lab-observer}"
REPO_DIR="${ASSISTANT_LAB_REPO_DIR:-/opt/bridge-school/bridge-video-free}"
SOURCE_ENV="${ASSISTANT_LAB_SOURCE_ENV:-/opt/bridge-school/assistant-lab/assistant-lab.env}"
SERVICE_NAME="${ASSISTANT_LAB_CONTROL_BRIDGE_SERVICE_NAME:-assistant-lab-control-bridge.service}"
SERVICE_SRC="$REPO_DIR/deploy/oracle-assistant-lab/assistant-lab-control-bridge.service"
SERVICE_DST="/etc/systemd/system/$SERVICE_NAME"
ACTIVATE="${ASSISTANT_LAB_CONTROL_BRIDGE_ACTIVATE:-0}"
EXPECTED_DB_USER="${ASSISTANT_LAB_EXPECTED_DB_USER:-assistant_lab_worker_principal}"
PSYCOPG_VERSION="${ASSISTANT_LAB_PSYCOPG_VERSION:-3.2.13}"

log(){ printf '\n[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "run as root on the existing Oracle host"
[[ "$ACTIVATE" =~ ^[01]$ ]] || die "ASSISTANT_LAB_CONTROL_BRIDGE_ACTIVATE must be 0 or 1"
[[ -d "$REPO_DIR/.git" ]] || die "repository checkout missing: $REPO_DIR"
[[ -f "$REPO_DIR/assistant_lab/control_bridge.py" ]] || die "control bridge code missing"
[[ -f "$SERVICE_SRC" ]] || die "control bridge systemd unit missing"
[[ -f "$SOURCE_ENV" ]] || die "existing Assistant Lab environment missing"
id "$OBS_USER" >/dev/null 2>&1 || die "observer Unix identity is not installed"
systemctl is-active --quiet assistant-lab-observer.service || die "observer service is not active"
systemctl is-active --quiet assistant-lab-control.service || die "control API service is not active"
command -v python3 >/dev/null 2>&1 || die "python3 is required"
command -v systemctl >/dev/null 2>&1 || die "systemd is required"
command -v systemd-analyze >/dev/null 2>&1 || die "systemd-analyze is required"

# Fail closed if the canonical credential source is broadly readable.
mode="$(stat -c '%a' "$SOURCE_ENV")"
case "$mode" in
  600|640) ;;
  *) die "canonical Assistant Lab environment must be mode 600 or 640, got $mode" ;;
esac

DB_URL="$(sed -n 's/^ASSISTANT_LAB_DATABASE_URL=//p' "$SOURCE_ENV" | head -n1)"
[[ -n "$DB_URL" ]] || die "ASSISTANT_LAB_DATABASE_URL not found in existing Assistant Lab environment"

log "Install bounded database client into existing observer runtime"
[[ -x "$OBS_DIR/.venv/bin/python" ]] || die "observer Python runtime missing"
"$OBS_DIR/.venv/bin/python" -m pip install --disable-pip-version-check --no-cache-dir \
  "psycopg[binary]==$PSYCOPG_VERSION" >/dev/null

log "Validate dedicated principal and control queue privileges"
ASSISTANT_LAB_DATABASE_URL="$DB_URL" EXPECTED_DB_USER="$EXPECTED_DB_USER" \
  "$OBS_DIR/.venv/bin/python" - <<'PY'
import os
import psycopg
with psycopg.connect(os.environ["ASSISTANT_LAB_DATABASE_URL"], connect_timeout=10, application_name="assistant-lab-control-bridge-install") as conn:
    with conn.cursor() as cur:
        cur.execute("""
        SELECT current_user,
               to_regclass('assistant_lab.control_command') IS NOT NULL,
               has_table_privilege(current_user,'assistant_lab.control_command','SELECT'),
               has_table_privilege(current_user,'assistant_lab.control_command','UPDATE'),
               has_table_privilege(current_user,'assistant_lab.control_command','INSERT')
        """)
        user, exists, can_select, can_update, can_insert = cur.fetchone()
        assert user == os.environ["EXPECTED_DB_USER"], (user, os.environ["EXPECTED_DB_USER"])
        assert exists, "assistant_lab.control_command missing"
        assert can_select and can_update and not can_insert, (can_select, can_update, can_insert)
print("ASSISTANT_LAB_CONTROL_BRIDGE_DB_PREFLIGHT_PASS")
PY

# Remove the obsolete duplicate secret file from earlier drafts, if present.
rm -f "$OBS_DIR/control-bridge.env"

log "Compile and install hardened resident bridge"
PYTHONPATH="$REPO_DIR" "$OBS_DIR/.venv/bin/python" -m py_compile "$REPO_DIR/assistant_lab/control_bridge.py"
install -m 0644 -o root -g root "$SERVICE_SRC" "$SERVICE_DST"
systemctl daemon-reload
systemd-analyze verify "$SERVICE_DST" >/dev/null

if [[ "$ACTIVATE" == "1" ]]; then
  systemctl enable "$SERVICE_NAME"
  systemctl restart "$SERVICE_NAME"
  sleep 2
  systemctl is-enabled --quiet "$SERVICE_NAME" || die "control bridge is not enabled"
  systemctl is-active --quiet "$SERVICE_NAME" || {
    journalctl -u "$SERVICE_NAME" -n 60 --no-pager >&2 || true
    die "control bridge failed to become active"
  }
  printf 'ASSISTANT_LAB_CONTROL_BRIDGE_INSTALL_PASS activated=1 public_port=none duplicate_db_secret=none\n'
else
  state="$(systemctl is-active "$SERVICE_NAME" 2>/dev/null || true)"
  printf 'ASSISTANT_LAB_CONTROL_BRIDGE_INSTALL_PASS activated=0 state=%s duplicate_db_secret=none\n' "${state:-unknown}"
fi
