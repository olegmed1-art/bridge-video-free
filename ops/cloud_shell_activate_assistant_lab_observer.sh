#!/usr/bin/env bash
set -Eeuo pipefail

# Bounded OCI Cloud Shell activation for Assistant Lab Observer + localhost Control API.
# Uses OCI Instance Agent Run Command only; no SSH key and no arbitrary remote command input.

readonly REGION='eu-frankfurt-1'
readonly INSTANCE_NAME='bridge-school-dds3-frankfurt'
readonly REPOSITORY='olegmed1-art/bridge-video-free'
readonly RUNTIME_COMMIT='9004a2db02fcb70d5f1747b67858c9a9dc6b28ff'
readonly REPO_DIR='/opt/bridge-school/bridge-video-free'
readonly ISSUE_NUMBER='336'

log(){ printf '\n[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }

usage(){
  cat <<'USAGE'
Usage: bash ops/cloud_shell_activate_assistant_lab_observer.sh MODE

MODE:
  probe     verify the single Frankfurt VM and RUNNING Run Command plugin
  status    read Assistant Lab, Observer, Control API, and DDS3 state
  activate  fast-forward the pinned checkout and activate Observer + Control API

No arbitrary host, instance, repository, ref, or remote command is accepted.
USAGE
}

[[ "$#" -eq 1 ]] || { usage >&2; exit 64; }
readonly MODE="$1"
case "$MODE" in probe|status|activate) ;; *) usage >&2; die "unsupported mode: $MODE" ;; esac

for c in oci python3 curl; do command -v "$c" >/dev/null 2>&1 || die "$c is required"; done
export OCI_CLI_REGION="$REGION"

log 'Resolve exactly one live Frankfurt instance'
ROOT_JSON="$(oci iam compartment list --include-root --all --output json)"
TENANCY_ID="$(printf '%s' "$ROOT_JSON" | python3 -c 'import json,sys; xs=json.load(sys.stdin).get("data",[]); print(next((x.get("id","") for x in xs if str(x.get("id","")).startswith("ocid1.tenancy.")),""))')"
[[ -n "$TENANCY_ID" ]] || die 'could not determine tenancy OCID'
COMPARTMENT_ID="$TENANCY_ID"
INSTANCES_JSON="$(oci compute instance list -c "$COMPARTMENT_ID" --display-name "$INSTANCE_NAME" --all --output json)"
INSTANCE_ID="$(printf '%s' "$INSTANCES_JSON" | python3 -c 'import json,sys; xs=[x for x in json.load(sys.stdin).get("data",[]) if x.get("lifecycle-state") not in {"TERMINATED","TERMINATING"}]; assert len(xs)==1, len(xs); print(xs[0]["id"])')" || die 'expected exactly one live instance'
export INSTANCE_ID
STATE="$(oci compute instance get --instance-id "$INSTANCE_ID" --query 'data."lifecycle-state"' --raw-output)"
[[ "$STATE" == 'RUNNING' ]] || die "instance is $STATE"

PLUGIN_JSON="$(oci compute instance-agent-plugin list --instanceagent-id "$INSTANCE_ID" --compartment-id "$COMPARTMENT_ID" --all --output json 2>/dev/null || true)"
if [[ -n "$PLUGIN_JSON" ]]; then
  PLUGIN_STATE="$(printf '%s' "$PLUGIN_JSON" | python3 -c 'import json,sys; xs=json.load(sys.stdin).get("data",[]); m=[x for x in xs if str(x.get("name","")).lower() in {"run command","runcommand","compute instance run command"}]; print((m[0].get("status") or m[0].get("state") or "") if m else "")')"
else
  PLUGIN_STATE='unknown'
fi
printf 'instance_id=%s\ninstance_state=%s\nrun_command_plugin=%s\n' "$INSTANCE_ID" "$STATE" "$PLUGIN_STATE"

