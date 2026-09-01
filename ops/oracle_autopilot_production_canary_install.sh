#!/usr/bin/env bash
set -Eeuo pipefail

# Install an isolated Oracle-resident worker for one zero-cost production
# control-plane canary. The worker intentionally stays in its SHADOW execution
# mode and receives only the migration-0300 bounded RPC role.

CANARY_USER=school-autopilot-canary
CANARY_GROUP=school-autopilot-canary
CANARY_DIR=/opt/bridge-school/school-autopilot-production-canary
RELEASES_DIR="$CANARY_DIR/releases"
RUNTIME_DIR="$CANARY_DIR/runtime"
CURRENT_LINK="$CANARY_DIR/current"
SERVICE_NAME=school-autopilot-production-canary.service
SERVICE_DST="/etc/systemd/system/$SERVICE_NAME"
ENV_FILE="$CANARY_DIR/autopilot-production-canary.env"
SOURCE_REVISION="${AUTOPILOT_SOURCE_REVISION:-}"
REPO_DIR="${AUTOPILOT_REPO_DIR:-}"
RELEASE_DIR="$RELEASES_DIR/$SOURCE_REVISION"
SERVICE_SRC="$REPO_DIR/deploy/oracle-autopilot/$SERVICE_NAME"
EXPECTED_DB_USER=autopilot_prod_canary_login
EXPECTED_PROJECT_ID=misty-poetry-18012774
EXPECTED_BRANCH_ID=br-wispy-lab-b1rq54of
PSYCOPG_VERSION=3.3.4

