#!/usr/bin/env bash
set -Eeuo pipefail

# Bridge School DDS3 - Oracle OCI bootstrap
# Safe goals:
# - Frankfurt only
# - one VM only
# - no account Upgrade / no billing-plan changes
# - x86 E6/E5/E4 only; fail closed if unavailable
# - public ingress: SSH/22 only
# - DDS3 HTTP runtime bound to localhost on the VM

REGION="${REGION:-eu-frankfurt-1}"
INSTANCE_NAME="${INSTANCE_NAME:-bridge-school-dds3-frankfurt}"
VCN_NAME="${VCN_NAME:-bridge-school-dds3-vcn}"
SUBNET_NAME="${SUBNET_NAME:-bridge-school-dds3-public}"
IGW_NAME="${IGW_NAME:-bridge-school-dds3-igw}"
VCN_CIDR="${VCN_CIDR:-10.77.0.0/16}"
SUBNET_CIDR="${SUBNET_CIDR:-10.77.10.0/24}"
REPO_URL="https://github.com/olegmed1-art/bridge-video-free.git"
REPO_REF="e84f7591a5b301c3ceb0339b2d09c60caf85732f"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/bridge_school_dds3_oracle}"
WORKDIR="${WORKDIR:-$HOME/.bridge-school-oracle-dds3}"

export OCI_CLI_REGION="$REGION"
mkdir -p "$WORKDIR" "$HOME/.ssh"
chmod 700 "$HOME/.ssh"

log() { printf '\n[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
warn() { printf '\nWARNING: %s\n' "$*" >&2; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
nullish() { [[ -z "${1:-}" || "${1:-}" == "null" || "${1:-}" == "None" ]]; }

command -v oci >/dev/null 2>&1 || die "OCI CLI not found. Run this in Oracle Cloud Shell."
command -v python3 >/dev/null 2>&1 || die "python3 not found."
command -v ssh >/dev/null 2>&1 || die "ssh not found."
command -v ssh-keygen >/dev/null 2>&1 || die "ssh-keygen not found."

log "Preflight: OCI identity, tenancy, Frankfurt"
ROOT_JSON="$(oci iam compartment list --include-root --all --output json)" || die "OCI CLI authentication failed."
TENANCY_ID="$(printf '%s' "$ROOT_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin).get("data",[]); print(next((x.get("id","") for x in d if str(x.get("id","")).startswith("ocid1.tenancy.")), ""))')"
nullish "$TENANCY_ID" && die "Could not determine tenancy OCID from the active OCI CLI profile."
COMPARTMENT_ID="${COMPARTMENT_ID:-$TENANCY_ID}"

if ! oci iam region-subscription list --output json | python3 -c 'import json,sys; r="eu-frankfurt-1"; d=json.load(sys.stdin).get("data",[]); raise SystemExit(0 if any(x.get("region-name")==r for x in d) else 1)'; then
  die "Tenancy is not subscribed to eu-frankfurt-1. No resources were created."
fi

printf 'Region:      %s\n' "$REGION"
printf 'Compartment: %s\n' "$COMPARTMENT_ID"
printf 'Repo ref:    %s\n' "$REPO_REF"

log "SSH key"
if [[ ! -f "$SSH_KEY" || ! -f "$SSH_KEY.pub" ]]; then
  ssh-keygen -t rsa -b 3072 -N '' -C 'bridge-school-dds3-oracle' -f "$SSH_KEY" >/dev/null
  log "Created dedicated key: $SSH_KEY"
else
  log "Reusing dedicated key: $SSH_KEY"
fi
chmod 600 "$SSH_KEY"
chmod 644 "$SSH_KEY.pub"

log "Network: dedicated VCN (idempotent)"
VCN_ID="$(oci network vcn list -c "$COMPARTMENT_ID" --display-name "$VCN_NAME" --all --query 'data[0].id' --raw-output 2>/dev/null || true)"
if nullish "$VCN_ID"; then
  VCN_ID="$(oci network vcn create -c "$COMPARTMENT_ID" --cidr-block "$VCN_CIDR" --display-name "$VCN_NAME" --dns-label dds3vcn --wait-for-state AVAILABLE --query 'data.id' --raw-output)"
  log "Created VCN: $VCN_ID"
else
  log "Reusing VCN: $VCN_ID"
fi

VCN_DATA="$(oci network vcn get --vcn-id "$VCN_ID" --output json)"
ROUTE_TABLE_ID="$(printf '%s' "$VCN_DATA" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["default-route-table-id"])')"
SECURITY_LIST_ID="$(printf '%s' "$VCN_DATA" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["default-security-list-id"])')"

IGW_ID="$(oci network internet-gateway list -c "$COMPARTMENT_ID" --vcn-id "$VCN_ID" --display-name "$IGW_NAME" --all --query 'data[0].id' --raw-output 2>/dev/null || true)"
if nullish "$IGW_ID"; then
  IGW_ID="$(oci network internet-gateway create -c "$COMPARTMENT_ID" --vcn-id "$VCN_ID" --is-enabled true --display-name "$IGW_NAME" --wait-for-state AVAILABLE --query 'data.id' --raw-output)"
  log "Created Internet Gateway: $IGW_ID"
else
  log "Reusing Internet Gateway: $IGW_ID"
fi

ROUTE_RULES="$(python3 - "$IGW_ID" <<'PY'
import json,sys
print(json.dumps([{"cidrBlock":"0.0.0.0/0","networkEntityId":sys.argv[1]}], separators=(",",":")))
PY
)"
oci network route-table update --rt-id "$ROUTE_TABLE_ID" --route-rules "$ROUTE_RULES" --force >/dev/null