run_command(){
  local display_name="$1" script_text="$2"
  local content_json target_json command_id execution_json state exit_code output
  content_json="$(SCRIPT_TEXT="$script_text" python3 -c 'import json,os; print(json.dumps({"source":{"sourceType":"TEXT","text":os.environ["SCRIPT_TEXT"]},"output":{"outputType":"TEXT"}},separators=(",",":")))')"
  target_json="$(python3 -c 'import json,os; print(json.dumps({"instanceId":os.environ["INSTANCE_ID"]},separators=(",",":")))')"
  command_id="$(oci instance-agent command create --compartment-id "$COMPARTMENT_ID" --content "$content_json" --target "$target_json" --timeout-in-seconds 300 --display-name "$display_name" --query data.id --raw-output)"
  [[ -n "$command_id" && "$command_id" != 'null' ]] || die "failed to create Run Command $display_name"
  state=''
  execution_json=''
  for _ in $(seq 1 90); do
    execution_json="$(oci instance-agent command-execution get --command-id "$command_id" --instance-id "$INSTANCE_ID" --output json 2>/dev/null || true)"
    if [[ -n "$execution_json" ]]; then
      state="$(printf '%s' "$execution_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("data",{}).get("lifecycle-state",""))')"
      case "$state" in SUCCEEDED) break ;; FAILED|CANCELED|TIMED_OUT) die "Run Command $display_name ended in $state" ;; esac
    fi
    sleep 4
  done
  [[ "$state" == 'SUCCEEDED' ]] || die "Run Command $display_name did not complete"
  exit_code="$(printf '%s' "$execution_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("data",{}).get("content",{}).get("exit-code",""))')"
  output="$(printf '%s' "$execution_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("data",{}).get("content",{}).get("text","") or "")')"
  printf '%s\n' "$output"
  [[ "$exit_code" == '0' ]] || die "Run Command $display_name returned exit code ${exit_code:-unknown}"
}

read_only_script='set -Eeuo pipefail
sudo -n true
[[ "$(sudo -n systemctl is-active assistant-lab.service)" == active ]]
ready="$(curl -fsS --max-time 10 http://127.0.0.1:8080/readyz)"
READY="$ready" python3 -c '\''import json,os; x=json.loads(os.environ["READY"]); assert x.get("status")=="ready" and x.get("engine")=="DDS3" and x.get("fallback_used") is False'\''
printf "assistant_lab=active\n"
printf "observer=%s\n" "$(sudo -n systemctl is-active assistant-lab-observer.service 2>/dev/null || true)"
printf "control=%s\n" "$(sudo -n systemctl is-active assistant-lab-control.service 2>/dev/null || true)"
printf "dds3=ready_real_no_fallback\n"
if sudo -n systemctl is-active --quiet assistant-lab-control.service; then
  token="$(sudo -n sed -n '\''s/^ASSISTANT_LAB_CONTROL_TOKEN=//p'\'' /opt/bridge-school/assistant-lab-observer/control.env)"
  health="$(curl -fsS --max-time 5 -H "Authorization: Bearer $token" http://127.0.0.1:8765/healthz)"
  HEALTH="$health" python3 -c '\''import json,os; x=json.loads(os.environ["HEALTH"]); assert x.get("status")=="ready"; assert x.get("arbitrary_shell") is False; assert x.get("video_analyzer_result_access") is False; assert x.get("other_oracle_result_access") is False'\''
  echo control_health=ready_bounded
fi
'

case "$MODE" in
  probe)
    run_command assistant-lab-observer-probe "$read_only_script"
    echo ASSISTANT_LAB_OBSERVER_CLOUD_SHELL_PROBE_PASS
    ;;
  status)
    run_command assistant-lab-observer-status "$read_only_script"
    echo ASSISTANT_LAB_OBSERVER_CLOUD_SHELL_STATUS_PASS
    ;;
  activate)
    activate_script="set -Eeuo pipefail
