#!/usr/bin/env bash
set -Eeuo pipefail

# Install and activate a separate SHADOW_ONLY online observer without stopping
# or restarting the existing Oracle Autopilot consumer or the Oracle instance.

OBSERVER_USER="${AUTOPILOT_UNIX_USER:-school-autopilot}"
OBSERVER_GROUP="${AUTOPILOT_UNIX_GROUP:-school-autopilot}"
OBSERVER_ROOT="${AUTOPILOT_OBSERVER_DIR:-/opt/bridge-school/school-autopilot-online-observer}"
CONSUMER_ROOT="${AUTOPILOT_DIR:-/opt/bridge-school/school-autopilot}"
REPO_DIR="${AUTOPILOT_REPO_DIR:-/opt/bridge-school/bridge-video-free-observer-stage}"
SOURCE_REVISION="${AUTOPILOT_OBSERVER_SOURCE_REVISION:-}"
EXPECTED_CONSUMER_REVISION="${AUTOPILOT_EXPECTED_CONSUMER_REVISION:-}"
SERVICE_NAME="school-autopilot-online-observer.service"
CONSUMER_SERVICE="school-autopilot-shadow.service"
SERVICE_SRC="$REPO_DIR/deploy/oracle-autopilot/$SERVICE_NAME"
SERVICE_DST="/etc/systemd/system/$SERVICE_NAME"
RELEASE_DIR="$OBSERVER_ROOT/releases/$SOURCE_REVISION"
CURRENT_LINK="$OBSERVER_ROOT/current"
ENV_FILE="$CONSUMER_ROOT/autopilot-shadow.env"
VENV_PYTHON="$CONSUMER_ROOT/.venv/bin/python"

