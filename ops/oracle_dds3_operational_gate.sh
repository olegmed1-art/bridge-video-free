#!/usr/bin/env bash
set -Eeuo pipefail

# Complete the remaining operational gate for the existing Oracle DDS3 VM.
# Run only from an authenticated Oracle Cloud Shell. The script is idempotent,
# never creates a second VM, and refuses to reboot without an AVAILABLE full
# boot-volume backup plus a proven SSH or OCI Run Command control path that can
# verify service health and prove that the boot ID changed.

REGION="${REGION:-eu-frankfurt-1}"
INSTANCE_NAME="${INSTANCE_NAME:-bridge-school-dds3-frankfurt}"
BACKUP_POLICY_NAME="${BACKUP_POLICY_NAME:-Gold}"
BUDGET_NAME="${BUDGET_NAME:-bridge-school-dds3-trial-guard}"
BUDGET_AMOUNT="${BUDGET_AMOUNT:-250}"
BUDGET_EMAIL="${BUDGET_EMAIL:-}"
NONINTERACTIVE="${NONINTERACTIVE:-0}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/bridge_school_dds3_oracle}"
ALLOW_REBOOT="${ALLOW_REBOOT:-0}"
BACKUP_NAME="${BACKUP_NAME:-bridge-school-dds3-pre-reboot-$(date -u +%Y%m%d)}"
EVIDENCE_FILE="${EVIDENCE_FILE:-$HOME/bridge-school-oci-operational-gate.json}"

export OCI_CLI_REGION="$REGION"
export BACKUP_NAME BACKUP_POLICY_NAME

log(){ printf '\n[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }
nullish(){ [[ -z "${1:-}" || "${1:-}" == "null" || "${1:-}" == "None" ]]; }
normalize_list_json(){
  python3 -c '
import json, sys
raw=sys.stdin.read()
decoder=json.JSONDecoder()
pos=0
data=[]
while True:
    while pos < len(raw) and raw[pos].isspace():
        pos += 1
    if pos >= len(raw):
        break
    value, pos = decoder.raw_decode(raw, pos)
    if isinstance(value, dict):
        page=value.get("data", [])
    elif isinstance(value, list):
        page=value
    else:
        raise ValueError("unexpected OCI list JSON document")
    if not isinstance(page, list):
        raise ValueError("unexpected OCI data payload")
    data.extend(page)
print(json.dumps({"data": data}, separators=(",", ":")))
'
}

curl_json_retry(){
  local url="$1" body=""
  for _ in $(seq 1 12); do
    if body="$(curl -fsS --max-time 20 "$url" 2>/dev/null)"; then
      printf '%s' "$body"
      return 0
    fi
    sleep 10
  done
  die "Readiness request failed after 12 attempts: $url"
}

RUN_AGENT_TEXT=""
run_agent_command(){
  local display_name="$1" script_text="$2"
  local content_json target_json command_id execution_json state exit_code
  content_json="$(SCRIPT_TEXT="$script_text" python3 -c '
import json, os
print(json.dumps({
    "source": {"sourceType": "TEXT", "text": os.environ["SCRIPT_TEXT"]},
    "output": {"outputType": "TEXT"},
}, separators=(",", ":")))
')"
  target_json="$(python3 -c 'import json,os; print(json.dumps({"instanceId":os.environ["INSTANCE_ID"]},separators=(",",":")))')"
  command_id="$(oci instance-agent command create \
    --compartment-id "$COMPARTMENT_ID" \
    --content "$content_json" \
    --target "$target_json" \
    --timeout-in-seconds 180 \
    --display-name "$display_name" \
    --query data.id --raw-output)"
  nullish "$command_id" && die "Run Command $display_name was created without an ID"

  execution_json=""
  state=""
  for _ in $(seq 1 90); do
    execution_json="$(oci instance-agent command-execution get \
      --command-id "$command_id" \
      --instance-id "$INSTANCE_ID" \
      --output json 2>/dev/null || true)"
    if [[ -n "$execution_json" ]]; then
      state="$(printf '%s' "$execution_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("data",{}).get("lifecycle-state",""))')"
      case "$state" in
        SUCCEEDED) break ;;
        FAILED|CANCELED|TIMED_OUT) die "Run Command $display_name ended in $state" ;;
      esac
    fi
    sleep 5
  done
  [[ "$state" == "SUCCEEDED" ]] || die "Run Command $display_name did not complete (last state: ${state:-unknown})"
  exit_code="$(printf '%s' "$execution_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("data",{}).get("content",{}).get("exit-code",""))')"
  RUN_AGENT_TEXT="$(printf '%s' "$execution_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("data",{}).get("content",{}).get("text","") or "")')"
  [[ "$exit_code" == "0" ]] || die "Run Command $display_name returned exit code ${exit_code:-unknown}"
}

