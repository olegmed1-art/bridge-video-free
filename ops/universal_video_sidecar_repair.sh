#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Fixed, no-argument repair for the Universal Video sidecar on the existing
# Frankfurt Oracle host. It does not submit media, run ASR, or change routing.
readonly BASE_DIR='/opt/bridge-school/universal-video'
readonly SOURCE_DIR='/opt/bridge-school/universal-video-src'
readonly SERVICE='universal-video.service'
readonly EXPECTED_RUNTIME_COMMIT='7e46f0327d6094400e0d35ec6af20408cc97683e'
readonly UNIT_SOURCE="$SOURCE_DIR/deploy/oracle-universal-video/universal-video.service"
readonly UNIT_TARGET="/etc/systemd/system/$SERVICE"
readonly PYTHON="$BASE_DIR/.venv/bin/python"
readonly SAFE_PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'

fail() {
  local code="$1"
  case "$code" in
    MUST_RUN_AS_ROOT|PROTECTED_SERVICE|DDS3|RUNTIME_LAYOUT|RUNTIME_PIN|RUNTIME_DIRTY|QUEUED_OR_RUNNING_JOB|SPOOL_REPAIR|SERVICE_START|POST_REGRESSION) ;;
    *) code='RUNTIME_LAYOUT' ;;
  esac
  printf 'UNIVERSAL_VIDEO_SIDECAR_REPAIR_FAIL=%s\n' "$code" >&2
  exit 1
}

[[ $# -eq 0 ]] || fail RUNTIME_LAYOUT
[[ $(id -u) -eq 0 ]] || fail MUST_RUN_AS_ROOT

state(){ systemctl is-active "$1" 2>/dev/null || true; }

verify_dds3() {
  local ready
  ready="$(curl -fsS --max-time 10 http://127.0.0.1:8080/readyz)" || fail DDS3
  if ! READY_JSON="$ready" python3 - <<'PY' >/dev/null
import json, os
x=json.loads(os.environ["READY_JSON"])
assert x.get("status") == "ready"
assert x.get("engine") == "DDS3"
assert x.get("fallback_used") is False
assert x.get("position_solver") == "ready"
PY
  then
    fail DDS3
  fi
}

protected=(assistant-lab.service assistant-lab-observer.service assistant-lab-control.service assistant-lab-control-bridge.service)
declare -A before=()
for s in "${protected[@]}"; do
  before["$s"]="$(state "$s")"
  [[ "${before[$s]}" == active ]] || fail PROTECTED_SERVICE
done
verify_dds3
echo 'dds3_before=ready_real_no_fallback'
echo 'protected_services_before=active'

[[ -d "$SOURCE_DIR/.git" && ! -L "$SOURCE_DIR" ]] || fail RUNTIME_LAYOUT
[[ -f "$UNIT_SOURCE" && ! -L "$UNIT_SOURCE" ]] || fail RUNTIME_LAYOUT
[[ -x "$PYTHON" ]] || fail RUNTIME_LAYOUT
[[ -f "$BASE_DIR/universal-video.env" && ! -L "$BASE_DIR/universal-video.env" ]] || fail RUNTIME_LAYOUT
id universal-video >/dev/null 2>&1 || fail RUNTIME_LAYOUT

source_head="$(git -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null)" || fail RUNTIME_PIN
[[ "$source_head" == "$EXPECTED_RUNTIME_COMMIT" ]] || fail RUNTIME_PIN
[[ -z "$(git -C "$SOURCE_DIR" status --porcelain=v1 --untracked-files=all)" ]] || fail RUNTIME_DIRTY
printf 'source_head=%s\n' "$source_head"

for leaf in inbox running; do
  if find "$BASE_DIR/spool/$leaf" -maxdepth 1 -type f -name '*.json' -print -quit 2>/dev/null | grep -q .; then
    fail QUEUED_OR_RUNNING_JOB
  fi
done
echo 'job_guard=empty_inbox_and_running'

if [[ -x /usr/local/sbin/universal-video-spool-repair ]]; then
  /usr/local/sbin/universal-video-spool-repair >/tmp/universal-video-spool-repair.out 2>&1 || fail SPOOL_REPAIR
  grep -Fx 'UNIVERSAL_VIDEO_SPOOL_RUNTIME_REPAIR_PASS' /tmp/universal-video-spool-repair.out >/dev/null || fail SPOOL_REPAIR
  rm -f /tmp/universal-video-spool-repair.out
else
  for leaf in inbox running done failed results; do
    install -d -o universal-video -g universal-video -m 0750 "$BASE_DIR/spool/$leaf"
    chown universal-video:universal-video "$BASE_DIR/spool/$leaf"
    chmod 0750 "$BASE_DIR/spool/$leaf"
  done
fi
echo 'spool_write_boundary=repaired'

echo 'runtime_import=deferred_to_authoritative_systemd_unit'


install -o root -g root -m 0644 "$UNIT_SOURCE" "$UNIT_TARGET"
systemctl daemon-reload
systemd-analyze verify "$UNIT_TARGET" >/dev/null || fail SERVICE_START
systemctl enable "$SERVICE" >/dev/null
systemctl start "$SERVICE" || true

for _ in $(seq 1 15); do
  [[ "$(state "$SERVICE")" == active ]] && break
  sleep 2
done
if [[ "$(state "$SERVICE")" != active ]]; then
  printf 'sidecar_active=%s\n' "$(state "$SERVICE")"
  systemctl show "$SERVICE" \
    -p SubState -p Result -p ExecMainCode -p ExecMainStatus -p NRestarts --no-pager \
    | sed -nE '/^(SubState|Result|ExecMainCode|ExecMainStatus|NRestarts)=/p'
  fail SERVICE_START
fi

echo 'sidecar_active=active'
printf 'sidecar_enabled=%s\n' "$(systemctl is-enabled "$SERVICE" 2>/dev/null || true)"

for s in "${protected[@]}"; do
  [[ "$(state "$s")" == "${before[$s]}" ]] || fail POST_REGRESSION
done
verify_dds3
echo 'protected_services_after=active'
echo 'dds3_after=ready_real_no_fallback'
echo UNIVERSAL_VIDEO_SIDECAR_REPAIR_PASS
