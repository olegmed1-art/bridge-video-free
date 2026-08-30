#!/usr/bin/env bash
set -Eeuo pipefail

# Stage or activate the Oracle-resident Autopilot Lite shadow worker.
#
# Required protected input:
#   AUTOPILOT_DATABASE_URL  direct (non-pooler) Neon DSN for the dedicated login
#
# Optional:
#   AUTOPILOT_EXPECTED_DB_USER=autopilot_runtime_login
#   AUTOPILOT_SOURCE_REVISION=<40-character git commit>
#   AUTOPILOT_ACTIVATE=0|1
#   AUTOPILOT_ACTIVATION_SCOPE=SHADOW_ONLY (required when ACTIVATE=1)
#
# The script cannot create a Neon login, apply a database migration, run media,
# call a model, or enable production mutations.

AUTOPILOT_USER="${AUTOPILOT_UNIX_USER:-school-autopilot}"
AUTOPILOT_GROUP="${AUTOPILOT_UNIX_GROUP:-school-autopilot}"
AUTOPILOT_DIR="${AUTOPILOT_DIR:-/opt/bridge-school/school-autopilot}"
REPO_DIR="${AUTOPILOT_REPO_DIR:-/opt/bridge-school/bridge-video-free}"
SOURCE_REVISION="${AUTOPILOT_SOURCE_REVISION:-}"
RELEASES_DIR="$AUTOPILOT_DIR/releases"
RELEASE_DIR="$RELEASES_DIR/$SOURCE_REVISION"
CURRENT_LINK="$AUTOPILOT_DIR/current"
RUNTIME_DIR="$AUTOPILOT_DIR/runtime"
SERVICE_NAME="${AUTOPILOT_SERVICE_NAME:-school-autopilot-shadow.service}"
SERVICE_SRC="$REPO_DIR/deploy/oracle-autopilot/school-autopilot-shadow.service"
SERVICE_DST="/etc/systemd/system/$SERVICE_NAME"
EXPECTED_DB_USER="${AUTOPILOT_EXPECTED_DB_USER:-autopilot_runtime_login}"
ACTIVATE="${AUTOPILOT_ACTIVATE:-0}"
ACTIVATION_SCOPE="${AUTOPILOT_ACTIVATION_SCOPE:-}"
PSYCOPG_VERSION="${AUTOPILOT_PSYCOPG_VERSION:-3.3.4}"