for command_name in oci python3 curl; do
  command -v "$command_name" >/dev/null 2>&1 || die "$command_name is required"
done
[[ "$REGION" == "eu-frankfurt-1" ]] || die "Refusing non-Frankfurt region: $REGION"

log "Resolve tenancy and the single existing DDS3 instance"
ROOT_JSON="$(oci iam compartment list --include-root --all --output json)"
TENANCY_ID="$(printf '%s' "$ROOT_JSON" | python3 -c 'import json,sys; xs=json.load(sys.stdin).get("data",[]); print(next((x.get("id","") for x in xs if str(x.get("id","")).startswith("ocid1.tenancy.")),""))')"
nullish "$TENANCY_ID" && die "Could not determine tenancy OCID"
COMPARTMENT_ID="${COMPARTMENT_ID:-$TENANCY_ID}"

INSTANCES_JSON="$(oci compute instance list -c "$COMPARTMENT_ID" --display-name "$INSTANCE_NAME" --all --output json)"
INSTANCE_COUNT="$(printf '%s' "$INSTANCES_JSON" | python3 -c 'import json,sys; xs=[x for x in json.load(sys.stdin).get("data",[]) if x.get("lifecycle-state") not in {"TERMINATED","TERMINATING"}]; print(len(xs))')"
[[ "$INSTANCE_COUNT" == "1" ]] || die "Expected exactly one live $INSTANCE_NAME instance, found $INSTANCE_COUNT"
INSTANCE_ID="$(printf '%s' "$INSTANCES_JSON" | python3 -c 'import json,sys; xs=[x for x in json.load(sys.stdin).get("data",[]) if x.get("lifecycle-state") not in {"TERMINATED","TERMINATING"}]; print(xs[0]["id"])')"
export INSTANCE_ID
INSTANCE_JSON="$(oci compute instance get --instance-id "$INSTANCE_ID" --output json)"
STATE="$(printf '%s' "$INSTANCE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"].get("lifecycle-state",""))')"
[[ "$STATE" == "RUNNING" ]] || die "Instance state is $STATE, expected RUNNING"
AD="$(printf '%s' "$INSTANCE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"].get("availability-domain",""))')"
SHAPE="$(printf '%s' "$INSTANCE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"].get("shape",""))')"
OCPU="$(printf '%s' "$INSTANCE_JSON" | python3 -c 'import json,sys; print((json.load(sys.stdin)["data"].get("shape-config") or {}).get("ocpus",""))')"
MEMORY_GB="$(printf '%s' "$INSTANCE_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin)["data"].get("shape-config") or {}; print(d.get("memory-in-gbs",d.get("memoryInGBs","")))')"

VNIC_JSON="$(oci compute instance list-vnics --instance-id "$INSTANCE_ID" --all --output json)"
PUBLIC_IP="$(printf '%s' "$VNIC_JSON" | python3 -c 'import json,sys; xs=json.load(sys.stdin).get("data",[]); print((xs[0].get("public-ip") if xs else "") or "")')"
nullish "$PUBLIC_IP" && die "Instance has no public IP"

