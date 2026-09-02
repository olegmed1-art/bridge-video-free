#!/usr/bin/env bash
set -Eeuo pipefail

# Targeted, rollback-safe rebuild of the existing Frankfurt DDS3 runtime after
# the Vercel JWKS verifier fix. No VM creation, no account Upgrade, no billing changes.

REGION="${REGION:-eu-frankfurt-1}"
INSTANCE_NAME="${INSTANCE_NAME:-bridge-school-dds3-frankfurt}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/bridge_school_dds3_oracle}"
TARGET_SHA="${TARGET_SHA:-}"
REPO_URL="https://github.com/olegmed1-art/bridge-video-free.git"

log(){ printf '\n[%s] %s\n' "$(date -u +'%FT%TZ')" "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }
nullish(){ [[ -z "${1:-}" || "${1:-}" == "null" || "${1:-}" == "None" ]]; }

[[ "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]] || die "TARGET_SHA must be an exact 40-character Git commit SHA"
command -v oci >/dev/null 2>&1 || die "OCI CLI not found; run this in Oracle Cloud Shell"
command -v python3 >/dev/null 2>&1 || die "python3 not found"
command -v ssh >/dev/null 2>&1 || die "ssh not found"
[[ -f "$SSH_KEY" ]] || die "Dedicated SSH key not found at $SSH_KEY"
chmod 600 "$SSH_KEY"
export OCI_CLI_REGION="$REGION"

log "Resolve the one existing Frankfurt DDS3 VM"
ROOT_JSON="$(oci iam compartment list --include-root --all --output json)"
TENANCY_ID="$(printf '%s' "$ROOT_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin).get("data",[]); print(next((x.get("id","") for x in d if str(x.get("id","")).startswith("ocid1.tenancy.")), ""))')"
nullish "$TENANCY_ID" && die "Could not determine tenancy OCID"
COMPARTMENT_ID="${COMPARTMENT_ID:-$TENANCY_ID}"
INSTANCES="$(oci compute instance list -c "$COMPARTMENT_ID" --display-name "$INSTANCE_NAME" --all --output json)"
INSTANCE_ID="$(printf '%s' "$INSTANCES" | python3 -c 'import json,sys; xs=json.load(sys.stdin).get("data",[]); xs=[x for x in xs if x.get("lifecycle-state") not in {"TERMINATED","TERMINATING"}]; xs.sort(key=lambda x:x.get("time-created", ""), reverse=True); print(xs[0].get("id","") if xs else "")')"
nullish "$INSTANCE_ID" && die "Existing $INSTANCE_NAME VM not found; this repair will not create one"
STATE="$(oci compute instance get --instance-id "$INSTANCE_ID" --query 'data."lifecycle-state"' --raw-output)"
[[ "$STATE" == "RUNNING" ]] || die "Existing VM is $STATE, expected RUNNING"
VNIC_JSON="$(oci compute instance list-vnics --instance-id "$INSTANCE_ID" --all --output json)"
PUBLIC_IP="$(printf '%s' "$VNIC_JSON" | python3 -c 'import json,sys; xs=json.load(sys.stdin).get("data",[]); print((xs[0].get("public-ip") if xs else "") or "")')"
nullish "$PUBLIC_IP" && die "Existing VM has no public IP"
printf 'Existing VM: %s\nPublic IP: %s\nTarget SHA: %s\n' "$INSTANCE_ID" "$PUBLIC_IP" "$TARGET_SHA"

SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new "ubuntu@$PUBLIC_IP")
"${SSH[@]}" 'echo SSH_OK' >/dev/null || die "SSH to existing VM is unavailable"

log "Build and canary-test the fixed runtime before replacing production"
"${SSH[@]}" "sudo TARGET_SHA='$TARGET_SHA' PUBLIC_IP='$PUBLIC_IP' REPO_URL='$REPO_URL' bash -s" <<'REMOTE'
set -Eeuo pipefail
set +x

REPO_DIR=/opt/bridge-school/bridge-video-free
ENV_FILE=/opt/bridge-school/dds3-runtime.env
CANARY=bridge-school-dds3-jwks-canary
NEW_IMAGE="bridge-school-dds3-runtime:jwks-${TARGET_SHA:0:12}"
ROLLBACK_IMAGE=bridge-school-dds3-runtime:pre-jwks-fix
DEAL='N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3'

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ -d "$REPO_DIR/.git" ]] || fail "project checkout missing on VM"
[[ -f "$ENV_FILE" ]] || fail "DDS3 runtime env file missing"
chmod 600 "$ENV_FILE"

cd "$REPO_DIR"
git fetch --quiet origin main
git cat-file -e "$TARGET_SHA^{commit}" 2>/dev/null || git fetch --quiet origin "$TARGET_SHA"
git checkout --quiet --detach "$TARGET_SHA"
[[ "$(git rev-parse HEAD)" == "$TARGET_SHA" ]] || fail "could not pin target commit"

python3 - <<'PY'
from pathlib import Path
p=Path('dds3_runtime/auth.py').read_text()
assert '"team", "global", "auto"' in p
assert 'jwks_url_for_issuer' in p
print('JWKS_SOURCE_CONTRACT_PASS')
PY

