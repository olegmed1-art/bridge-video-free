#!/usr/bin/env bash
set -Eeuo pipefail

REGION="${REGION:-eu-frankfurt-1}"
INSTANCE_NAME="${INSTANCE_NAME:-bridge-school-dds3-frankfurt}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/bridge_school_dds3_oracle}"
REPO_URL="https://github.com/olegmed1-art/bridge-video-free.git"
REPO_REF="e84f7591a5b301c3ceb0339b2d09c60caf85732f"
export OCI_CLI_REGION="$REGION"

log(){ printf '\n[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }
nullish(){ [[ -z "${1:-}" || "${1:-}" == "null" || "${1:-}" == "None" ]]; }

command -v oci >/dev/null || die "OCI CLI not found; run in Oracle Cloud Shell."
command -v ssh >/dev/null || die "ssh not found."
[[ -f "$SSH_KEY" ]] || die "Dedicated SSH key not found at $SSH_KEY. The original bootstrap must have created it."
chmod 600 "$SSH_KEY"

ROOT_JSON="$(oci iam compartment list --include-root --all --output json)"
TENANCY_ID="$(printf '%s' "$ROOT_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin).get("data",[]); print(next((x.get("id","") for x in d if str(x.get("id","")).startswith("ocid1.tenancy.")), ""))')"
nullish "$TENANCY_ID" && die "Could not determine tenancy."
COMPARTMENT_ID="${COMPARTMENT_ID:-$TENANCY_ID}"

log "Locate existing DDS3 VM in Frankfurt"
INSTANCES="$(oci compute instance list -c "$COMPARTMENT_ID" --display-name "$INSTANCE_NAME" --all --output json)"
INSTANCE_ID="$(printf '%s' "$INSTANCES" | python3 -c '
import json,sys
xs=json.load(sys.stdin).get("data",[])
xs=[x for x in xs if x.get("lifecycle-state") not in {"TERMINATED","TERMINATING"}]
xs.sort(key=lambda x:x.get("time-created", ""), reverse=True)
print(xs[0].get("id","") if xs else "")
')"
nullish "$INSTANCE_ID" && die "No existing $INSTANCE_NAME instance found. Do not create a second VM from this repair script."

INSTANCE_JSON="$(oci compute instance get --instance-id "$INSTANCE_ID" --output json)"
STATE="$(printf '%s' "$INSTANCE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"].get("lifecycle-state", ""))')"
SHAPE="$(printf '%s' "$INSTANCE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"].get("shape", ""))')"
OCPU="$(printf '%s' "$INSTANCE_JSON" | python3 -c 'import json,sys; print((json.load(sys.stdin)["data"].get("shape-config") or {}).get("ocpus", ""))')"
MEM="$(printf '%s' "$INSTANCE_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin)["data"].get("shape-config") or {}; print(d.get("memory-in-gbs", d.get("memoryInGBs", "")))')"
AD="$(printf '%s' "$INSTANCE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"].get("availability-domain", ""))')"
[[ "$STATE" == "RUNNING" ]] || die "Instance state is $STATE, expected RUNNING."

PUBLIC_IP="$(oci compute instance list-vnics --instance-id "$INSTANCE_ID" --all --query 'data[0]."public-ip"' --raw-output 2>/dev/null || true)"
nullish "$PUBLIC_IP" && die "Instance has no public IP."

printf 'Instance: %s\nShape: %s\nOCPU/RAM: %s / %s GB\nAD: %s\nPublic IP: %s\n' "$INSTANCE_ID" "$SHAPE" "$OCPU" "$MEM" "$AD" "$PUBLIC_IP"

SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new "ubuntu@$PUBLIC_IP")
log "Wait for SSH"
for _ in $(seq 1 36); do
  if "${SSH[@]}" 'echo SSH_OK' >/dev/null 2>&1; then break; fi
  sleep 5
done
"${SSH[@]}" 'echo SSH_OK' >/dev/null || die "SSH is not reachable."

log "Repair runtime build and continue canonical DDS3 validation"
"${SSH[@]}" "sudo REPO_URL='$REPO_URL' REPO_REF='$REPO_REF' bash -s" <<'REMOTE'
set -Eeuo pipefail
mkdir -p /opt/bridge-school
exec > >(tee -a /var/log/dds3-repair.log) 2>&1
trap 'rc=$?; trap - ERR; printf "FAILED rc=%s at %s\n" "$rc" "$(date -u +%FT%TZ)" > /opt/bridge-school/DDS3_FAILED; exit "$rc"' ERR

REPO_DIR=/opt/bridge-school/bridge-video-free
DEAL='N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3'
export DEBIAN_FRONTEND=noninteractive