ATTACHMENT_JSON="$(oci compute boot-volume-attachment list -c "$COMPARTMENT_ID" --availability-domain "$AD" --instance-id "$INSTANCE_ID" --all --output json)"
BOOT_VOLUME_ID="$(printf '%s' "$ATTACHMENT_JSON" | python3 -c 'import json,sys; xs=[x for x in json.load(sys.stdin).get("data",[]) if x.get("lifecycle-state") in {"ATTACHED","ATTACHING"}]; print(xs[0].get("boot-volume-id","") if len(xs)==1 else "")')"
nullish "$BOOT_VOLUME_ID" && die "Could not resolve exactly one attached boot volume"
BOOT_VOLUME_JSON="$(oci bv boot-volume get --boot-volume-id "$BOOT_VOLUME_ID" --output json)"
BOOT_VOLUME_SIZE_GB="$(printf '%s' "$BOOT_VOLUME_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"].get("size-in-gbs",""))')"

printf 'Instance: %s\nShape: %s\nOCPU/RAM: %s / %s GB\nBoot volume: %s GB\nPublic IP: %s\n' \
  "$INSTANCE_ID" "$SHAPE" "$OCPU" "$MEMORY_GB" "$BOOT_VOLUME_SIZE_GB" "$PUBLIC_IP"

log "Create or reuse today's AVAILABLE full boot-volume backup"
BACKUPS_JSON="$(oci bv boot-volume-backup list -c "$COMPARTMENT_ID" --boot-volume-id "$BOOT_VOLUME_ID" --all --output json | normalize_list_json)"
BACKUP_ID="$(printf '%s' "$BACKUPS_JSON" | python3 -c 'import json,sys,os; name=os.environ["BACKUP_NAME"]; xs=[x for x in json.load(sys.stdin).get("data",[]) if x.get("display-name")==name and x.get("lifecycle-state")=="AVAILABLE" and x.get("type")=="FULL"]; xs.sort(key=lambda x:x.get("time-created",""),reverse=True); print(xs[0].get("id","") if xs else "")')"
if nullish "$BACKUP_ID"; then
  BACKUP_JSON="$(oci bv boot-volume-backup create \
    --boot-volume-id "$BOOT_VOLUME_ID" \
    --display-name "$BACKUP_NAME" \
    --type FULL \
    --wait-for-state AVAILABLE \
    --max-wait-seconds 3600 \
    --output json)"
  BACKUP_ID="$(printf '%s' "$BACKUP_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"].get("id",""))')"
fi
nullish "$BACKUP_ID" && die "Full backup did not reach AVAILABLE"
BACKUP_STATE="$(oci bv boot-volume-backup get --boot-volume-backup-id "$BACKUP_ID" --query 'data."lifecycle-state"' --raw-output)"
[[ "$BACKUP_STATE" == "AVAILABLE" ]] || die "Backup state is $BACKUP_STATE"

log "Enforce the exact daily boot-volume backup policy"
POLICIES_JSON="$(oci bv volume-backup-policy list --all --output json)"
POLICY_ID="$(printf '%s' "$POLICIES_JSON" | python3 -c 'import json,sys,os; name=os.environ["BACKUP_POLICY_NAME"].lower(); xs=[x for x in json.load(sys.stdin).get("data",[]) if str(x.get("display-name","")).lower()==name]; print(xs[0].get("id","") if len(xs)==1 else "")')"
nullish "$POLICY_ID" && die "Could not resolve exactly one Oracle-defined $BACKUP_POLICY_NAME policy"
ASSIGNMENT_JSON="$(oci bv volume-backup-policy-assignment get-volume-backup-policy-asset-assignment --asset-id "$BOOT_VOLUME_ID" --output json | normalize_list_json)"
ASSIGNED_POLICY_ID="$(printf '%s' "$ASSIGNMENT_JSON" | python3 -c 'import json,sys; xs=json.load(sys.stdin).get("data",[]); print(xs[0].get("policy-id","") if xs else "")')"
ASSIGNMENT_ID="$(printf '%s' "$ASSIGNMENT_JSON" | python3 -c 'import json,sys; xs=json.load(sys.stdin).get("data",[]); print(xs[0].get("id","") if xs else "")')"
if [[ "$ASSIGNED_POLICY_ID" != "$POLICY_ID" ]]; then
  PREVIOUS_POLICY_ID="$ASSIGNED_POLICY_ID"
  if ! nullish "$ASSIGNMENT_ID"; then
    oci bv volume-backup-policy-assignment delete --policy-assignment-id "$ASSIGNMENT_ID" --force
  fi
  if ! ASSIGNMENT_JSON="$(oci bv volume-backup-policy-assignment create --asset-id "$BOOT_VOLUME_ID" --policy-id "$POLICY_ID" --output json)"; then
    if ! nullish "$PREVIOUS_POLICY_ID"; then
      oci bv volume-backup-policy-assignment create --asset-id "$BOOT_VOLUME_ID" --policy-id "$PREVIOUS_POLICY_ID" --output json >/dev/null || true
    fi
    die "Failed to assign $BACKUP_POLICY_NAME policy; previous assignment restoration attempted"
  fi