CANARY_ENV=/tmp/dds3-runtime-oidc-auto.env
cp -a "$ENV_FILE" "$CANARY_ENV"
if grep -q '^DDS3_VERCEL_ISSUER_MODE=' "$CANARY_ENV"; then
  sed -i 's/^DDS3_VERCEL_ISSUER_MODE=.*/DDS3_VERCEL_ISSUER_MODE=auto/' "$CANARY_ENV"
else
  printf '\nDDS3_VERCEL_ISSUER_MODE=auto\n' >>"$CANARY_ENV"
fi

for expected in \
  'DDS3_TRUST_VERCEL_OIDC=true' \
  'DDS3_VERCEL_ISSUER_MODE=auto' \
  'DDS3_VERCEL_TEAM_SLUG=olegmed1-4368s-projects' \
  'DDS3_VERCEL_PROJECT_NAME=bridge-video-free' \
  'DDS3_VERCEL_TEAM_ID=team_qXr2smag8blW1WWeS10CDRXb' \
  'DDS3_VERCEL_PROJECT_ID=prj_oF4SA0gA1PX6BuJEmJ1BiHVBXUGP' \
  'DDS3_VERCEL_ENVIRONMENT=production'; do
  grep -Fqx "$expected" "$CANARY_ENV" || fail "strict OIDC env mismatch: ${expected%%=*}"
done
STATIC_TOKEN="$(sed -n 's/^DDS3_RUNTIME_TOKEN=//p' "$ENV_FILE" | head -n1)"
[[ -n "$STATIC_TOKEN" ]] || fail "static local/admin runtime token missing"

if ! docker image inspect bridge-school-dds3 >/dev/null 2>&1; then
  printf 'Pinned DDS3 core image missing; rebuilding it.\n'
  docker build -f Dockerfile.dds3 -t bridge-school-dds3 .
fi

docker build -f dds3_runtime/Dockerfile -t "$NEW_IMAGE" .
docker run --rm -i --entrypoint python --env-file "$CANARY_ENV" "$NEW_IMAGE" - <<'PY'
from dds3_runtime.auth import _oidc_config
c=_oidc_config()
assert c.enabled is True
assert c.issuer_mode == 'auto'
assert c.allowed_issuers == (
    'https://oidc.vercel.com/olegmed1-4368s-projects',
    'https://oidc.vercel.com',
)
assert c.jwks_url_for_issuer(c.allowed_issuers[0]) == 'https://oidc.vercel.com/olegmed1-4368s-projects/.well-known/jwks'
assert c.jwks_url_for_issuer(c.allowed_issuers[1]) == 'https://oidc.vercel.com/.well-known/jwks'
assert c.audience == 'https://vercel.com/olegmed1-4368s-projects'
assert c.subject == 'owner:olegmed1-4368s-projects:project:bridge-video-free:environment:production'
assert c.team_id == 'team_qXr2smag8blW1WWeS10CDRXb'
assert c.project_id == 'prj_oF4SA0gA1PX6BuJEmJ1BiHVBXUGP'
assert c.environment == 'production'
print('JWKS_RUNTIME_CONFIG_PASS')
PY

docker rm -f "$CANARY" >/dev/null 2>&1 || true
docker run -d --rm \
  --name "$CANARY" \
  --security-opt no-new-privileges:true \
  --log-opt max-size=10m --log-opt max-file=2 \
  --env-file "$CANARY_ENV" \
  -p 127.0.0.1:18081:8080 \
  "$NEW_IMAGE" >/dev/null
cleanup_canary(){ docker rm -f "$CANARY" >/dev/null 2>&1 || true; }
trap cleanup_canary EXIT
for _ in $(seq 1 45); do
  if curl -fsS --max-time 5 http://127.0.0.1:18081/readyz >/tmp/dds3-jwks-canary-ready.json; then break; fi
  sleep 2
done
curl -fsS --max-time 5 http://127.0.0.1:18081/readyz >/tmp/dds3-jwks-canary-ready.json || fail "canary readiness failed"

curl -fsS --max-time 30 \
  -H "Authorization: Bearer $STATIC_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"operation\":\"dd_table\",\"pbn\":\"$DEAL\",\"dealer\":\"N\",\"vulnerability\":\"None\"}" \
  http://127.0.0.1:18081/v1/compute >/tmp/dds3-jwks-canary-golden.json
python3 - <<'PY'
import json
r=json.load(open('/tmp/dds3-jwks-canary-golden.json'))
assert r['engine']=='DDS3' and r['fallback_used'] is False
assert r['par_score_ns']==-110 and r['par_contracts']==['2S-EW']
assert r['dd_table']=={'S':[5,8,5,8],'H':[6,6,6,6],'D':[5,7,5,7],'C':[7,5,7,5],'NT':[6,6,6,6]}
print('JWKS_CANARY_GOLDEN_PASS')
PY
cleanup_canary
trap - EXIT