# Dedicated VCN: stateful SSH ingress only; all egress. Port 8080 stays private.
INGRESS_RULES='[{"source":"0.0.0.0/0","protocol":"6","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":22,"max":22}}}]'
EGRESS_RULES='[{"destination":"0.0.0.0/0","protocol":"all","isStateless":false}]'
oci network security-list update \
  --security-list-id "$SECURITY_LIST_ID" \
  --ingress-security-rules "$INGRESS_RULES" \
  --egress-security-rules "$EGRESS_RULES" \
  --force >/dev/null

SUBNET_ID="$(oci network subnet list -c "$COMPARTMENT_ID" --vcn-id "$VCN_ID" --display-name "$SUBNET_NAME" --all --query 'data[0].id' --raw-output 2>/dev/null || true)"
if nullish "$SUBNET_ID"; then
  SUBNET_ID="$(oci network subnet create -c "$COMPARTMENT_ID" --vcn-id "$VCN_ID" --cidr-block "$SUBNET_CIDR" --display-name "$SUBNET_NAME" --dns-label dds3 --route-table-id "$ROUTE_TABLE_ID" --security-list-ids "[\"$SECURITY_LIST_ID\"]" --prohibit-public-ip-on-vnic false --wait-for-state AVAILABLE --query 'data.id' --raw-output)"
  log "Created subnet: $SUBNET_ID"
else
  log "Reusing subnet: $SUBNET_ID"
fi

log "Prepare cloud-init: pinned project DDS3 runtime + canonical checks + benchmark"
USER_DATA="$WORKDIR/cloud-init-dds3.sh"
cat > "$USER_DATA" <<'CLOUD'
#!/usr/bin/env bash
set -Eeuo pipefail
mkdir -p /opt/bridge-school
exec > >(tee -a /var/log/dds3-bootstrap.log) 2>&1
trap 'rc=$?; trap - ERR; printf "FAILED rc=%s at %s\\n" "$rc" "$(date -u +%FT%TZ)" > /opt/bridge-school/DDS3_FAILED; exit "$rc"' ERR

REPO_URL="__REPO_URL__"
REPO_REF="__REPO_REF__"
REPO_DIR="/opt/bridge-school/bridge-video-free"
DEAL='N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3'

echo "=== Bridge School DDS3 bootstrap $(date -u +%FT%TZ) ==="
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl git docker.io jq openssl python3
systemctl enable --now docker