fi
ASSIGNMENT_JSON="$(oci bv volume-backup-policy-assignment get-volume-backup-policy-asset-assignment --asset-id "$BOOT_VOLUME_ID" --output json | normalize_list_json)"
ASSIGNED_POLICY_ID="$(printf '%s' "$ASSIGNMENT_JSON" | python3 -c 'import json,sys; xs=json.load(sys.stdin).get("data",[]); print(xs[0].get("policy-id","") if len(xs)==1 else "")')"
[[ "$ASSIGNED_POLICY_ID" == "$POLICY_ID" ]] || die "Exact $BACKUP_POLICY_NAME backup policy assignment is not proven"

log "Ensure monthly budget and five alert rules"
BUDGETS_JSON="$(oci budgets budget budget list -c "$TENANCY_ID" --display-name "$BUDGET_NAME" --all --output json | normalize_list_json)"
BUDGET_ID="$(printf '%s' "$BUDGETS_JSON" | python3 -c 'import json,sys; xs=json.load(sys.stdin).get("data",[]); xs.sort(key=lambda x:x.get("time-created",""),reverse=True); print(xs[0].get("id","") if xs else "")')"
if nullish "$BUDGET_ID"; then
  BUDGET_ID="$(oci budgets budget budget create \
    --amount "$BUDGET_AMOUNT" \
    --compartment-id "$TENANCY_ID" \
    --reset-period MONTHLY \
    --display-name "$BUDGET_NAME" \
    --target-type COMPARTMENT \
    --targets "[\"$COMPARTMENT_ID\"]" \
    --wait-for-state ACTIVE \
    --query data.id --raw-output)"
fi
nullish "$BUDGET_ID" && die "Budget is missing"
BUDGET_JSON="$(oci budgets budget budget get --budget-id "$BUDGET_ID" --output json)"
BUDGET_AMOUNT_MATCH="$(printf '%s' "$BUDGET_JSON" | EXPECTED="$BUDGET_AMOUNT" python3 -c 'import json,sys,os; actual=float(json.load(sys.stdin)["data"].get("amount",-1)); expected=float(os.environ["EXPECTED"]); print("1" if actual==expected else "0")')"
if [[ "$BUDGET_AMOUNT_MATCH" == "0" ]]; then
  oci budgets budget budget update \
    --budget-id "$BUDGET_ID" \
    --amount "$BUDGET_AMOUNT" \
    --reset-period MONTHLY \
    --force \
    --wait-for-state ACTIVE >/dev/null
  BUDGET_JSON="$(oci budgets budget budget get --budget-id "$BUDGET_ID" --output json)"
fi
printf '%s' "$BUDGET_JSON" | EXPECTED="$BUDGET_AMOUNT" TARGET="$COMPARTMENT_ID" python3 -c '
import json, os, sys
x=json.load(sys.stdin)["data"]
assert float(x.get("amount",-1)) == float(os.environ["EXPECTED"]), x
assert x.get("reset-period") == "MONTHLY", x
assert x.get("target-type") == "COMPARTMENT", x
assert x.get("targets") == [os.environ["TARGET"]], x
'
ALERTS_JSON="$(oci budgets budget alert-rule list --budget-id "$BUDGET_ID" --all --output json | normalize_list_json)"
if [[ -z "$BUDGET_EMAIL" ]]; then
  BUDGET_EMAIL="$(printf '%s' "$ALERTS_JSON" | python3 -c '
