#!/usr/bin/env bash
set -Eeuo pipefail

readonly REGION='eu-frankfurt-1'
readonly INSTANCE_NAME='bridge-school-dds3-frankfurt'
export OCI_CLI_REGION="$REGION"

die(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
for cmd in oci python3; do command -v "$cmd" >/dev/null 2>&1 || die "$cmd is required"; done

ROOT_JSON="$(oci iam compartment list --include-root --all --output json)"
TENANCY_ID="$(printf '%s' "$ROOT_JSON" | python3 -c 'import json,sys; xs=json.load(sys.stdin).get("data",[]); print(next((x.get("id","") for x in xs if str(x.get("id","")).startswith("ocid1.tenancy.")),""))')"
[[ -n "$TENANCY_ID" ]] || die 'could not determine tenancy'
COMPARTMENT_ID="$TENANCY_ID"
INSTANCES_JSON="$(oci compute instance list -c "$COMPARTMENT_ID" --display-name "$INSTANCE_NAME" --all --output json)"
INSTANCE_ID="$(printf '%s' "$INSTANCES_JSON" | python3 -c 'import json,sys; xs=[x for x in json.load(sys.stdin).get("data",[]) if x.get("lifecycle-state") not in {"TERMINATED","TERMINATING"}]; print(xs[0].get("id","") if len(xs)==1 else "")')"
[[ -n "$INSTANCE_ID" ]] || die "expected exactly one live $INSTANCE_NAME"
export INSTANCE_ID

REMOTE=$(cat <<'REMOTE'
set -u
printf 'HOST_DIAG_START\n'
printf 'user=%s\n' "$(id -un 2>&1 || true)"
printf 'hostname=%s\n' "$(hostname 2>&1 || true)"
printf 'repo_exists='; test -d /opt/bridge-school/bridge-video-free/.git && echo yes || echo no
printf 'repo_head='; git -C /opt/bridge-school/bridge-video-free rev-parse HEAD 2>&1 || true
printf 'repo_status_begin\n'; git -C /opt/bridge-school/bridge-video-free status --porcelain 2>&1 || true; printf 'repo_status_end\n'
printf 'assistant_lab='; systemctl is-active assistant-lab.service 2>&1 || true
printf 'observer='; systemctl is-active assistant-lab-observer.service 2>&1 || true
printf 'control='; systemctl is-active assistant-lab-control.service 2>&1 || true
printf 'bridge='; systemctl is-active assistant-lab-control-bridge.service 2>&1 || true
printf 'dds3_ready='; curl -fsS --max-time 5 http://127.0.0.1:8080/readyz 2>&1 || true
printf '\nHOST_DIAG_END\n'
REMOTE
)

CONTENT="$(SCRIPT_TEXT="$REMOTE" python3 -c 'import json,os; print(json.dumps({"source":{"sourceType":"TEXT","text":os.environ["SCRIPT_TEXT"]},"output":{"outputType":"TEXT"}},separators=(",",":")))')"
TARGET="$(python3 -c 'import json,os; print(json.dumps({"instanceId":os.environ["INSTANCE_ID"]},separators=(",",":")))')"
CMD_ID="$(oci instance-agent command create --compartment-id "$COMPARTMENT_ID" --content "$CONTENT" --target "$TARGET" --timeout-in-seconds 120 --display-name assistant-lab-host-diagnostic --query data.id --raw-output)"
[[ -n "$CMD_ID" && "$CMD_ID" != null ]] || die 'Run Command creation failed'
printf 'CMD_ID=%s\n' "$CMD_ID"

for _ in $(seq 1 60); do
  EXECUTION="$(oci instance-agent command-execution get --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" --output json 2>/dev/null || true)"
  [[ -n "$EXECUTION" ]] || { sleep 2; continue; }
  STATE="$(printf '%s' "$EXECUTION" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("data",{}).get("lifecycle-state",""))')"
  case "$STATE" in SUCCEEDED|FAILED|CANCELED|TIMED_OUT) break ;; esac
  sleep 2
done

printf 'STATE=%s\n' "${STATE:-UNKNOWN}"
printf '%s' "$EXECUTION" | python3 -c 'import json,sys; d=json.load(sys.stdin).get("data",{}); c=d.get("content",{}) or {}; print("EXIT_CODE="+str(c.get("exit-code",""))); print("TEXT_BEGIN"); print(c.get("text","") or ""); print("TEXT_END")'
[[ "${STATE:-}" == SUCCEEDED ]] || exit 2