OLD_IMAGE_ID="$(docker inspect -f '{{.Image}}' bridge-school-dds3-runtime 2>/dev/null || true)"
[[ -n "$OLD_IMAGE_ID" ]] || fail "existing production DDS3 container missing"
docker tag "$OLD_IMAGE_ID" "$ROLLBACK_IMAGE"

ENV_BACKUP="${ENV_FILE}.pre-oidc-auto"
cp -a "$ENV_FILE" "$ENV_BACKUP"
cp -a "$CANARY_ENV" "$ENV_FILE"
chmod 600 "$ENV_FILE"
ENV_COMMITTED=0
restore_env_on_exit(){
  if [[ "$ENV_COMMITTED" != 1 && -f "$ENV_BACKUP" ]]; then
    cp -a "$ENV_BACKUP" "$ENV_FILE"
  fi
}
trap restore_env_on_exit EXIT

start_runtime(){
  local image="$1"
  docker rm -f bridge-school-dds3-runtime >/dev/null 2>&1 || true
  docker run -d \
    --name bridge-school-dds3-runtime \
    --restart unless-stopped \
    --security-opt no-new-privileges:true \
    --log-opt max-size=10m --log-opt max-file=5 \
    --env-file "$ENV_FILE" \
    -p 127.0.0.1:8080:8080 \
    "$image" >/dev/null
}

rollback(){
  printf 'New runtime failed; rolling back previous image.\n' >&2
  cp -a "$ENV_BACKUP" "$ENV_FILE"
  start_runtime "$ROLLBACK_IMAGE"
  for _ in $(seq 1 30); do
    if curl -fsS --max-time 5 http://127.0.0.1:8080/readyz >/dev/null 2>&1; then
      printf 'ROLLBACK_READY_PASS\n' >&2
      return 0
    fi
    sleep 2
  done
  return 1
}

start_runtime "$NEW_IMAGE"
READY=0
for _ in $(seq 1 45); do
  if curl -fsS --max-time 5 http://127.0.0.1:8080/readyz >/tmp/dds3-jwks-prod-ready.json; then READY=1; break; fi
  sleep 2
done
if [[ "$READY" != 1 ]]; then
  docker logs --tail 100 bridge-school-dds3-runtime >&2 || true
  rollback || fail "new runtime failed and rollback readiness also failed"
  fail "new production runtime readiness failed; rollback completed"
fi

POST_OK=1
if ! curl -fsS --max-time 30 \
  -H "Authorization: Bearer $STATIC_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"operation\":\"dd_table\",\"pbn\":\"$DEAL\",\"dealer\":\"N\",\"vulnerability\":\"None\"}" \
  http://127.0.0.1:8080/v1/compute >/tmp/dds3-jwks-prod-golden.json; then
  POST_OK=0
fi
if [[ "$POST_OK" == 1 ]] && ! python3 -c "import json; r=json.load(open('/tmp/dds3-jwks-prod-golden.json')); assert r['engine']=='DDS3' and r['fallback_used'] is False; assert r['par_score_ns']==-110; assert r['dd_table']=={'S':[5,8,5,8],'H':[6,6,6,6],'D':[5,7,5,7],'C':[7,5,7,5],'NT':[6,6,6,6]}"; then
  POST_OK=0
fi
if [[ "$POST_OK" != 1 ]]; then
  rollback || fail "golden validation failed and rollback readiness also failed"
  fail "new production runtime golden validation failed; rollback completed"
fi
printf 'JWKS_PRODUCTION_LOCAL_GOLDEN_PASS\n'

PUBLIC_OK=1
if ! curl -fsS --max-time 12 "https://$PUBLIC_IP/readyz" >/tmp/dds3-jwks-public-ready.json; then
  PUBLIC_OK=0
fi
if [[ "$PUBLIC_OK" == 1 ]] && ! python3 -c "import json; r=json.load(open('/tmp/dds3-jwks-public-ready.json')); assert r['status']=='ready' and r['engine']=='DDS3' and r['fallback_used'] is False"; then
  PUBLIC_OK=0
fi
if [[ "$PUBLIC_OK" != 1 ]]; then
  rollback || fail "public readiness failed and rollback readiness also failed"
  fail "new production runtime public readiness failed; rollback completed"
fi
printf 'JWKS_PUBLIC_READINESS_PASS\n'

ENV_COMMITTED=1
trap - EXIT
printf '%s\n' "$TARGET_SHA" >/opt/bridge-school/dds3-runtime-git-sha
printf 'OIDC_JWKS_RUNTIME_REPAIR_PASS target_sha=%s\n' "$TARGET_SHA"
REMOTE

log "Verify public HTTPS from Oracle Cloud Shell"
curl -fsS --max-time 20 "https://$PUBLIC_IP/readyz" | python3 -c 'import json,sys; r=json.load(sys.stdin); assert r.get("status")=="ready" and r.get("engine")=="DDS3" and r.get("fallback_used") is False, r; print("CLOUD_SHELL_DDS3_HTTPS_PASS")'

printf '\nOIDC_JWKS_REPAIR_COMPLETE\n'
printf 'Existing VM only: YES\nAccount Upgrade: NOT PERFORMED\nSecond VM: NOT CREATED\nTarget SHA: %s\n' "$TARGET_SHA"