cat > /etc/ssh/sshd_config.d/99-bridge-school-dds3.conf <<'SSHCFG'
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
SSHCFG
systemctl reload ssh || systemctl reload sshd || true

rm -rf "$REPO_DIR"
git clone "$REPO_URL" "$REPO_DIR"
cd "$REPO_DIR"
git checkout --detach "$REPO_REF"
ACTUAL_REF="$(git rev-parse HEAD)"
[[ "$ACTUAL_REF" == "$REPO_REF" ]]

# Use the project's canonical pinned DDS3 core.
docker build --pull -f Dockerfile.dds3 -t bridge-school-dds3 .
docker run --rm bridge-school-dds3 N None "$DEAL" > /opt/bridge-school/golden-core.json
python3 - <<'PY'
import json
d=json.load(open('/opt/bridge-school/golden-core.json'))
assert d['hand_order']==['N','E','S','W']
assert d['strain_order']==['S','H','D','C','NT']
assert d['par_score_ns']==-110
assert d['par_contracts']==['2S-EW']
assert d['dd_table']=={'S':[5,8,5,8],'H':[6,6,6,6],'D':[5,7,5,7],'C':[7,5,7,5],'NT':[6,6,6,6]}
print('DDS3_CORE_GOLDEN_PASS')
PY

# Production HTTP runtime from the same verified core.
docker build --pull -f dds3_runtime/Dockerfile -t bridge-school-dds3-runtime .
umask 077
TOKEN="$(openssl rand -hex 32)"
printf 'DDS3_RUNTIME_TOKEN=%s\n' "$TOKEN" > /opt/bridge-school/dds3-runtime.env
chmod 600 /opt/bridge-school/dds3-runtime.env

docker rm -f bridge-school-dds3-runtime >/dev/null 2>&1 || true
docker run -d \
  --name bridge-school-dds3-runtime \
  --restart unless-stopped \
  --env-file /opt/bridge-school/dds3-runtime.env \
  -p 127.0.0.1:8080:8080 \
  bridge-school-dds3-runtime >/opt/bridge-school/runtime-container-id.txt

READY=0
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8080/readyz > /opt/bridge-school/runtime-ready.json; then READY=1; break; fi
  sleep 2
done
[[ "$READY" == 1 ]]

curl -fsS \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"operation":"dd_table","pbn":"N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3","dealer":"N","vulnerability":"None"}' \
  http://127.0.0.1:8080/v1/compute > /opt/bridge-school/runtime-result.json
python3 - <<'PY'
import json
r=json.load(open('/opt/bridge-school/runtime-result.json'))
assert r['engine']=='DDS3'
assert r['fallback_used'] is False
assert r['par_score_ns']==-110
assert r['dd_table']=={'S':[5,8,5,8],'H':[6,6,6,6],'D':[5,7,5,7],'C':[7,5,7,5],'NT':[6,6,6,6]}
print('DDS3_RUNTIME_HTTP_PASS')
PY

# Verify persistent SolverContext / transposition-table reuse.
BODY='{"operation":"position_all_moves","position":{"pbn":"N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3","trump":"NT","first":"N","current_trick":[]}}'
curl -fsS -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d "$BODY" http://127.0.0.1:8080/v1/compute > /opt/bridge-school/position1.json
curl -fsS -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d "$BODY" http://127.0.0.1:8080/v1/compute > /opt/bridge-school/position2.json
python3 - <<'PY'
import json
a=json.load(open('/opt/bridge-school/position1.json'))
b=json.load(open('/opt/bridge-school/position2.json'))
for r in (a,b):
    assert r['engine']=='DDS3' and r['fallback_used'] is False
    assert r['operation']=='position_all_moves'
    assert r['moves'] and r['optimal_cards']
assert a['moves']==b['moves']
assert a['solver_context']['request_seq']==1
assert b['solver_context']['request_seq']==2
assert b['solver_context']['tt_present_before'] is True
assert b['solver_context']['tt_present_after'] is True
assert b['solver_context']['same_tt_instance'] is True
assert a['nodes'] > b['nodes'] >= 0
print('DDS3_POSITION_TT_REUSE_PASS', a['nodes'], '->', b['nodes'])
PY