apt-get update -qq
apt-get install -y --no-install-recommends ca-certificates curl git docker.io jq openssl python3 >/dev/null
systemctl enable --now docker >/dev/null

if [[ ! -d "$REPO_DIR/.git" ]]; then
  rm -rf "$REPO_DIR"
  git clone "$REPO_URL" "$REPO_DIR"
fi
cd "$REPO_DIR"
git fetch --quiet origin "$REPO_REF" || true
git checkout --detach "$REPO_REF"
[[ "$(git rev-parse HEAD)" == "$REPO_REF" ]]

# Ensure canonical core exists and passes the repository golden result.
if ! docker image inspect bridge-school-dds3:latest >/dev/null 2>&1; then
  docker build -f Dockerfile.dds3 -t bridge-school-dds3 .
fi
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

# IMPORTANT FIX: no --pull here. The first stage intentionally references
# the locally-built bridge-school-dds3 image.
docker build -f dds3_runtime/Dockerfile -t bridge-school-dds3-runtime .

umask 077
if [[ -f /opt/bridge-school/dds3-runtime.env ]]; then
  # Reuse existing secret if one was created before a partial failure.
  set -a
  . /opt/bridge-school/dds3-runtime.env
  set +a
else
  DDS3_RUNTIME_TOKEN="$(openssl rand -hex 32)"
  printf 'DDS3_RUNTIME_TOKEN=%s\n' "$DDS3_RUNTIME_TOKEN" > /opt/bridge-school/dds3-runtime.env
  chmod 600 /opt/bridge-school/dds3-runtime.env
fi
: "${DDS3_RUNTIME_TOKEN:?runtime token missing}"

docker rm -f bridge-school-dds3-runtime >/dev/null 2>&1 || true
docker run -d --name bridge-school-dds3-runtime --restart unless-stopped \
  --env-file /opt/bridge-school/dds3-runtime.env \
  -p 127.0.0.1:8080:8080 \
  bridge-school-dds3-runtime >/opt/bridge-school/runtime-container-id.txt

READY=0
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8080/readyz > /opt/bridge-school/runtime-ready.json; then READY=1; break; fi
  sleep 2
done
if [[ "$READY" != 1 ]]; then
  docker logs bridge-school-dds3-runtime || true
  exit 1
fi
cat /opt/bridge-school/runtime-ready.json

curl -fsS -H "Authorization: Bearer $DDS3_RUNTIME_TOKEN" -H 'Content-Type: application/json' \
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

BODY='{"operation":"position_all_moves","position":{"pbn":"N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3","trump":"NT","first":"N","current_trick":[]}}'
curl -fsS -H "Authorization: Bearer $DDS3_RUNTIME_TOKEN" -H 'Content-Type: application/json' -d "$BODY" http://127.0.0.1:8080/v1/compute > /opt/bridge-school/position1.json
curl -fsS -H "Authorization: Bearer $DDS3_RUNTIME_TOKEN" -H 'Content-Type: application/json' -d "$BODY" http://127.0.0.1:8080/v1/compute > /opt/bridge-school/position2.json
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

# Baseline benchmark on the actual Oracle CPU.
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
print(json.dumps({
    'status':'PASS','engine':'DDS3','fallback_used':False,'repo_ref':ref,
    'arch':platform.machine(),'cpu_model':model,'logical_cpus':os.cpu_count(),
    'tables':n,'seconds':round(seconds,6),
    'tables_per_second':round(n/seconds,6) if seconds else None,
}, indent=2, sort_keys=True))
PY

printf 'READY %s\n' "$(date -u +%FT%TZ)" > /opt/bridge-school/DDS3_READY
rm -f /opt/bridge-school/DDS3_FAILED

echo '=== DDS3 REPAIR COMPLETE ==='
cat /opt/bridge-school/benchmark.json
REMOTE

log "Final status"
"${SSH[@]}" 'sudo test -f /opt/bridge-school/DDS3_READY && echo DDS3_READY; sudo cat /opt/bridge-school/benchmark.json; sudo docker ps --filter name=bridge-school-dds3-runtime --format "{{.Names}} {{.Status}} {{.Ports}}"'

printf '\nRESULT\n------\nShape: %s\nOCPU/RAM: %s / %s GB\nAD: %s\nPublic IP: %s\n' "$SHAPE" "$OCPU" "$MEM" "$AD" "$PUBLIC_IP"
printf 'DDS3 API: localhost-only on VM:8080; access via SSH tunnel.\n'
printf 'Account Upgrade: NOT performed.\n'