sudo -n true
[[ \"\$(sudo -n systemctl is-active assistant-lab.service)\" == active ]]
cd '$REPO_DIR'
[[ -d .git ]]
[[ -z \"\$(git status --porcelain)\" ]] || { echo repo_dirty; exit 40; }
git fetch --quiet origin main
git merge-base --is-ancestor HEAD '$RUNTIME_COMMIT' || { echo pinned_commit_not_descendant; exit 41; }
git cat-file -e '$RUNTIME_COMMIT^{commit}'
git checkout --quiet main
git merge --ff-only '$RUNTIME_COMMIT'
[[ \"\$(git rev-parse HEAD)\" == '$RUNTIME_COMMIT' ]]
sudo -n env ASSISTANT_LAB_OBSERVER_ACTIVATE=1 ASSISTANT_LAB_REPO_DIR='$REPO_DIR' bash '$REPO_DIR/ops/oracle_assistant_lab_observer_install.sh'
[[ \"\$(sudo -n systemctl is-enabled assistant-lab-observer.service)\" == enabled ]]
[[ \"\$(sudo -n systemctl is-active assistant-lab-observer.service)\" == active ]]
[[ \"\$(sudo -n systemctl is-enabled assistant-lab-control.service)\" == enabled ]]
[[ \"\$(sudo -n systemctl is-active assistant-lab-control.service)\" == active ]]
ss -ltn | grep -Eq '\''127\\.0\\.0\\.1:8765[[:space:]]'\''
! ss -ltn | grep -Eq '\''(^|[[:space:]])0\\.0\\.0\\.0:8765|\\[::\\]:8765'\''
token=\"\$(sudo -n sed -n '\''s/^ASSISTANT_LAB_CONTROL_TOKEN=//p'\'' /opt/bridge-school/assistant-lab-observer/control.env)\"
health=\"\$(curl -fsS --max-time 5 -H \"Authorization: Bearer \$token\" http://127.0.0.1:8765/healthz)\"
HEALTH=\"\$health\" python3 -c '\''import json,os; x=json.loads(os.environ["HEALTH"]); assert x.get("status")=="ready"; assert x.get("arbitrary_shell") is False; assert x.get("video_analyzer_result_access") is False; assert x.get("other_oracle_result_access") is False'\''
ready=\"\$(curl -fsS --max-time 10 http://127.0.0.1:8080/readyz)\"
READY=\"\$ready\" python3 -c '\''import json,os; x=json.loads(os.environ["READY"]); assert x.get("status")=="ready" and x.get("engine")=="DDS3" and x.get("fallback_used") is False'\''
[[ \"\$(sudo -n systemctl is-active assistant-lab.service)\" == active ]]
latest=\"\$(find /opt/bridge-school/assistant-lab-observer/experiments -maxdepth 1 -mindepth 1 -type d -name '\''INSTALL-SMOKE-*'\'' | sort | tail -n1)\"
[[ -n \"\$latest\" && -f \"\$latest/observer/observer_report.json\" && -f \"\$latest/manifest.json\" ]]
python3 - \"\$latest\" <<'PY'
import json,sys,pathlib
p=pathlib.Path(sys.argv[1])
r=json.loads((p/'observer/observer_report.json').read_text())
m=json.loads((p/'manifest.json').read_text())
assert r['exit_code']==0 and r['timed_out'] is False
s=m['separation']
assert s['video_analyzer_result_consumed'] is False
assert s['other_oracle_tool_results_consumed'] is False
print('observer_smoke=pass')
PY
echo observer=active
echo control=active_localhost_only
echo assistant_lab=active
echo dds3=ready_real_no_fallback
echo ASSISTANT_LAB_OBSERVER_HOST_ACTIVATION_PASS"
    out="$(run_command assistant-lab-observer-activate "$activate_script")"
    printf '%s\n' "$out"
    grep -Fx 'ASSISTANT_LAB_OBSERVER_HOST_ACTIVATION_PASS' <<<"$out" >/dev/null || die 'activation marker missing'
    echo ASSISTANT_LAB_OBSERVER_CLOUD_SHELL_ACTIVATION_PASS
    ;;
esac

printf 'issue=%s runtime_commit=%s mode=%s\n' "$ISSUE_NUMBER" "$RUNTIME_COMMIT" "$MODE"