log(){ printf '\n[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die 'run as root on the pinned Oracle host'
[[ "${AUTOPILOT_ACTIVATION_SCOPE:-}" == PRODUCTION_CANARY_ZERO_COST ]] \
  || die 'AUTOPILOT_ACTIVATION_SCOPE=PRODUCTION_CANARY_ZERO_COST is required'
[[ -n "${AUTOPILOT_DATABASE_URL:-}" ]] || die 'AUTOPILOT_DATABASE_URL is required'
[[ "$SOURCE_REVISION" =~ ^[0-9a-f]{40}$ ]] || die 'source revision must be a full commit SHA'
[[ -d "$REPO_DIR" ]] || die 'packaged source directory is missing'
[[ -f "$REPO_DIR/AUTOPILOT_SOURCE_REVISION" ]] || die 'source marker is missing'
[[ "$(<"$REPO_DIR/AUTOPILOT_SOURCE_REVISION")" == "$SOURCE_REVISION" ]] \
  || die 'source marker does not match the pinned revision'
[[ -f "$REPO_DIR/oracle_autopilot/worker.py" ]] || die 'bounded worker is missing'
[[ -f "$REPO_DIR/autopilot_phase3b/policy.py" ]] || die 'bounded policy module is missing'
[[ -f "$SERVICE_SRC" ]] || die 'production-canary unit is missing'
command -v python3 >/dev/null 2>&1 || die 'python3 is required'
command -v systemctl >/dev/null 2>&1 || die 'systemctl is required'
command -v systemd-analyze >/dev/null 2>&1 || die 'systemd-analyze is required'

log 'Create a separate Unix identity and directories'
if ! getent group "$CANARY_GROUP" >/dev/null 2>&1; then
  groupadd --system "$CANARY_GROUP"
fi
if ! id "$CANARY_USER" >/dev/null 2>&1; then
  useradd --system --gid "$CANARY_GROUP" --home-dir "$CANARY_DIR" \
    --shell /usr/sbin/nologin "$CANARY_USER"
fi
install -d -m 0750 -o root -g "$CANARY_GROUP" "$CANARY_DIR"
install -d -m 0755 -o root -g root "$RELEASES_DIR"
install -d -m 0750 -o "$CANARY_USER" -g "$CANARY_GROUP" "$RUNTIME_DIR"

log 'Install the immutable canary source release'
install -d -m 0755 -o root -g root "$RELEASE_DIR/oracle_autopilot"
install -m 0644 -o root -g root "$REPO_DIR"/oracle_autopilot/*.py \
  "$RELEASE_DIR/oracle_autopilot/"
install -d -m 0755 -o root -g root "$RELEASE_DIR/autopilot_phase3b"
install -m 0644 -o root -g root "$REPO_DIR"/autopilot_phase3b/*.py \
  "$RELEASE_DIR/autopilot_phase3b/"
printf '%s\n' "$SOURCE_REVISION" > "$RELEASE_DIR/SOURCE_REVISION"
chmod 0444 "$RELEASE_DIR/SOURCE_REVISION"

log 'Create the isolated Python runtime'
if [[ ! -x "$CANARY_DIR/.venv/bin/python" ]]; then
  python3 -m venv "$CANARY_DIR/.venv"
fi
"$CANARY_DIR/.venv/bin/python" -m pip install \
  --disable-pip-version-check --no-cache-dir "psycopg[binary]==$PSYCOPG_VERSION" >/dev/null
chown -R root:"$CANARY_GROUP" "$CANARY_DIR/.venv"
chmod -R g+rX,o-rwx "$CANARY_DIR/.venv"

log 'Validate the production branch and least-privilege runtime boundary'
AUTOPILOT_EXPECTED_DB_USER="$EXPECTED_DB_USER" \
AUTOPILOT_DATABASE_URL="$AUTOPILOT_DATABASE_URL" \
EXPECTED_PROJECT_ID="$EXPECTED_PROJECT_ID" \
EXPECTED_BRANCH_ID="$EXPECTED_BRANCH_ID" \
PYTHONPATH="$RELEASE_DIR" \
"$CANARY_DIR/.venv/bin/python" - <<'PY'
import os

import psycopg

from oracle_autopilot.worker import validate_neon_direct_dsn

dsn = validate_neon_direct_dsn(os.environ["AUTOPILOT_DATABASE_URL"])
with psycopg.connect(
    dsn,
    connect_timeout=10,
    application_name="autopilot-production-canary-install",
) as conn:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT current_user,
                   current_setting('neon.project_id', true),
                   current_setting('neon.branch_id', true),
                   NOT pg_is_in_recovery(),
                   has_schema_privilege(current_user, 'autopilot', 'USAGE'),
                   has_table_privilege(current_user, 'autopilot.task', 'SELECT'),
                   has_table_privilege(current_user, 'autopilot.task', 'INSERT'),
                   has_table_privilege(current_user, 'autopilot.task_status', 'SELECT'),
                   has_function_privilege(
                       current_user,
                       'autopilot.claim_next_task(text,integer)',
                       'EXECUTE'
                   ),
                   has_function_privilege(
                       current_user,
                       'autopilot.complete_task(uuid,text,bigint,text,text,jsonb)',
                       'EXECUTE'
                   ),
                   has_function_privilege(
                       current_user,
                       'autopilot.create_shadow_task(text,text,jsonb,integer,bigint,text,text)',
                       'EXECUTE'
                   )
            """
        )
        (
            user,
            project_id,
            branch_id,
            primary,
            schema_usage,
            task_select,
            task_insert,
            status_select,
            can_claim,
            can_complete,
            can_create,
        ) = cur.fetchone()
        cur.execute(
            """
            SELECT count(*)
              FROM information_schema.tables
             WHERE table_schema NOT IN ('pg_catalog', 'information_schema', 'autopilot')
               AND (
                   has_table_privilege(current_user, quote_ident(table_schema) || '.' || quote_ident(table_name), 'INSERT')
                   OR has_table_privilege(current_user, quote_ident(table_schema) || '.' || quote_ident(table_name), 'UPDATE')
                   OR has_table_privilege(current_user, quote_ident(table_schema) || '.' || quote_ident(table_name), 'DELETE')
               )
            """
        )
        non_autopilot_write_access = cur.fetchone()[0]
        cur.execute(
            """
            SELECT count(*)
              FROM information_schema.tables
             WHERE table_schema NOT IN ('pg_catalog', 'information_schema', 'autopilot')
               AND has_table_privilege(
                   current_user,
                   quote_ident(table_schema) || '.' || quote_ident(table_name),
                   'SELECT'
               )
               AND NOT (
                   table_schema = 'public'
                   AND table_type = 'VIEW'
                   AND table_name IN ('pg_stat_statements', 'pg_stat_statements_info')
               )
            """
        )
        unexpected_non_autopilot_select_access = cur.fetchone()[0]
        cur.execute(
            """
            SELECT count(*)
              FROM information_schema.tables
             WHERE table_schema = 'public'
               AND table_type = 'VIEW'
               AND table_name IN ('pg_stat_statements', 'pg_stat_statements_info')
               AND has_table_privilege(
                   current_user,
                   quote_ident(table_schema) || '.' || quote_ident(table_name),
                   'SELECT'
               )
            """
        )
        allowed_postgres_telemetry_views = cur.fetchone()[0]
        cur.execute(
            """
            SELECT count(*)
              FROM information_schema.tables
             WHERE table_schema = 'autopilot'
               AND table_name <> 'task_status'
               AND (
                   has_table_privilege(current_user, quote_ident(table_schema) || '.' || quote_ident(table_name), 'SELECT')
                   OR has_table_privilege(current_user, quote_ident(table_schema) || '.' || quote_ident(table_name), 'INSERT')
                   OR has_table_privilege(current_user, quote_ident(table_schema) || '.' || quote_ident(table_name), 'UPDATE')
                   OR has_table_privilege(current_user, quote_ident(table_schema) || '.' || quote_ident(table_name), 'DELETE')
               )
            """
        )
        direct_autopilot_table_access = cur.fetchone()[0]
        cur.execute(
            """
            SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                   rolreplication, rolbypassrls, rolconnlimit
              FROM pg_roles
             WHERE rolname = current_user
            """
        )
        role_attributes = cur.fetchone()

assert user == "autopilot_prod_canary_login", user
assert project_id == os.environ["EXPECTED_PROJECT_ID"], project_id
assert branch_id == os.environ["EXPECTED_BRANCH_ID"], branch_id
assert primary is True
assert schema_usage and status_select and can_claim and can_complete
assert not task_select and not task_insert and not can_create
assert non_autopilot_write_access == 0, non_autopilot_write_access
assert unexpected_non_autopilot_select_access == 0, unexpected_non_autopilot_select_access
assert allowed_postgres_telemetry_views == 2, allowed_postgres_telemetry_views
assert direct_autopilot_table_access == 0, direct_autopilot_table_access
assert role_attributes == (True, False, False, False, False, False, 1), role_attributes
print("AUTOPILOT_PRODUCTION_CANARY_DB_PREFLIGHT_PASS")
PY

log 'Select the pinned release and write the root-owned environment'
ln -sfn "$RELEASE_DIR" "$CANARY_DIR/current.next"
mv -Tf "$CANARY_DIR/current.next" "$CURRENT_LINK"
umask 077
{
  printf 'AUTOPILOT_DATABASE_URL=%s\n' "$AUTOPILOT_DATABASE_URL"
  printf 'AUTOPILOT_EXPECTED_DB_USER=%s\n' "$EXPECTED_DB_USER"
  printf 'AUTOPILOT_WORKER_ID=oracle-autopilot-production-canary-1\n'
  printf 'AUTOPILOT_LEASE_SECONDS=60\n'
  printf 'AUTOPILOT_HEARTBEAT_SECONDS=15\n'
  printf 'AUTOPILOT_RECOVERY_POLL_SECONDS=30\n'
} > "$ENV_FILE"
chown root:root "$ENV_FILE"
chmod 0600 "$ENV_FILE"

log 'Install and activate only the isolated canary service'
install -m 0644 -o root -g root "$SERVICE_SRC" "$SERVICE_DST"
systemctl daemon-reload
systemd-analyze verify "$SERVICE_DST" >/dev/null
systemctl disable --now "$SERVICE_NAME" >/dev/null 2>&1 || true
systemctl enable --now "$SERVICE_NAME"
sleep 2
if ! systemctl is-active --quiet "$SERVICE_NAME"; then
  journalctl -u "$SERVICE_NAME" -n 40 --no-pager >&2 || true
  die 'production-canary service failed to become active'
fi
[[ "$(systemctl is-enabled "$SERVICE_NAME")" == enabled ]] \
  || die 'production-canary service is not enabled'
[[ "$(readlink -f "$CURRENT_LINK")" == "$RELEASE_DIR" ]] \
  || die 'production-canary source symlink is not pinned'
[[ "$(stat -c '%U:%G:%a' "$ENV_FILE")" == root:root:600 ]] \
  || die 'production-canary environment permissions are unsafe'

printf 'AUTOPILOT_PRODUCTION_CANARY_INSTALL_PASS\n'
printf 'AUTOPILOT_EXECUTION_CONTRACT=SHADOW\n'
printf 'AUTOPILOT_TASK_COST_CAP_MICROUSD=0\n'
printf 'ORACLE_INSTANCE_STOP_REQUESTED=NO\n'