import json, re, sys
emails=set()
for rule in json.load(sys.stdin).get("data", []):
    for item in re.split(r"[,;\s]+", str(rule.get("recipients") or "")):
        if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", item):
            emails.add(item)
print(next(iter(emails)) if len(emails) == 1 else "")
')"
fi
if [[ -z "$BUDGET_EMAIL" && "$NONINTERACTIVE" != "1" && -t 0 ]]; then
  read -r -p 'Email for OCI budget alerts: ' BUDGET_EMAIL
fi
[[ "$BUDGET_EMAIL" == *@*.* ]] || die "A unique BUDGET_EMAIL secret or existing OCI alert recipient is required"
wait_alert_active(){
  local alert_rule_id="$1" state=""
  for _ in $(seq 1 30); do
    state="$(oci budgets budget alert-rule get \
      --budget-id "$BUDGET_ID" \
      --alert-rule-id "$alert_rule_id" \
      --query 'data."lifecycle-state"' --raw-output 2>/dev/null || true)"
    [[ "$state" == "ACTIVE" ]] && return 0
    sleep 2
  done
  die "Alert rule $alert_rule_id did not reach ACTIVE (last state: ${state:-unknown})"
}
verify_alert_config(){
  local alert_rule_id="$1" alert_type="$2" threshold="$3" display_name="$4"
  oci budgets budget alert-rule get \
    --budget-id "$BUDGET_ID" \
    --alert-rule-id "$alert_rule_id" \
    --output json | \
    EXPECTED_TYPE="$alert_type" EXPECTED_THRESHOLD="$threshold" EXPECTED_NAME="$display_name" EXPECTED_EMAIL="$BUDGET_EMAIL" python3 -c '
import json, os, re, sys
x=json.load(sys.stdin)["data"]
assert x.get("display-name") == os.environ["EXPECTED_NAME"], x
assert x.get("type") == os.environ["EXPECTED_TYPE"], x
assert x.get("threshold-type") == "PERCENTAGE", x
assert float(x.get("threshold", -1)) == float(os.environ["EXPECTED_THRESHOLD"]), x
recipients={p for p in re.split(r"[,;\s]+", str(x.get("recipients") or "")) if p}
assert os.environ["EXPECTED_EMAIL"] in recipients, x
assert x.get("lifecycle-state") == "ACTIVE", x
'
}
ensure_alert(){
  local alert_type="$1" threshold="$2" display_name="$3"
  local alert_id alert_count
  alert_count="$(printf '%s' "$ALERTS_JSON" | ALERT_NAME="$display_name" python3 -c 'import json,sys,os; name=os.environ["ALERT_NAME"]; xs=[x for x in json.load(sys.stdin).get("data",[]) if x.get("display-name")==name]; print(len(xs))')"
  [[ "$alert_count" == "0" || "$alert_count" == "1" ]] || die "Duplicate budget alert name: $display_name"
  alert_id="$(printf '%s' "$ALERTS_JSON" | ALERT_NAME="$display_name" python3 -c 'import json,sys,os; name=os.environ["ALERT_NAME"]; xs=[x for x in json.load(sys.stdin).get("data",[]) if x.get("display-name")==name]; print(xs[0].get("id","") if xs else "")')"
  if nullish "$alert_id"; then
    alert_id="$(oci budgets budget alert-rule create \
      --budget-id "$BUDGET_ID" \
      --display-name "$display_name" \
      --description "Bridge School Oracle guard $display_name" \
      --threshold "$threshold" \
      --threshold-type PERCENTAGE \
      --type "$alert_type" \
      --recipients "$BUDGET_EMAIL" \
      --message "OCI Bridge School budget alert: $display_name" \
      --query data.id --raw-output)"
    nullish "$alert_id" && die "Created alert $display_name without an ID"
    wait_alert_active "$alert_id"
  fi
  verify_alert_config "$alert_id" "$alert_type" "$threshold" "$display_name"
}
ensure_alert ACTUAL 50 actual-50
ensure_alert ACTUAL 75 actual-75
ensure_alert ACTUAL 90 actual-90
ensure_alert ACTUAL 100 actual-100
ensure_alert FORECAST 90 forecast-90