log(){ printf '\n[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "run as root on the existing Oracle host"
[[ -n "${AUTOPILOT_DATABASE_URL:-}" ]] || die "AUTOPILOT_DATABASE_URL is required as protected input"
[[ "$SOURCE_REVISION" =~ ^[0-9a-f]{40}$ ]] || die "AUTOPILOT_SOURCE_REVISION must be a pinned commit"
[[ "$ACTIVATE" =~ ^[01]$ ]] || die "AUTOPILOT_ACTIVATE must be 0 or 1"
[[ "$PSYCOPG_VERSION" =~ ^3\.[23]\.[0-9]+$ ]] || die "psycopg must stay on a verified 3.2/3.3 line"
if [[ "$ACTIVATE" == "1" && "$ACTIVATION_SCOPE" != "SHADOW_ONLY" ]]; then
  die "AUTOPILOT_ACTIVATION_SCOPE=SHADOW_ONLY is required for activation"
fi
[[ -f "$REPO_DIR/AUTOPILOT_SOURCE_REVISION" ]] \
  || die "pinned source revision marker not found at $REPO_DIR"
[[ "$(cat "$REPO_DIR/AUTOPILOT_SOURCE_REVISION")" == "$SOURCE_REVISION" ]] \
  || die "source revision marker does not match AUTOPILOT_SOURCE_REVISION"
[[ -f "$REPO_DIR/oracle_autopilot/worker.py" ]] || die "Oracle Autopilot worker code is missing"
[[ -f "$SERVICE_SRC" ]] || die "systemd unit template is missing"
command -v python3 >/dev/null 2>&1 || die "python3 is required"
command -v systemctl >/dev/null 2>&1 || die "systemd is required"
command -v systemd-analyze >/dev/null 2>&1 || die "systemd-analyze is required"

if [[ "$ACTIVATE" == "0" ]]; then
  current_state="$(systemctl is-active "$SERVICE_NAME" 2>/dev/null || true)"
  enabled_state="$(systemctl is-enabled "$SERVICE_NAME" 2>/dev/null || true)"
  [[ "$current_state" != "active" && "$current_state" != "activating" ]] \
    || die "staging refuses to replace an active service"
  [[ "$enabled_state" != "enabled" && "$enabled_state" != "enabled-runtime" ]] \
    || die "staging refuses to retain an enabled service"
fi

log "Create isolated Unix identity and runtime directory"
if ! getent group "$AUTOPILOT_GROUP" >/dev/null 2>&1; then
  groupadd --system "$AUTOPILOT_GROUP"
fi
if ! id "$AUTOPILOT_USER" >/dev/null 2>&1; then
  useradd --system --gid "$AUTOPILOT_GROUP" --home-dir "$AUTOPILOT_DIR" --shell /usr/sbin/nologin "$AUTOPILOT_USER"
fi
install -d -m 0750 -o root -g "$AUTOPILOT_GROUP" "$AUTOPILOT_DIR"
install -d -m 0755 -o root -g root "$RELEASES_DIR"
install -d -m 0750 -o "$AUTOPILOT_USER" -g "$AUTOPILOT_GROUP" "$RUNTIME_DIR"

log "Install immutable Autopilot source release"
install -d -m 0755 -o root -g root "$RELEASE_DIR"
install -d -m 0755 -o root -g root "$RELEASE_DIR/oracle_autopilot"
install -m 0644 -o root -g root "$REPO_DIR"/oracle_autopilot/*.py "$RELEASE_DIR/oracle_autopilot/"
printf '%s\n' "$SOURCE_REVISION" > "$RELEASE_DIR/SOURCE_REVISION"
chmod 0444 "$RELEASE_DIR/SOURCE_REVISION"

log "Create bounded Python runtime"
python3 -m venv "$AUTOPILOT_DIR/.venv"
"$AUTOPILOT_DIR/.venv/bin/python" -m pip install --disable-pip-version-check --no-cache-dir \
  "psycopg[binary]==$PSYCOPG_VERSION" >/dev/null
chown -R root:"$AUTOPILOT_GROUP" "$AUTOPILOT_DIR/.venv"
chmod -R g+rX,o-rwx "$AUTOPILOT_DIR/.venv"

log "Validate direct Neon DSN and least-privilege RPC boundary"
AUTOPILOT_EXPECTED_DB_USER="$EXPECTED_DB_USER" \
AUTOPILOT_DATABASE_URL="$AUTOPILOT_DATABASE_URL" \
PYTHONPATH="$RELEASE_DIR" \
"$AUTOPILOT_DIR/.venv/bin/python" - <<'PY'
import os
import psycopg
from oracle_autopilot.worker import validate_neon_direct_dsn

dsn = validate_neon_direct_dsn(os.environ["AUTOPILOT_DATABASE_URL"])
with psycopg.connect(dsn, connect_timeout=10, application_name="autopilot-shadow-install-preflight") as conn:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT current_user,
                   has_schema_privilege(current_user, 'autopilot', 'USAGE'),
                   has_table_privilege(current_user, 'autopilot.task', 'SELECT'),
                   has_table_privilege(current_user, 'autopilot.task', 'INSERT'),
                   has_function_privilege(current_user, 'autopilot.claim_next_task(text,integer)', 'EXECUTE'),
                   has_function_privilege(current_user, 'autopilot.complete_task(uuid,text,bigint,text,text,jsonb)', 'EXECUTE')
        """)
        user, schema_usage, table_select, table_insert, can_claim, can_complete = cur.fetchone()
expected = os.environ["AUTOPILOT_EXPECTED_DB_USER"]
assert user == expected, (user, expected)
assert schema_usage and not table_select and not table_insert and can_claim and can_complete, (
    schema_usage, table_select, table_insert, can_claim, can_complete
)
print("AUTOPILOT_DB_PREFLIGHT_PASS")
PY

log "Select verified immutable source release"
ln -sfn "$RELEASE_DIR" "$AUTOPILOT_DIR/current.next"
mv -Tf "$AUTOPILOT_DIR/current.next" "$CURRENT_LINK"

log "Write root-owned shadow environment without printing credentials"
umask 077
{
  printf 'AUTOPILOT_DATABASE_URL=%s\n' "$AUTOPILOT_DATABASE_URL"
  printf 'AUTOPILOT_EXPECTED_DB_USER=%s\n' "$EXPECTED_DB_USER"
  printf 'AUTOPILOT_WORKER_ID=oracle-autopilot-shadow-1\n'
  printf 'AUTOPILOT_LEASE_SECONDS=60\n'
  printf 'AUTOPILOT_HEARTBEAT_SECONDS=15\n'
  printf 'AUTOPILOT_RECOVERY_POLL_SECONDS=30\n'
} >"$AUTOPILOT_DIR/autopilot-shadow.env"
chown root:root "$AUTOPILOT_DIR/autopilot-shadow.env"
chmod 0600 "$AUTOPILOT_DIR/autopilot-shadow.env"

log "Install hardened systemd unit"
install -m 0644 -o root -g root "$SERVICE_SRC" "$SERVICE_DST"
systemctl daemon-reload
systemd-analyze verify "$SERVICE_DST" >/dev/null

if [[ "$ACTIVATE" == "1" ]]; then
  log "Enable Oracle Autopilot shadow worker"
  systemctl enable --now "$SERVICE_NAME"
  sleep 2
  systemctl is-active --quiet "$SERVICE_NAME" || {
    journalctl -u "$SERVICE_NAME" -n 40 --no-pager >&2 || true
    die "Oracle Autopilot shadow service failed to become active"
  }
  printf 'AUTOPILOT_SHADOW_INSTALL_PASS activated=1 production_mutations=0\n'
else
  current_state="$(systemctl is-active "$SERVICE_NAME" 2>/dev/null || true)"
  enabled_state="$(systemctl is-enabled "$SERVICE_NAME" 2>/dev/null || true)"
  [[ "$current_state" != "active" && "$current_state" != "activating" ]] \
    || die "staged service unexpectedly active"
  [[ "$enabled_state" != "enabled" && "$enabled_state" != "enabled-runtime" ]] \
    || die "staged service unexpectedly enabled"
  log "Stage complete; service remains inactive and disabled"
  printf 'AUTOPILOT_SHADOW_INSTALL_PASS activated=0 inactive=1 disabled=1\n'
fi