# Repeatable baseline benchmark: one warmup + 30 full DD tables.
docker run --rm bridge-school-dds3 N None "$DEAL" >/dev/null
BENCH_N=30
START_NS="$(date +%s%N)"
docker run --rm -e DEAL="$DEAL" -e BENCH_N="$BENCH_N" --entrypoint /bin/sh bridge-school-dds3 -c '
  i=0
  while [ "$i" -lt "$BENCH_N" ]; do
    /opt/bridge-school-dds3/dds_pbn_cli N None "$DEAL" >/dev/null
    i=$((i+1))
  done
'
END_NS="$(date +%s%N)"
python3 - "$START_NS" "$END_NS" "$BENCH_N" "$REPO_REF" <<'PY' > /opt/bridge-school/benchmark.json
import json, os, platform, sys
start=int(sys.argv[1]); end=int(sys.argv[2]); n=int(sys.argv[3]); ref=sys.argv[4]
seconds=(end-start)/1e9
model='unknown'
try:
    for line in open('/proc/cpuinfo', errors='ignore'):
        if line.lower().startswith('model name'):
            model=line.split(':',1)[1].strip(); break
except Exception:
    pass
out={
    'status':'PASS', 'engine':'DDS3', 'fallback_used':False,
    'repo_ref':ref, 'arch':platform.machine(), 'cpu_model':model,
    'logical_cpus':os.cpu_count(), 'tables':n,
    'seconds':round(seconds,6),
    'tables_per_second':round(n/seconds,6) if seconds else None,
}
print(json.dumps(out, indent=2, sort_keys=True))
PY

printf 'READY %s\n' "$(date -u +%FT%TZ)" > /opt/bridge-school/DDS3_READY
rm -f /opt/bridge-school/DDS3_FAILED
cat /opt/bridge-school/benchmark.json
echo "=== DDS3 bootstrap COMPLETE ==="
CLOUD
python3 - "$USER_DATA" "$REPO_URL" "$REPO_REF" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1])
s=p.read_text().replace('__REPO_URL__', sys.argv[2]).replace('__REPO_REF__', sys.argv[3])
p.write_text(s)
PY
chmod 600 "$USER_DATA"

find_existing_instance() {
  oci compute instance list -c "$COMPARTMENT_ID" --display-name "$INSTANCE_NAME" --all --output json 2>/dev/null | python3 -c '
import json,sys
items=json.load(sys.stdin).get("data",[])
items=[x for x in items if x.get("lifecycle-state") not in {"TERMINATED","TERMINATING"}]
items.sort(key=lambda x:x.get("time-created", ""), reverse=True)
print(items[0].get("id","") if items else "")
' || true
}

INSTANCE_ID="$(find_existing_instance)"
SELECTED_AD=""
SELECTED_SHAPE=""
SELECTED_OCPU=""
SELECTED_MEM=""
IMAGE_ID=""

if ! nullish "$INSTANCE_ID"; then
  log "Existing non-terminated instance found; no second VM will be created: $INSTANCE_ID"
  EXISTING="$(oci compute instance get --instance-id "$INSTANCE_ID" --output json)"
  SELECTED_AD="$(printf '%s' "$EXISTING" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"].get("availability-domain", ""))')"
  SELECTED_SHAPE="$(printf '%s' "$EXISTING" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"].get("shape", ""))')"
  SELECTED_OCPU="$(printf '%s' "$EXISTING" | python3 -c 'import json,sys; print((json.load(sys.stdin)["data"].get("shape-config") or {}).get("ocpus", ""))')"
  SELECTED_MEM="$(printf '%s' "$EXISTING" | python3 -c 'import json,sys; d=json.load(sys.stdin)["data"].get("shape-config") or {}; print(d.get("memory-in-gbs", d.get("memoryInGBs", "")))')"