log "Verify public DDS3 readiness before any reboot"
READY_BEFORE="$(curl -fsS --max-time 20 "https://$PUBLIC_IP/readyz")"
printf '%s' "$READY_BEFORE" | python3 -c 'import json,sys; x=json.load(sys.stdin); assert x.get("status")=="ready" and x.get("engine")=="DDS3" and x.get("fallback_used") is False, x'

REBOOTED=false
BOOT_ID_BEFORE=""
BOOT_ID_AFTER=""
HOST_DISK="not-collected"
HOST_MEMORY="not-collected"
CONTROL_PATH="not-used"
if [[ "$ALLOW_REBOOT" != "1" ]]; then
  PLUGINS_JSON="$(oci instance-agent plugin list \
    --compartment-id "$COMPARTMENT_ID" \
    --instanceagent-id "$INSTANCE_ID" \
    --all --output json)"
  RUN_COMMAND_STATUS="$(printf '%s' "$PLUGINS_JSON" | python3 -c 'import json,sys; xs=json.load(sys.stdin).get("data",[]); print(next((x.get("status","") for x in xs if "Run Command" in str(x.get("name",""))),""))')"
  [[ "$RUN_COMMAND_STATUS" == "RUNNING" ]] || die "Preflight refused: OCI Run Command status is ${RUN_COMMAND_STATUS:-unknown}"
  CONTROL_PATH="oci-run-command"
  run_agent_command "bridge-dds3-operational-preflight-$(date -u +%Y%m%d%H%M%S)" $'set -Eeuo pipefail\nsystemctl is-active --quiet assistant-lab.service bridge-ben.service bridge-ben-healthcheck.timer nginx docker dds3-healthcheck.timer dds3-cert-renew.timer\ncurl -fsS --max-time 8 http://127.0.0.1:8080/readyz >/dev/null\ncurl -fsS --max-time 15 "http://127.0.0.1:8085/bid?hand=AK97543.K.T3.AK7&seat=S&dealer=N&vul=&ctx=----&details=true" >/dev/null\nprintf "BOOT_ID=%s\\n" "$(cat /proc/sys/kernel/random/boot_id)"\nprintf "HOST_DISK=%s\\n" "$(df -h / | tail -n 1)"\nprintf "HOST_MEMORY=%s\\n" "$(free -h | sed -n "2p")"'
  BOOT_ID_BEFORE="$(printf '%s\n' "$RUN_AGENT_TEXT" | sed -n 's/^BOOT_ID=//p' | head -n 1)"
  HOST_DISK="$(printf '%s\n' "$RUN_AGENT_TEXT" | sed -n 's/^HOST_DISK=//p' | head -n 1)"
  HOST_MEMORY="$(printf '%s\n' "$RUN_AGENT_TEXT" | sed -n 's/^HOST_MEMORY=//p' | head -n 1)"
  [[ -n "$BOOT_ID_BEFORE" && -n "$HOST_DISK" && -n "$HOST_MEMORY" ]] || die "Run Command preflight returned incomplete capacity evidence"
