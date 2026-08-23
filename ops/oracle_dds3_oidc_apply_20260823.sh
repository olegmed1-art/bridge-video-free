#!/usr/bin/env bash
set -Eeuo pipefail
set +x

TARGET_SHA="740019f54a42e2dabe4dd0ebd7010dee9c8c3306"
REPO_DIR="/opt/bridge-school/bridge-video-free"
ENV_FILE="/opt/bridge-school/dds3-runtime.env"
PROD_CONTAINER="bridge-school-dds3-runtime"
CANARY_CONTAINER="bridge-school-dds3-oidc-canary"
NEW_IMAGE="bridge-school-dds3-runtime:oidc-auto-${TARGET_SHA:0:12}"
ROLLBACK_IMAGE="bridge-school-dds3-runtime:pre-oidc-auto-20260823"
CANARY_ENV="/tmp/dds3-runtime-oidc-auto.env"
DEAL='N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3'

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
cleanup(){ docker rm -f "$CANARY_CONTAINER" >/dev/null 2>&1 || true; rm -f "$CANARY_ENV"; }
trap cleanup EXIT

[[ "$(id -u)" == 0 ]] || fail "Run Command must execute as root"
[[ -d "$REPO_DIR/.git" ]] || fail "project checkout missing"
[[ -f "$ENV_FILE" ]] || fail "runtime environment missing"
command -v docker >/dev/null || fail "docker missing"

cd "$REPO_DIR"
git fetch --quiet origin main
git cat-file -e "$TARGET_SHA^{commit}" 2>/dev/null || git fetch --quiet origin "$TARGET_SHA"
git checkout --quiet --detach "$TARGET_SHA"
[[ "$(git rev-parse HEAD)" == "$TARGET_SHA" ]] || fail "target commit not checked out"
grep -Fq '"team", "global", "auto"' dds3_runtime/auth.py || fail "OIDC auto source contract missing"

cp -a "$ENV_FILE" "$CANARY_ENV"
if grep -q '^DDS3_VERCEL_ISSUER_MODE=' "$CANARY_ENV"; then
  sed -i 's/^DDS3_VERCEL_ISSUER_MODE=.*/DDS3_VERCEL_ISSUER_MODE=auto/' "$CANARY_ENV"
else
  printf '\nDDS3_VERCEL_ISSUER_MODE=auto\n' >>"$CANARY_ENV"
fi
chmod 600 "$CANARY_ENV"

for expected in \
  'DDS3_TRUST_VERCEL_OIDC=true' \
  'DDS3_VERCEL_ISSUER_MODE=auto' \
  'DDS3_VERCEL_TEAM_SLUG=olegmed1-4368s-projects' \
  'DDS3_VERCEL_PROJECT_NAME=bridge-video-free' \
  'DDS3_VERCEL_TEAM_ID=team_qXr2smag8blW1WWeS10CDRXb' \
  'DDS3_VERCEL_PROJECT_ID=prj_oF4SA0gA1PX6BuJEmJ1BiHVBXUGP' \
  'DDS3_VERCEL_ENVIRONMENT=production'; do
  grep -Fqx "$expected" "$CANARY_ENV" || fail "runtime setting mismatch: ${expected%%=*}"
done

STATIC_TOKEN="$(sed -n 's/^DDS3_RUNTIME_TOKEN=//p' "$ENV_FILE" | head -n1)"
[[ -n "$STATIC_TOKEN" ]] || fail "local runtime token missing"
docker image inspect bridge-school-dds3 >/dev/null 2>&1 || docker build -f Dockerfile.dds3 -t bridge-school-dds3 .
docker build -f dds3_runtime/Dockerfile -t "$NEW_IMAGE" .

docker rm -f "$CANARY_CONTAINER" >/dev/null 2>&1 || true
docker run -d --rm --name "$CANARY_CONTAINER" \
  --security-opt no-new-privileges:true --env-file "$CANARY_ENV" \
  -p 127.0.0.1:18081:8080 "$NEW_IMAGE" >/dev/null
