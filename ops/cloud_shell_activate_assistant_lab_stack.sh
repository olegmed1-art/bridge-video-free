#!/usr/bin/env bash
set -Eeuo pipefail

# Canonical bounded OCI Cloud Shell launcher for the Assistant Lab Observer stack.
# This is the only supported bootstrap/recovery entry point for Observer +
# localhost Control API + outbound Neon Control Bridge.

readonly REGION='eu-frankfurt-1'
readonly INSTANCE_NAME='bridge-school-dds3-frankfurt'
readonly RUNTIME_COMMIT='355724aa23cd1fb47b1bd0be1b4975170bf397c3'
readonly REPO_DIR='/opt/bridge-school/bridge-video-free'
readonly ARCHIVE_DIR='/srv/assistant-lab-observer-archive'

log(){ printf '\n[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[[ "$#" -eq 1 ]] || die 'usage: cloud_shell_activate_assistant_lab_stack.sh probe|status|activate'
readonly MODE="$1"
case "$MODE" in probe|status|activate) ;; *) die "unsupported mode: $MODE" ;; esac
for c in oci python3 curl; do command -v "$c" >/dev/null 2>&1 || die "$c is required"; done
export OCI_CLI_REGION="$REGION"

log 'Resolve exactly one live Frankfurt instance'
ROOT_JSON="$(oci iam compartment list --include-root --all --output json)"
TENANCY_ID="$(printf '%s' "$ROOT_JSON" | python3 -c 'import json,sys; xs=json.load(sys.stdin).get("data",[]); print(next((x.get("id","") for x in xs if str(x.get("id","")).startswith("ocid1.tenancy.")),""))')"
[[ -n "$TENANCY_ID" ]] || die 'could not determine tenancy'
COMPARTMENT_ID="$TENANCY_ID"
INSTANCES_JSON="$(oci compute instance list -c "$COMPARTMENT_ID" --display-name "$INSTANCE_NAME" --all --output json)"
INSTANCE_ID="$(printf '%s' "$INSTANCES_JSON" | python3 -c 'import json,sys; xs=[x for x in json.load(sys.stdin).get("data",[]) if x.get("lifecycle-state") not in {"TERMINATED","TERMINATING"}]; assert len(xs)==1, len(xs); print(xs[0]["id"])')" || die 'expected exactly one live instance'
export INSTANCE_ID COMPARTMENT_ID
STATE="$(oci compute instance get --instance-id "$INSTANCE_ID" --query 'data."lifecycle-state"' --raw-output)"
[[ "$STATE" == RUNNING ]] || die "instance is $STATE"
printf 'instance_id=%s\ninstance_state=%s\nruntime_commit=%s\n' "$INSTANCE_ID" "$STATE" "$RUNTIME_COMMIT"

run_command(){
  local display_name="$1" script_text="$2"
  local content target command_id execution state exit_code output
  content="$(SCRIPT_TEXT="$script_text" python3 -c 'import json,os; print(json.dumps({"source":{"sourceType":"TEXT","text":os.environ["SCRIPT_TEXT"]},"output":{"outputType":"TEXT"}},separators=(",",":")))')"
  target="$(python3 -c 'import json,os; print(json.dumps({"instanceId":os.environ["INSTANCE_ID"]},separators=(",",":")))')"
  command_id="$(oci instance-agent command create --compartment-id "$COMPARTMENT_ID" --content "$content" --target "$target" --timeout-in-seconds 600 --display-name "$display_name" --query data.id --raw-output)"
  [[ -n "$command_id" && "$command_id" != null ]] || die 'Run Command creation failed'
  printf 'command_id=%s\n' "$command_id"
  execution=''; state=''
  for _ in $(seq 1 120); do
    execution="$(oci instance-agent command-execution get --command-id "$command_id" --instance-id "$INSTANCE_ID" --output json 2>/dev/null || true)"
    if [[ -n "$execution" ]]; then
      state="$(printf '%s' "$execution" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("data",{}).get("lifecycle-state",""))')"
      case "$state" in SUCCEEDED|FAILED|CANCELED|TIMED_OUT) break ;; esac
    fi
    sleep 5
  done
  [[ -n "$execution" ]] || die 'Run Command produced no execution record'
  exit_code="$(printf '%s' "$execution" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("data",{}).get("content",{}).get("exit-code",""))')"
  output="$(printf '%s' "$execution" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("data",{}).get("content",{}).get("text","") or "")')"
  printf 'run_command_state=%s\nexit_code=%s\n%s\n' "$state" "${exit_code:-unknown}" "$output"
  [[ "$state" == SUCCEEDED && "$exit_code" == 0 ]] || die "Run Command $display_name failed"
}

