#!/usr/bin/env bash
set -Eeuo pipefail

# Root-owned bounded administrative entrypoint for OCI Run Command (ocarun).
# This file is installed under /usr/local/sbin by the one-time bootstrap installer.
# It intentionally exposes only fixed Assistant Lab operations; no arbitrary shell.

readonly REPO='/opt/bridge-school/bridge-video-free'
readonly OBSERVER_ENV='/opt/bridge-school/assistant-lab-observer/control.env'
readonly LAB_ENV='/opt/bridge-school/assistant-lab/assistant-lab.env'
readonly ARCHIVE='/srv/assistant-lab-observer-archive'
readonly EXPECTED_ORIGIN='https://github.com/olegmed1-art/bridge-video-free.git'

fail(){ echo "ERROR: $*" >&2; exit 1; }
need_root(){ [[ $(id -u) -eq 0 ]] || fail 'must run as root'; }
verify_repo(){
  [[ -d "$REPO/.git" ]] || fail 'repo missing'
  case "$(git -C "$REPO" remote get-url origin)" in
    https://github.com/olegmed1-art/bridge-video-free|https://github.com/olegmed1-art/bridge-video-free.git|git@github.com:olegmed1-art/bridge-video-free.git) ;;
    *) fail 'unexpected repository origin' ;;
  esac
}
verify_dds3(){
  local ready
  ready="$(curl -fsS --max-time 10 http://127.0.0.1:8080/readyz)"
  READY="$ready" python3 - <<'PY'
import json, os
x=json.loads(os.environ['READY'])
assert x.get('status') == 'ready', x
assert x.get('engine') == 'DDS3', x
assert x.get('fallback_used') is False, x
PY
}
verify_services(){
  local s
  for s in assistant-lab.service assistant-lab-observer.service assistant-lab-control.service assistant-lab-control-bridge.service; do
    systemctl is-active --quiet "$s" || fail "$s is not active"
  done
}
verify_control(){
  [[ -f "$OBSERVER_ENV" ]] || fail 'observer env missing'
  local token health
  token="$(sed -n 's/^ASSISTANT_LAB_CONTROL_TOKEN=//p' "$OBSERVER_ENV" | head -n1)"
  [[ ${#token} -ge 32 ]] || fail 'control token missing/short'
  health="$(curl -fsS --max-time 5 -H "Authorization: Bearer $token" http://127.0.0.1:8765/healthz)"
  HEALTH="$health" python3 - <<'PY'
import json, os
x=json.loads(os.environ['HEALTH'])
assert x.get('status') == 'ready', x
assert x.get('arbitrary_shell') is False, x
assert x.get('video_analyzer_result_access') is False, x
assert x.get('other_oracle_result_access') is False, x
PY
  ss -ltn | grep -Eq '127\.0\.0\.1:8765[[:space:]]'
  ! ss -ltn | grep -Eq '(^|[[:space:]])0\.0\.0\.0:8765|\[::\]:8765'
}
verify_db(){
  [[ -f "$LAB_ENV" ]] || fail 'Assistant Lab env missing'
  local db_url py
  db_url="$(sed -n 's/^ASSISTANT_LAB_DATABASE_URL=//p' "$LAB_ENV" | head -n1)"
  [[ -n "$db_url" ]] || fail 'database URL missing'
  py='/opt/bridge-school/assistant-lab-observer/.venv/bin/python'
  [[ -x "$py" ]] || fail 'observer python missing'
  ASSISTANT_LAB_DATABASE_URL="$db_url" "$py" - <<'PY'
import os, psycopg
with psycopg.connect(os.environ['ASSISTANT_LAB_DATABASE_URL'], connect_timeout=10, application_name='assistant-lab-oci-admin-audit') as conn:
    with conn.cursor() as cur:
        cur.execute("""
        SELECT current_user,
               has_schema_privilege(current_user,'assistant_lab','USAGE'),
               has_table_privilege(current_user,'assistant_lab.control_command','SELECT'),
               has_table_privilege(current_user,'assistant_lab.control_command','UPDATE'),
               has_table_privilege(current_user,'assistant_lab.control_command','INSERT'),
               has_table_privilege(current_user,'assistant_lab.control_command','DELETE'),
               has_function_privilege(current_user,'assistant_lab.claim_control_command(text)','EXECUTE'),
               has_function_privilege(current_user,'assistant_lab.finish_control_command(uuid,text,text,jsonb,text)','EXECUTE'),
               has_function_privilege(current_user,'assistant_lab.recover_stale_control_commands(integer)','EXECUTE')
        """)
        row=cur.fetchone()
        user, schema_usage, sel, upd, ins, dele, claim, finish, recover = row
        assert user == 'assistant_lab_worker_principal', row
        assert schema_usage is True, row
        assert not any((sel, upd, ins, dele)), row
        assert all((claim, finish, recover)), row
print('db_contract=least_privilege_rpc_only')
PY
}

audit(){
  verify_repo
  verify_services
  verify_dds3
  verify_control
  [[ "$(systemctl show assistant-lab-observer.service -p NoNewPrivileges --value)" == yes ]] || fail 'NoNewPrivileges is not enabled'
  [[ -d "$ARCHIVE" ]] || fail 'archive missing'
  verify_db
  echo "repo_head=$(git -C "$REPO" rev-parse HEAD)"
  echo 'services=active'
  echo 'dds3=ready_real_no_fallback'
  echo 'control=ready_localhost_bounded'
  echo 'archive=present'
  echo 'ASSISTANT_LAB_OCI_ADMIN_AUDIT_PASS'
}

restart_bridge(){
  verify_repo
  verify_dds3
  systemctl restart assistant-lab-control-bridge.service
  sleep 2
  verify_services
  verify_dds3
  verify_control
  echo 'ASSISTANT_LAB_OCI_ADMIN_RESTART_BRIDGE_PASS'
}

activate_stack(){
  verify_repo
  verify_dds3
  [[ -z "$(git -C "$REPO" status --porcelain --untracked-files=no)" ]] || fail 'tracked repository files dirty'
  ASSISTANT_LAB_OBSERVER_ACTIVATE=1 ASSISTANT_LAB_OBSERVER_ARCHIVE_ROOT="$ARCHIVE" ASSISTANT_LAB_REPO_DIR="$REPO" bash "$REPO/ops/oracle_assistant_lab_observer_install.sh"
  ASSISTANT_LAB_CONTROL_BRIDGE_ACTIVATE=1 ASSISTANT_LAB_REPO_DIR="$REPO" bash "$REPO/ops/oracle_assistant_lab_control_bridge_install.sh"
  verify_services
  verify_dds3
  verify_control
  echo 'ASSISTANT_LAB_OCI_ADMIN_ACTIVATE_STACK_PASS'
}

need_root
[[ $# -eq 1 ]] || fail 'usage: assistant-lab-oci-admin audit|restart-bridge|activate-stack'
case "$1" in
  audit) audit ;;
  restart-bridge) restart_bridge ;;
  activate-stack) activate_stack ;;
  *) fail 'unsupported operation' ;;
esac