for _ in $(seq 1 60); do
  curl -fsS --max-time 5 http://127.0.0.1:18081/readyz >/tmp/dds3-oidc-canary-ready.json 2>/dev/null && break
  sleep 2
done
curl -fsS --max-time 5 http://127.0.0.1:18081/readyz >/tmp/dds3-oidc-canary-ready.json || fail "canary readiness failed"
curl -fsS --max-time 30 -H "Authorization: Bearer $STATIC_TOKEN" -H 'Content-Type: application/json' \
  -d "{\"operation\":\"dd_table\",\"pbn\":\"$DEAL\",\"dealer\":\"N\",\"vulnerability\":\"None\"}" \
  http://127.0.0.1:18081/v1/compute >/tmp/dds3-oidc-canary-golden.json
python3 - <<'PY'
import json
r=json.load(open('/tmp/dds3-oidc-canary-golden.json'))
assert r['engine']=='DDS3' and r['fallback_used'] is False
assert r['par_score_ns']==-110 and r['par_contracts']==['2S-EW']
assert r['dd_table']=={'S':[5,8,5,8],'H':[6,6,6,6],'D':[5,7,5,7],'C':[7,5,7,5],'NT':[6,6,6,6]}
PY
docker rm -f "$CANARY_CONTAINER" >/dev/null 2>&1 || true

OLD_IMAGE_ID="$(docker inspect -f '{{.Image}}' "$PROD_CONTAINER" 2>/dev/null || true)"
[[ -n "$OLD_IMAGE_ID" ]] || fail "production DDS3 container missing"
docker tag "$OLD_IMAGE_ID" "$ROLLBACK_IMAGE"
ENV_BACKUP="${ENV_FILE}.pre-oidc-auto-20260823"
cp -a "$ENV_FILE" "$ENV_BACKUP"
cp -a "$CANARY_ENV" "$ENV_FILE"
chmod 600 "$ENV_FILE"

start_runtime(){
  docker rm -f "$PROD_CONTAINER" >/dev/null 2>&1 || true
  docker run -d --name "$PROD_CONTAINER" --restart unless-stopped \
    --security-opt no-new-privileges:true --log-opt max-size=10m --log-opt max-file=5 \
    --env-file "$ENV_FILE" -p 127.0.0.1:8080:8080 "$1" >/dev/null
}
rollback(){
  printf 'ROLLBACK_START\n' >&2
  cp -a "$ENV_BACKUP" "$ENV_FILE"
  start_runtime "$ROLLBACK_IMAGE"
  for _ in $(seq 1 45); do
    curl -fsS --max-time 5 http://127.0.0.1:8080/readyz >/dev/null 2>&1 && { printf 'ROLLBACK_READY_PASS\n' >&2; return 0; }
    sleep 2
  done
  return 1
}

start_runtime "$NEW_IMAGE"
READY=0
for _ in $(seq 1 60); do
  if curl -fsS --max-time 5 http://127.0.0.1:8080/readyz >/tmp/dds3-oidc-prod-ready.json 2>/dev/null; then READY=1; break; fi
  sleep 2
done
if [[ "$READY" != 1 ]]; then rollback || true; fail "new production readiness failed"; fi
if ! curl -fsS --max-time 30 -H "Authorization: Bearer $STATIC_TOKEN" -H 'Content-Type: application/json' \
  -d "{\"operation\":\"dd_table\",\"pbn\":\"$DEAL\",\"dealer\":\"N\",\"vulnerability\":\"None\"}" \
  http://127.0.0.1:8080/v1/compute >/tmp/dds3-oidc-prod-golden.json; then
  rollback || true; fail "new production golden request failed"
fi
if ! python3 -c "import json; r=json.load(open('/tmp/dds3-oidc-prod-golden.json')); assert r['engine']=='DDS3' and r['fallback_used'] is False; assert r['par_score_ns']==-110"; then
  rollback || true; fail "new production golden response mismatch"
fi

printf '%s\n' "$TARGET_SHA" >/opt/bridge-school/dds3-runtime-git-sha
printf 'OIDC_AUTO_PRODUCTION_PASS target_sha=%s\n' "$TARGET_SHA"