fi
if [[ "$ALLOW_REBOOT" == "1" ]]; then
  if [[ -f "$SSH_KEY" ]]; then
    command -v ssh >/dev/null 2>&1 || die "ssh is required when SSH_KEY is present"
    CONTROL_PATH="ssh"
    chmod 600 "$SSH_KEY"
    SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new "ubuntu@$PUBLIC_IP")
    BOOT_ID_BEFORE="$("${SSH[@]}" 'cat /proc/sys/kernel/random/boot_id')"
    HOST_DISK="$("${SSH[@]}" 'df -h / | tail -n 1')"
    HOST_MEMORY="$("${SSH[@]}" "free -h | sed -n '2p'")"
    "${SSH[@]}" 'sudo systemctl is-active --quiet assistant-lab.service bridge-ben.service bridge-ben-healthcheck.timer nginx docker dds3-healthcheck.timer dds3-cert-renew.timer; curl -fsS --max-time 8 http://127.0.0.1:8080/readyz >/dev/null; curl -fsS --max-time 15 "http://127.0.0.1:8085/bid?hand=AK97543.K.T3.AK7&seat=S&dealer=N&vul=&ctx=----&details=true" >/dev/null'
  else
    PLUGINS_JSON="$(oci instance-agent plugin list \
      --compartment-id "$COMPARTMENT_ID" \
      --instanceagent-id "$INSTANCE_ID" \
      --all --output json)"
    RUN_COMMAND_STATUS="$(printf '%s' "$PLUGINS_JSON" | python3 -c 'import json,sys; xs=json.load(sys.stdin).get("data",[]); print(next((x.get("status","") for x in xs if "Run Command" in str(x.get("name",""))),""))')"
    [[ "$RUN_COMMAND_STATUS" == "RUNNING" ]] || die "Reboot refused: no SSH key and Run Command status is ${RUN_COMMAND_STATUS:-unknown}"
    CONTROL_PATH="oci-run-command"
    run_agent_command "bridge-dds3-pre-reboot-$(date -u +%Y%m%d%H%M%S)" $'set -Eeuo pipefail\nsystemctl is-active --quiet assistant-lab.service bridge-ben.service bridge-ben-healthcheck.timer nginx docker dds3-healthcheck.timer dds3-cert-renew.timer\ncurl -fsS --max-time 8 http://127.0.0.1:8080/readyz >/dev/null\ncurl -fsS --max-time 15 "http://127.0.0.1:8085/bid?hand=AK97543.K.T3.AK7&seat=S&dealer=N&vul=&ctx=----&details=true" >/dev/null\nprintf "BOOT_ID=%s\\n" "$(cat /proc/sys/kernel/random/boot_id)"\nprintf "HOST_DISK=%s\\n" "$(df -h / | tail -n 1)"\nprintf "HOST_MEMORY=%s\\n" "$(free -h | sed -n "2p")"'
    BOOT_ID_BEFORE="$(printf '%s\n' "$RUN_AGENT_TEXT" | sed -n 's/^BOOT_ID=//p' | head -n 1)"
    HOST_DISK="$(printf '%s\n' "$RUN_AGENT_TEXT" | sed -n 's/^HOST_DISK=//p' | head -n 1)"
    HOST_MEMORY="$(printf '%s\n' "$RUN_AGENT_TEXT" | sed -n 's/^HOST_MEMORY=//p' | head -n 1)"
    [[ -n "$BOOT_ID_BEFORE" ]] || die "Run Command preflight returned no boot ID"
  fi

  log "Perform OCI SOFTRESET only after backup and preflight success"
  oci compute instance action --instance-id "$INSTANCE_ID" --action SOFTRESET >/dev/null
  if [[ "$CONTROL_PATH" == "ssh" ]]; then
    sleep 20
    for _ in $(seq 1 90); do
      if BOOT_ID_AFTER="$("${SSH[@]}" 'cat /proc/sys/kernel/random/boot_id' 2>/dev/null)" && [[ -n "$BOOT_ID_AFTER" && "$BOOT_ID_AFTER" != "$BOOT_ID_BEFORE" ]]; then
        break
      fi
      sleep 10
    done
    "${SSH[@]}" 'sudo systemctl is-active --quiet assistant-lab.service bridge-ben.service bridge-ben-healthcheck.timer nginx docker dds3-healthcheck.timer dds3-cert-renew.timer; curl -fsS --max-time 8 http://127.0.0.1:8080/readyz >/dev/null; curl -fsS --max-time 15 "http://127.0.0.1:8085/bid?hand=AK97543.K.T3.AK7&seat=S&dealer=N&vul=&ctx=----&details=true" >/dev/null'
  else
    for _ in $(seq 1 90); do
      curl -fsS --max-time 10 "https://$PUBLIC_IP/readyz" >/dev/null 2>&1 && break
      sleep 10
    done
    run_agent_command "bridge-dds3-post-reboot-$(date -u +%Y%m%d%H%M%S)" $'set -Eeuo pipefail\nsystemctl is-active --quiet assistant-lab.service bridge-ben.service bridge-ben-healthcheck.timer nginx docker dds3-healthcheck.timer dds3-cert-renew.timer\ncurl -fsS --max-time 8 http://127.0.0.1:8080/readyz >/dev/null\ncurl -fsS --max-time 15 "http://127.0.0.1:8085/bid?hand=AK97543.K.T3.AK7&seat=S&dealer=N&vul=&ctx=----&details=true" >/dev/null\nprintf "BOOT_ID=%s\\n" "$(cat /proc/sys/kernel/random/boot_id)"'
    BOOT_ID_AFTER="$(printf '%s\n' "$RUN_AGENT_TEXT" | sed -n 's/^BOOT_ID=//p' | head -n 1)"
  fi
  [[ -n "$BOOT_ID_AFTER" && "$BOOT_ID_AFTER" != "$BOOT_ID_BEFORE" ]] || die "Could not prove a completed reboot by boot_id"
  REBOOTED=true