COMMON_READ_ONLY=$(cat <<'REMOTE'
set -Eeuo pipefail
sudo -n true
systemctl is-active --quiet assistant-lab.service
ready="$(curl -fsS --max-time 10 http://127.0.0.1:8080/readyz)"
READY="$ready" python3 -c 'import json,os; x=json.loads(os.environ["READY"]); assert x.get("status")=="ready" and x.get("engine")=="DDS3" and x.get("fallback_used") is False'
printf 'repo_head=%s\n' "$(git -C /opt/bridge-school/bridge-video-free rev-parse HEAD 2>/dev/null || true)"
printf 'assistant_lab=%s\n' "$(systemctl is-active assistant-lab.service 2>/dev/null || true)"
printf 'observer=%s\n' "$(systemctl is-active assistant-lab-observer.service 2>/dev/null || true)"
printf 'control=%s\n' "$(systemctl is-active assistant-lab-control.service 2>/dev/null || true)"
printf 'bridge=%s\n' "$(systemctl is-active assistant-lab-control-bridge.service 2>/dev/null || true)"
echo dds3=ready_real_no_fallback
if systemctl is-active --quiet assistant-lab-control.service; then
  token="$(sudo -n sed -n 's/^ASSISTANT_LAB_CONTROL_TOKEN=//p' /opt/bridge-school/assistant-lab-observer/control.env)"
  health="$(curl -fsS --max-time 5 -H "Authorization: Bearer $token" http://127.0.0.1:8765/healthz)"
  HEALTH="$health" python3 -c 'import json,os; x=json.loads(os.environ["HEALTH"]); assert x.get("status")=="ready"; assert x.get("arbitrary_shell") is False; assert x.get("video_analyzer_result_access") is False; assert x.get("other_oracle_result_access") is False'
  echo control_health=ready_bounded
fi
REMOTE
)

case "$MODE" in
  probe)
    run_command assistant-lab-stack-probe "$COMMON_READ_ONLY"
    echo ASSISTANT_LAB_STACK_PROBE_PASS
    ;;
  status)
    run_command assistant-lab-stack-status "$COMMON_READ_ONLY"
    echo ASSISTANT_LAB_STACK_STATUS_PASS
    ;;
  activate)
    ACTIVATE_SCRIPT=$(cat <<REMOTE
set -Eeuo pipefail
sudo -n true
systemctl is-active --quiet assistant-lab.service
repo='$REPO_DIR'
cd "\$repo"
[[ -d .git ]]
[[ -z "\$(git status --porcelain)" ]] || { echo repo_dirty; exit 40; }
git fetch --quiet origin '$RUNTIME_COMMIT'
git cat-file -e '$RUNTIME_COMMIT^{commit}'
current="\$(git rev-parse HEAD)"
git merge-base --is-ancestor "\$current" '$RUNTIME_COMMIT' || { echo runtime_not_fast_forward; exit 41; }
git checkout --quiet --detach '$RUNTIME_COMMIT'
[[ "\$(git rev-parse HEAD)" == '$RUNTIME_COMMIT' ]]
sudo -n env ASSISTANT_LAB_OBSERVER_ACTIVATE=1 ASSISTANT_LAB_OBSERVER_ARCHIVE_ROOT='$ARCHIVE_DIR' ASSISTANT_LAB_REPO_DIR='$REPO_DIR' bash '$REPO_DIR/ops/oracle_assistant_lab_observer_install.sh'
sudo -n env ASSISTANT_LAB_CONTROL_BRIDGE_ACTIVATE=1 ASSISTANT_LAB_REPO_DIR='$REPO_DIR' bash '$REPO_DIR/ops/oracle_assistant_lab_control_bridge_install.sh'
for svc in assistant-lab.service assistant-lab-observer.service assistant-lab-control.service assistant-lab-control-bridge.service; do systemctl is-active --quiet "\$svc"; done
systemctl is-enabled --quiet assistant-lab-observer.service
systemctl is-enabled --quiet assistant-lab-control.service
systemctl is-enabled --quiet assistant-lab-control-bridge.service
ss -ltn | grep -Eq '127\\.0\\.0\\.1:8765[[:space:]]'
! ss -ltn | grep -Eq '(^|[[:space:]])0\\.0\\.0\\.0:8765|\\[::\\]:8765'
token="\$(sudo -n sed -n 's/^ASSISTANT_LAB_CONTROL_TOKEN=//p' /opt/bridge-school/assistant-lab-observer/control.env)"
health="\$(curl -fsS --max-time 5 -H "Authorization: Bearer \$token" http://127.0.0.1:8765/healthz)"
HEALTH="\$health" python3 -c 'import json,os; x=json.loads(os.environ["HEALTH"]); assert x.get("status")=="ready"; assert x.get("arbitrary_shell") is False; assert x.get("video_analyzer_result_access") is False; assert x.get("other_oracle_result_access") is False'
ready="\$(curl -fsS --max-time 10 http://127.0.0.1:8080/readyz)"
READY="\$ready" python3 -c 'import json,os; x=json.loads(os.environ["READY"]); assert x.get("status")=="ready" and x.get("engine")=="DDS3" and x.get("fallback_used") is False'
echo observer=active
echo control=active_localhost_only
echo bridge=active_outbound_only
echo assistant_lab=active
echo dds3=ready_real_no_fallback
echo ASSISTANT_LAB_STACK_ACTIVATION_PASS
REMOTE
)
    run_command assistant-lab-stack-activate "$ACTIVATE_SCRIPT"
    echo ASSISTANT_LAB_STACK_CLOUD_SHELL_ACTIVATION_PASS
    ;;
esac