else
  log "Discover Frankfurt availability domains"
  mapfile -t ADS < <(oci iam availability-domain list -c "$COMPARTMENT_ID" --output json | python3 -c 'import json,sys; [print(x["name"]) for x in json.load(sys.stdin).get("data",[])]')
  [[ "${#ADS[@]}" -gt 0 ]] || die "No availability domains returned for Frankfurt."
  printf 'ADs: %s\n' "${ADS[*]}"

  # Conservative initial size: 6 OCPU / 12 GB. At current reference prices
  # E5/E6 is about $146.88 for 720h, leaving a large buffer below $300.
  CANDIDATES=(
    'VM.Standard.E6.Flex|6|12'
    'VM.Standard.E5.Flex|6|12'
    'VM.Standard.E4.Flex|6|12'
    'VM.Standard.E6.Flex|4|8'
    'VM.Standard.E5.Flex|4|8'
    'VM.Standard.E4.Flex|4|8'
    'VM.Standard.E6.Flex|2|4'
    'VM.Standard.E5.Flex|2|4'
    'VM.Standard.E4.Flex|2|4'
  )

  find_image() {
    local shape="$1"
    local data
    data="$(oci compute image list -c "$COMPARTMENT_ID" --shape "$shape" --all --sort-by TIMECREATED --sort-order DESC --output json 2>/dev/null || true)"
    [[ -n "$data" ]] || return 1
    printf '%s' "$data" | python3 -c '
import json,re,sys
items=json.load(sys.stdin).get("data",[])
def score(x):
    n=x.get("display-name","")
    if re.match(r"^Canonical-Ubuntu-24\.04-\d", n): return 0
    if n.startswith("Canonical-Ubuntu-24.04-Minimal-") and "aarch64" not in n: return 1
    if re.match(r"^Canonical-Ubuntu-22\.04-\d", n): return 2
    if n.startswith("Canonical-Ubuntu-22.04-Minimal-") and "aarch64" not in n: return 3
    return 99
items=[x for x in items if score(x)<99]
if items:
    best=min(score(x) for x in items)
    best_items=[x for x in items if score(x)==best]
    best_items.sort(key=lambda x:x.get("time-created", ""), reverse=True)
    print(best_items[0]["id"])
' | head -n1
  }

  LAUNCH_ERR="$WORKDIR/last-launch-error.txt"
  : > "$LAUNCH_ERR"
  for candidate in "${CANDIDATES[@]}"; do
    IFS='|' read -r shape ocpu mem <<<"$candidate"
    for ad in "${ADS[@]}"; do
      INSTANCE_ID="$(find_existing_instance)"
      if ! nullish "$INSTANCE_ID"; then
        SELECTED_AD="$ad"; SELECTED_SHAPE="$shape"; SELECTED_OCPU="$ocpu"; SELECTED_MEM="$mem"
        break 2
      fi

      if ! oci compute shape list -c "$COMPARTMENT_ID" --availability-domain "$ad" --all --output json 2>/dev/null | \
          python3 -c 'import json,sys; s=sys.argv[1]; raise SystemExit(0 if any(x.get("shape")==s for x in json.load(sys.stdin).get("data",[])) else 1)' "$shape"; then
        log "Skip: $shape not listed in $ad"
        continue
      fi

      IMAGE_ID="$(find_image "$shape" || true)"
      if nullish "$IMAGE_ID"; then
        log "Skip: no compatible Ubuntu 24.04/22.04 image for $shape"
        continue
      fi

      log "Launch attempt: $shape, ${ocpu} OCPU, ${mem} GB, $ad"
      SHAPE_CONFIG="$(python3 - "$ocpu" "$mem" <<'PY'