log(){ printf '\n[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "run as root on the existing Oracle host"
[[ "$SOURCE_REVISION" =~ ^[0-9a-f]{40}$ ]] \
  || die "AUTOPILOT_OBSERVER_SOURCE_REVISION must be a pinned commit"
[[ "$EXPECTED_CONSUMER_REVISION" =~ ^[0-9a-f]{40}$ ]] \
  || die "AUTOPILOT_EXPECTED_CONSUMER_REVISION must be a pinned commit"
[[ -s "$REPO_DIR/AUTOPILOT_OBSERVER_SOURCE_REVISION" ]] \
  || die "observer source revision marker is missing"
[[ "$(cat "$REPO_DIR/AUTOPILOT_OBSERVER_SOURCE_REVISION")" == "$SOURCE_REVISION" ]] \
  || die "observer source revision marker mismatch"
[[ -f "$REPO_DIR/oracle_autopilot/online_observer.py" ]] \
  || die "online observer source is missing"
[[ -f "$REPO_DIR/oracle_autopilot/worker.py" ]] \
  || die "shared DSN validator source is missing"
[[ -f "$REPO_DIR/autopilot_phase3b/policy.py" ]] \
  || die "Phase 3B policy dependency is missing"
[[ -f "$SERVICE_SRC" ]] || die "online observer unit is missing"
[[ -x "$VENV_PYTHON" ]] || die "verified Autopilot Python runtime is missing"
[[ "$(stat -c '%U:%G:%a' "$ENV_FILE")" == root:root:600 ]] \
  || die "Autopilot environment metadata mismatch"
[[ "$(systemctl is-active "$CONSUMER_SERVICE" 2>/dev/null || true)" == active ]] \
  || die "existing Autopilot consumer must remain active"
[[ "$(systemctl is-enabled "$CONSUMER_SERVICE" 2>/dev/null || true)" == enabled ]] \
  || die "existing Autopilot consumer must remain enabled"
[[ "$(cat "$CONSUMER_ROOT/current/SOURCE_REVISION")" == "$EXPECTED_CONSUMER_REVISION" ]] \
  || die "existing Autopilot consumer revision mismatch"
[[ "$(systemctl is-active "$SERVICE_NAME" 2>/dev/null || true)" != active ]] \
  || die "online observer is already active"
[[ "$(systemctl is-enabled "$SERVICE_NAME" 2>/dev/null || true)" != enabled ]] \
  || die "online observer is already enabled"

database_url="$(sed -n 's/^AUTOPILOT_DATABASE_URL=//p' "$ENV_FILE")"
expected_db_user="$(sed -n 's/^AUTOPILOT_EXPECTED_DB_USER=//p' "$ENV_FILE")"
[[ -n "$database_url" && -n "$expected_db_user" ]] \
  || die "Autopilot protected database environment is incomplete"

log "Validate least-privilege online pilot RPC boundary"
AUTOPILOT_DATABASE_URL="$database_url" \
AUTOPILOT_EXPECTED_DB_USER="$expected_db_user" \
PYTHONPATH="$REPO_DIR" \
"$VENV_PYTHON" - <<'PY'
import os
import psycopg
from oracle_autopilot.worker import validate_neon_direct_dsn

dsn = validate_neon_direct_dsn(os.environ["AUTOPILOT_DATABASE_URL"])
with psycopg.connect(
    dsn,
    connect_timeout=10,
    application_name="autopilot-online-observer-install-preflight",
) as conn:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT current_user,
                   has_table_privilege(current_user, 'autopilot.task', 'SELECT'),
                   has_table_privilege(current_user, 'autopilot.online_pilot_state', 'SELECT'),
                   has_function_privilege(
                       current_user,
                       'autopilot.online_pilot_tick(text,integer,integer)',
                       'EXECUTE'
                   ),
                   has_function_privilege(
                       current_user,
                       'autopilot.online_pilot_status()',
                       'EXECUTE'
                   )
        """)
        user, task_select, state_select, can_tick, can_status = cur.fetchone()
assert user == os.environ["AUTOPILOT_EXPECTED_DB_USER"], (user, "unexpected")
assert not task_select and not state_select and can_tick and can_status
print("AUTOPILOT_ONLINE_DB_PREFLIGHT_PASS")
PY

log "Install immutable observer release"
install -d -m 0755 -o root -g root "$OBSERVER_ROOT/releases"
install -d -m 0755 -o root -g root "$RELEASE_DIR/oracle_autopilot"
install -m 0644 -o root -g root "$REPO_DIR"/oracle_autopilot/*.py \
  "$RELEASE_DIR/oracle_autopilot/"
install -d -m 0755 -o root -g root "$RELEASE_DIR/autopilot_phase3b"
install -m 0644 -o root -g root "$REPO_DIR"/autopilot_phase3b/*.py \
  "$RELEASE_DIR/autopilot_phase3b/"
printf '%s\n' "$SOURCE_REVISION" >"$RELEASE_DIR/SOURCE_REVISION"
chmod 0444 "$RELEASE_DIR/SOURCE_REVISION"

ln -sfn "$RELEASE_DIR" "$OBSERVER_ROOT/current.next"
mv -Tf "$OBSERVER_ROOT/current.next" "$CURRENT_LINK"

log "Install and activate isolated hardened unit"
install -m 0644 -o root -g root "$SERVICE_SRC" "$SERVICE_DST"
systemctl daemon-reload
systemd-analyze verify "$SERVICE_DST" >/dev/null

rollback_observer() {
  rc=$?
  if [[ $rc -ne 0 ]]; then
    systemctl disable --now "$SERVICE_NAME" >/dev/null 2>&1 || true
    rm -f "$SERVICE_DST" "$CURRENT_LINK"
    systemctl daemon-reload >/dev/null 2>&1 || true
    echo 'AUTOPILOT_ONLINE_OBSERVER_ROLLED_BACK' >&2
  fi
  exit "$rc"
}
trap rollback_observer EXIT

systemctl enable --now "$SERVICE_NAME"
sleep 3
systemctl is-active --quiet "$SERVICE_NAME"
systemctl is-enabled --quiet "$SERVICE_NAME"
[[ "$(systemctl is-active "$CONSUMER_SERVICE")" == active ]]
[[ "$(systemctl is-enabled "$CONSUMER_SERVICE")" == enabled ]]
trap - EXIT

echo 'AUTOPILOT_ONLINE_OBSERVER_INSTALL_PASS'
echo 'AUTOPILOT_RUNTIME_MODE=SHADOW_ONLY'
echo 'AUTOPILOT_CONSUMER_RESTARTED=NO'
echo 'ORACLE_INSTANCE_STOP_REQUESTED=NO'
