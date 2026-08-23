#!/usr/bin/env bash
set -Eeuo pipefail

# One-time bootstrap for the outbound Neon control bridge. After this succeeds,
# routine Assistant Lab control no longer needs OCI Run Command or SSH.

readonly REGION='eu-frankfurt-1'
readonly INSTANCE_NAME='bridge-school-dds3-frankfurt'
readonly RUNTIME_COMMIT='32eed77de0184ab7099249187f916ab42c35a3e7'
readonly REPOSITORY='olegmed1-art/bridge-video-free'

export OCI_CLI_REGION="$REGION"

log(){ printf '\n[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[[ "$#" -eq 1 ]] || die "usage: $0 probe|activate|status"
MODE="$1"
case "$MODE" in probe|activate|status) ;; *) die "unsupported mode: $MODE" ;; esac
for cmd in oci python3; do command -v "$cmd" >/dev/null 2>&1 || die "$cmd is required"; done

ROOT_JSON="$(oci iam compartment list --include-root --all --output json)"
TENANCY_ID="$(printf '%s' "$ROOT_JSON" | python3 -c 'import json,sys; xs=json.load(sys.stdin).get("data",[]); print(next((x.get("id","") for x in xs if str(x.get("id","")).startswith("ocid1.tenancy.")),""))')"
[[ -n "$TENANCY_ID" ]] || die "could not determine tenancy"
COMPARTMENT_ID="${COMPARTMENT_ID:-$TENANCY_ID}"
INSTANCES_JSON="$(oci compute instance list -c "$COMPARTMENT_ID" --display-name "$INSTANCE_NAME" --all --output json)"
INSTANCE_ID="$(printf '%s' "$INSTANCES_JSON" | python3 -c 'import json,sys; xs=[x for x in json.load(sys.stdin).get("data",[]) if x.get("lifecycle-state") not in {"TERMINATED","TERMINATING"}]; print(xs[0].get("id","") if len(xs)==1 else "")')"
[[ -n "$INSTANCE_ID" ]] || die "expected exactly one live $INSTANCE_NAME"
export INSTANCE_ID COMPARTMENT_ID

run_agent(){
  local name="$1" text="$2" content target id execution state code
  content="$(SCRIPT_TEXT="$text" python3 -c 'import json,os; print(json.dumps({"source":{"sourceType":"TEXT","text":os.environ["SCRIPT_TEXT"]},"output":{"outputType":"TEXT"}},separators=(",",":")))')"
  target="$(python3 -c 'import json,os; print(json.dumps({"instanceId":os.environ["INSTANCE_ID"]},separators=(",",":")))')"
  id="$(oci instance-agent command create --compartment-id "$COMPARTMENT_ID" --content "$content" --target "$target" --timeout-in-seconds 600 --display-name "$name" --query data.id --raw-output)"
  [[ -n "$id" && "$id" != null ]] || die "Run Command creation failed"
  state=""
  for _ in $(seq 1 120); do
    execution="$(oci instance-agent command-execution get --command-id "$id" --instance-id "$INSTANCE_ID" --output json 2>/dev/null || true)"
    if [[ -n "$execution" ]]; then
      state="$(printf '%s' "$execution" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("data",{}).get("lifecycle-state",""))')"
      case "$state" in SUCCEEDED) break ;; FAILED|CANCELED|TIMED_OUT) die "Run Command ended in $state" ;; esac
    fi
    sleep 5
  done
  [[ "$state" == SUCCEEDED ]] || die "Run Command did not complete"
  code="$(printf '%s' "$execution" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("data",{}).get("content",{}).get("exit-code",""))')"
  RUN_TEXT="$(printf '%s' "$execution" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("data",{}).get("content",{}).get("text","") or "")')"
  printf '%s\n' "$RUN_TEXT"
  [[ "$code" == 0 ]] || die "remote exit code ${code:-unknown}"
}

REMOTE_COMMON=$(cat <<'REMOTE'
set -Eeuo pipefail
REPO=/opt/bridge-school/bridge-video-free
[[ -d "$REPO/.git" ]] || { echo REPO_MISSING; exit 1; }
cd "$REPO"
[[ -z "$(git status --porcelain)" ]] || { echo REPO_DIRTY; exit 1; }
git fetch --quiet origin
TARGET='32eed77de0184ab7099249187f916ab42c35a3e7'
git cat-file -e "$TARGET^{commit}"
git checkout --detach "$TARGET" >/dev/null
[[ "$(git rev-parse HEAD)" == "$TARGET" ]]
systemctl is-active --quiet assistant-lab.service
curl -fsS --max-time 10 http://127.0.0.1:8080/readyz | python3 -c 'import json,sys; x=json.load(sys.stdin); assert x.get("status")=="ready" and x.get("engine")=="DDS3" and x.get("fallback_used") is False'
REMOTE
)

case "$MODE" in
  probe)
    run_agent assistant-lab-control-bridge-probe "$REMOTE_COMMON
systemctl is-active assistant-lab-control.service >/dev/null 2>&1 || true
echo ASSISTANT_LAB_CONTROL_BRIDGE_PROBE_PASS"
    ;;
  activate)
    run_agent assistant-lab-control-bridge-activate "$REMOTE_COMMON
ASSISTANT_LAB_OBSERVER_ACTIVATE=1 bash ops/oracle_assistant_lab_observer_install.sh
ASSISTANT_LAB_CONTROL_BRIDGE_ACTIVATE=1 bash ops/oracle_assistant_lab_control_bridge_install.sh
systemctl is-active --quiet assistant-lab-observer.service
systemctl is-active --quiet assistant-lab-control.service
systemctl is-active --quiet assistant-lab-control-bridge.service
systemctl is-enabled --quiet assistant-lab-control-bridge.service
ss -ltn | grep -q '127.0.0.1:8765'
! ss -ltn | grep -q '0.0.0.0:8765'
echo ASSISTANT_LAB_DIRECT_CONTROL_ACTIVATION_PASS"
    ;;
  status)
    run_agent assistant-lab-control-bridge-status "$REMOTE_COMMON
printf 'observer=%s\n' \"$(systemctl is-active assistant-lab-observer.service 2>/dev/null || true)\"
printf 'control=%s\n' \"$(systemctl is-active assistant-lab-control.service 2>/dev/null || true)\"
printf 'bridge=%s\n' \"$(systemctl is-active assistant-lab-control-bridge.service 2>/dev/null || true)\"
ss -ltn | grep '127.0.0.1:8765' || true
echo ASSISTANT_LAB_DIRECT_CONTROL_STATUS_PASS"
    ;;
esac