import json,sys
print(json.dumps({'ocpus':float(sys.argv[1]), 'memoryInGBs':float(sys.argv[2])}, separators=(',',':')))
PY
)"
      TAGS='{"bridge-school":"dds3-oracle-bootstrap"}'
      set +e
      LAUNCH_JSON="$(oci compute instance launch \
        -c "$COMPARTMENT_ID" \
        --availability-domain "$ad" \
        --display-name "$INSTANCE_NAME" \
        --shape "$shape" \
        --shape-config "$SHAPE_CONFIG" \
        --image-id "$IMAGE_ID" \
        --subnet-id "$SUBNET_ID" \
        --assign-public-ip true \
        --ssh-authorized-keys-file "$SSH_KEY.pub" \
        --user-data-file "$USER_DATA" \
        --freeform-tags "$TAGS" \
        --wait-for-state RUNNING \
        --max-wait-seconds 1200 \
        --output json 2>"$LAUNCH_ERR")"
      RC=$?
      set -e
      if [[ "$RC" -eq 0 ]]; then
        INSTANCE_ID="$(printf '%s' "$LAUNCH_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["id"])')"
        SELECTED_AD="$ad"; SELECTED_SHAPE="$shape"; SELECTED_OCPU="$ocpu"; SELECTED_MEM="$mem"
        break 2
      fi

      warn "Launch rejected for $shape ${ocpu}/${mem} in $ad. Trying next safe candidate."
      tail -n 5 "$LAUNCH_ERR" >&2 || true
      sleep 4
    done
  done

  if nullish "$INSTANCE_ID"; then
    die "No E6/E5/E4 x86 candidate could be launched. The script did NOT fall back to ARM and did NOT Upgrade the account. Last OCI error is in $LAUNCH_ERR"
  fi
fi

log "Instance created/reused"
INSTANCE_JSON="$(oci compute instance get --instance-id "$INSTANCE_ID" --output json)"
STATE="$(printf '%s' "$INSTANCE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"].get("lifecycle-state", ""))')"
SELECTED_AD="${SELECTED_AD:-$(printf '%s' "$INSTANCE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"].get("availability-domain", ""))')}"
SELECTED_SHAPE="${SELECTED_SHAPE:-$(printf '%s' "$INSTANCE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"].get("shape", ""))')}"
if nullish "$SELECTED_OCPU"; then SELECTED_OCPU="$(printf '%s' "$INSTANCE_JSON" | python3 -c 'import json,sys; print((json.load(sys.stdin)["data"].get("shape-config") or {}).get("ocpus", ""))')"; fi
if nullish "$SELECTED_MEM"; then SELECTED_MEM="$(printf '%s' "$INSTANCE_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin)["data"].get("shape-config") or {}; print(d.get("memory-in-gbs", d.get("memoryInGBs", "")))')"; fi

PUBLIC_IP=""
for _ in $(seq 1 30); do
  PUBLIC_IP="$(oci compute instance list-vnics --instance-id "$INSTANCE_ID" --all --query 'data[0]."public-ip"' --raw-output 2>/dev/null || true)"
  nullish "$PUBLIC_IP" || break
  sleep 5
done

MONTHLY_ESTIMATE="unknown"
HOURLY_ESTIMATE="unknown"
if [[ "$SELECTED_SHAPE" == "VM.Standard.E5.Flex" || "$SELECTED_SHAPE" == "VM.Standard.E6.Flex" ]] && [[ "$SELECTED_OCPU" =~ ^[0-9.]+$ ]] && [[ "$SELECTED_MEM" =~ ^[0-9.]+$ ]]; then
  read -r HOURLY_ESTIMATE MONTHLY_ESTIMATE < <(python3 - "$SELECTED_OCPU" "$SELECTED_MEM" <<'PY'
import sys
oc=float(sys.argv[1]); mem=float(sys.argv[2]); h=oc*0.03+mem*0.002
print(f"{h:.4f}", f"{h*720:.2f}")
PY
)
elif [[ "$SELECTED_SHAPE" == "VM.Standard.E4.Flex" ]] && [[ "$SELECTED_OCPU" =~ ^[0-9.]+$ ]] && [[ "$SELECTED_MEM" =~ ^[0-9.]+$ ]]; then
  read -r HOURLY_ESTIMATE MONTHLY_ESTIMATE < <(python3 - "$SELECTED_OCPU" "$SELECTED_MEM" <<'PY'
import sys
oc=float(sys.argv[1]); mem=float(sys.argv[2]); h=oc*0.025+mem*0.0015
print(f"{h:.4f}", f"{h*720:.2f}")
PY
)
fi