fi

log "Final external Oracle and Vercel checks"
READY_AFTER="$(curl_json_retry "https://$PUBLIC_IP/readyz")"
ROUTED_AFTER="$(curl_json_retry 'https://bridge-video-free.vercel.app/dds3/readyz')"
printf '%s' "$READY_AFTER" | python3 -c 'import json,sys; x=json.load(sys.stdin); assert x.get("status")=="ready" and x.get("engine")=="DDS3" and x.get("fallback_used") is False, x'
printf '%s' "$ROUTED_AFTER" | python3 -c 'import json,sys; x=json.load(sys.stdin); assert x.get("status")=="ready" and x.get("engine")=="DDS3" and x.get("authenticated_compute")=="ready" and x.get("fallback_used") is False, x'

export INSTANCE_ID SHAPE OCPU MEMORY_GB BOOT_VOLUME_ID BOOT_VOLUME_SIZE_GB BACKUP_ID BACKUP_STATE
export ASSIGNED_POLICY_ID BUDGET_ID BUDGET_AMOUNT REBOOTED BOOT_ID_BEFORE BOOT_ID_AFTER HOST_DISK HOST_MEMORY CONTROL_PATH
export BUDGET_EMAIL_CONFIGURED=true
python3 - <<'PY' >"$EVIDENCE_FILE"
import json, os
keys = [
    "INSTANCE_ID", "SHAPE", "OCPU", "MEMORY_GB", "BOOT_VOLUME_ID",
    "BOOT_VOLUME_SIZE_GB", "BACKUP_ID", "BACKUP_STATE", "ASSIGNED_POLICY_ID",
    "BUDGET_ID", "BUDGET_AMOUNT", "REBOOTED", "BOOT_ID_BEFORE", "BOOT_ID_AFTER",
    "HOST_DISK", "HOST_MEMORY", "CONTROL_PATH", "BUDGET_EMAIL_CONFIGURED",
]
print(json.dumps({"status": "PASS", **{k.lower(): os.environ.get(k, "") for k in keys}}, indent=2, sort_keys=True))
PY
chmod 600 "$EVIDENCE_FILE"
cat "$EVIDENCE_FILE"

if [[ "$ALLOW_REBOOT" != "1" ]]; then
  printf '\nOCI_OPERATIONAL_GATE_PRE_REBOOT_PASS\n'
  printf 'Backup, recurring policy, budget alerts, capacity, and readiness are complete.\n'
  printf 'Re-run with ALLOW_REBOOT=1 to perform and prove the planned full reboot.\n'
else
  printf '\nOCI_OPERATIONAL_GATE_PASS\n'
fi