printf '\n========== DDS3 OCI RESULT ==========\n'
printf 'Instance:        %s\n' "$INSTANCE_NAME"
printf 'Instance OCID:   %s\n' "$INSTANCE_ID"
printf 'State:           %s\n' "$STATE"
printf 'Region:          %s\n' "$REGION"
printf 'AD:              %s\n' "$SELECTED_AD"
printf 'Shape:           %s\n' "$SELECTED_SHAPE"
printf 'OCPU / RAM:      %s / %s GB\n' "$SELECTED_OCPU" "$SELECTED_MEM"
printf 'Public IP:       %s\n' "${PUBLIC_IP:-pending}"
printf 'Reference cost:  $%s/hour; $%s per 720h (compute only)\n' "$HOURLY_ESTIMATE" "$MONTHLY_ESTIMATE"
printf 'Repo ref:        %s\n' "$REPO_REF"
printf 'Public ports:    SSH 22 only; DDS3 8080 is localhost-only\n'
printf 'Upgrade action:  NOT performed\n'
printf '=====================================\n\n'

if nullish "$PUBLIC_IP"; then
  warn "Public IP is not visible yet. Re-run this script later; it will reuse the same VM and will not create a second one."
  exit 0
fi

SSH_BASE=(ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=7 -o StrictHostKeyChecking=accept-new "ubuntu@$PUBLIC_IP")
log "Try to observe cloud-init over SSH (non-fatal if Cloud Shell public networking is unavailable)"
SSH_OK=0
for _ in $(seq 1 36); do
  if "${SSH_BASE[@]}" 'echo SSH_OK' >/dev/null 2>&1; then SSH_OK=1; break; fi
  sleep 5
done

if [[ "$SSH_OK" == 1 ]]; then
  log "SSH reachable. Waiting up to 30 minutes for DDS3 build/test/benchmark."
  READY=0
  for _ in $(seq 1 180); do
    if "${SSH_BASE[@]}" 'test -f /opt/bridge-school/DDS3_READY'; then READY=1; break; fi
    if "${SSH_BASE[@]}" 'test -f /opt/bridge-school/DDS3_FAILED'; then
      warn "VM bootstrap reported failure. Last log lines:"
      "${SSH_BASE[@]}" 'sudo tail -n 120 /var/log/dds3-bootstrap.log' || true
      exit 2
    fi
    sleep 10
  done
  if [[ "$READY" == 1 ]]; then
    log "DDS3 READY. Benchmark:"
    "${SSH_BASE[@]}" 'sudo cat /opt/bridge-school/benchmark.json'
  else
    warn "Timed out waiting for DDS3_READY. VM remains running; inspect the log with the command below."
  fi
else
  warn "Cloud Shell could not SSH to the public IP. This does not stop VM cloud-init. Use the SSH command below from your own computer."
fi

cat <<EOF

DIRECT COMMANDS
---------------
SSH:
  ssh -i $SSH_KEY ubuntu@$PUBLIC_IP

Check bootstrap:
  ssh -i $SSH_KEY ubuntu@$PUBLIC_IP 'sudo cloud-init status --wait; sudo tail -n 120 /var/log/dds3-bootstrap.log'

Benchmark:
  ssh -i $SSH_KEY ubuntu@$PUBLIC_IP 'sudo cat /opt/bridge-school/benchmark.json'

DDS3 token (keep private):
  ssh -i $SSH_KEY ubuntu@$PUBLIC_IP 'sudo cat /opt/bridge-school/dds3-runtime.env'

Secure local tunnel to DDS3 runtime:
  ssh -i $SSH_KEY -L 18080:127.0.0.1:8080 ubuntu@$PUBLIC_IP
Then use: http://127.0.0.1:18080/readyz

Oracle Cost Analysis (direct):
  https://cloud.oracle.com/account-management/cost-analysis?region=eu-frankfurt-1

Terminate the VM when finished (direct instances page):
  https://cloud.oracle.com/compute/instances?region=eu-frankfurt-1
EOF
