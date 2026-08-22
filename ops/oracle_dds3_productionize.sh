#!/usr/bin/env bash
set -Eeuo pipefail

# Final production hardening for the already-created Bridge School DDS3 OCI VM.
# This script is intentionally idempotent and fail-closed:
# - Frankfurt only
# - reuses one existing VM; never creates a second VM
# - never upgrades the Oracle account or changes billing plan
# - opens only 22/80/443 on the dedicated DDS3 security list
# - DDS3 container remains localhost-only; nginx terminates HTTPS
# - authenticates Vercel production calls with short-lived Vercel OIDC

REGION="${REGION:-eu-frankfurt-1}"
INSTANCE_NAME="${INSTANCE_NAME:-bridge-school-dds3-frankfurt}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/bridge_school_dds3_oracle}"
REPO_URL="${REPO_URL:-https://github.com/olegmed1-art/bridge-video-free.git}"
REPO_REF="${REPO_REF:-feature/oci-dds3-production}"
BUDGET_NAME="${BUDGET_NAME:-bridge-school-dds3-trial-guard}"
BUDGET_AMOUNT="${BUDGET_AMOUNT:-250}"

VERCEL_TEAM_SLUG="${VERCEL_TEAM_SLUG:-olegmed1-4368s-projects}"
VERCEL_PROJECT_NAME="${VERCEL_PROJECT_NAME:-bridge-video-free}"
VERCEL_TEAM_ID="${VERCEL_TEAM_ID:-team_qXr2smag8blW1WWeS10CDRXb}"
VERCEL_PROJECT_ID="${VERCEL_PROJECT_ID:-prj_oF4SA0gA1PX6BuJEmJ1BiHVBXUGP}"
VERCEL_ENVIRONMENT="${VERCEL_ENVIRONMENT:-production}"
VERCEL_ISSUER_MODE="${VERCEL_ISSUER_MODE:-team}"

export OCI_CLI_REGION="$REGION"

log(){ printf '\n[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
warn(){ printf '\nWARNING: %s\n' "$*" >&2; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }
nullish(){ [[ -z "${1:-}" || "${1:-}" == "null" || "${1:-}" == "None" ]]; }

command -v oci >/dev/null 2>&1 || die "OCI CLI not found. Run this in Oracle Cloud Shell."
command -v python3 >/dev/null 2>&1 || die "python3 not found."
command -v ssh >/dev/null 2>&1 || die "ssh not found."
[[ -f "$SSH_KEY" ]] || die "Dedicated SSH key not found at $SSH_KEY. Use the same Cloud Shell home used for initial setup."
chmod 600 "$SSH_KEY"

log "Preflight: identify tenancy and existing Frankfurt DDS3 VM"
ROOT_JSON="$(oci iam compartment list --include-root --all --output json)"
TENANCY_ID="$(printf '%s' "$ROOT_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin).get("data",[]); print(next((x.get("id","") for x in d if str(x.get("id","")).startswith("ocid1.tenancy.")), ""))')"
nullish "$TENANCY_ID" && die "Could not determine tenancy OCID."
COMPARTMENT_ID="${COMPARTMENT_ID:-$TENANCY_ID}"

INSTANCES_JSON="$(oci compute instance list -c "$COMPARTMENT_ID" --display-name "$INSTANCE_NAME" --all --output json)"
INSTANCE_ID="$(printf '%s' "$INSTANCES_JSON" | python3 -c '
import json,sys
xs=json.load(sys.stdin).get("data",[])
xs=[x for x in xs if x.get("lifecycle-state") not in {"TERMINATED","TERMINATING"}]
xs.sort(key=lambda x:x.get("time-created", ""), reverse=True)
print(xs[0].get("id","") if xs else "")
')"
nullish "$INSTANCE_ID" && die "Existing $INSTANCE_NAME VM not found. This script will NOT create a new VM."

INSTANCE_JSON="$(oci compute instance get --instance-id "$INSTANCE_ID" --output json)"
STATE="$(printf '%s' "$INSTANCE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"].get("lifecycle-state", ""))')"
SHAPE="$(printf '%s' "$INSTANCE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"].get("shape", ""))')"
OCPU="$(printf '%s' "$INSTANCE_JSON" | python3 -c 'import json,sys; print((json.load(sys.stdin)["data"].get("shape-config") or {}).get("ocpus", ""))')"
MEM="$(printf '%s' "$INSTANCE_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin)["data"].get("shape-config") or {}; print(d.get("memory-in-gbs", d.get("memoryInGBs", "")))')"
AD="$(printf '%s' "$INSTANCE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"].get("availability-domain", ""))')"
[[ "$STATE" == "RUNNING" ]] || die "Existing VM state is $STATE, expected RUNNING."

VNIC_JSON="$(oci compute instance list-vnics --instance-id "$INSTANCE_ID" --all --output json)"
PUBLIC_IP="$(printf '%s' "$VNIC_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin).get("data",[]); print((d[0].get("public-ip") if d else "") or "")')"
SUBNET_ID="$(printf '%s' "$VNIC_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin).get("data",[]); print((d[0].get("subnet-id") if d else "") or "")')"
nullish "$PUBLIC_IP" && die "Existing VM has no public IP."
nullish "$SUBNET_ID" && die "Could not determine VM subnet."

printf 'VM: %s\nShape: %s\nOCPU/RAM: %s / %s GB\nAD: %s\nPublic IP: %s\n' "$INSTANCE_ID" "$SHAPE" "$OCPU" "$MEM" "$AD" "$PUBLIC_IP"

log "Network hardening: allow only SSH + ACME HTTP + HTTPS inbound"
SUBNET_JSON="$(oci network subnet get --subnet-id "$SUBNET_ID" --output json)"
SECURITY_LIST_ID="$(printf '%s' "$SUBNET_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin)["data"]; xs=d.get("security-list-ids") or []; print(xs[0] if xs else "")')"
nullish "$SECURITY_LIST_ID" && die "Subnet has no security list."
INGRESS_RULES='[
  {"source":"0.0.0.0/0","protocol":"6","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":22,"max":22}}},
  {"source":"0.0.0.0/0","protocol":"6","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":80,"max":80}}},
  {"source":"0.0.0.0/0","protocol":"6","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":443,"max":443}}}
]'
oci network security-list update --security-list-id "$SECURITY_LIST_ID" --ingress-security-rules "$INGRESS_RULES" --force >/dev/null
log "Security list updated: 22, 80, 443 only"

if [[ -z "${BUDGET_EMAIL:-}" ]]; then
  printf '\nOCI budget alerts need an email recipient.\n'
  read -r -p 'Email for budget alerts: ' BUDGET_EMAIL
fi
BUDGET_EMAIL="${BUDGET_EMAIL:-}"

log "Budget guard: monthly $BUDGET_AMOUNT with actual/forecast alerts"
set +e
BUDGET_ID="$(oci budgets budget budget list -c "$TENANCY_ID" --display-name "$BUDGET_NAME" --all --query 'data[0].id' --raw-output 2>/tmp/dds3-budget-list.err)"
BUDGET_LIST_RC=$?
set -e
if [[ "$BUDGET_LIST_RC" -ne 0 ]]; then
  warn "Could not list budgets; continuing server hardening. $(tail -n 1 /tmp/dds3-budget-list.err 2>/dev/null || true)"
  BUDGET_ID=""
fi
if nullish "$BUDGET_ID"; then
  set +e
  BUDGET_ID="$(oci budgets budget budget create \
    --amount "$BUDGET_AMOUNT" \
    --compartment-id "$TENANCY_ID" \
    --reset-period MONTHLY \
    --display-name "$BUDGET_NAME" \
    --target-type COMPARTMENT \
    --targets "[\"$COMPARTMENT_ID\"]" \
    --query 'data.id' --raw-output 2>/tmp/dds3-budget-create.err)"
  BUDGET_CREATE_RC=$?
  set -e
  if [[ "$BUDGET_CREATE_RC" -ne 0 ]]; then
    warn "Budget creation failed; server work will continue. $(tail -n 2 /tmp/dds3-budget-create.err 2>/dev/null || true)"
    BUDGET_ID=""
  else
    log "Created budget: $BUDGET_ID"
  fi
else
  log "Reusing budget: $BUDGET_ID"
fi

create_alert_if_missing(){
  local name="$1" type="$2" threshold="$3"
  [[ -n "$BUDGET_ID" ]] || return 0
  local existing
  existing="$(oci budgets budget alert-rule list --budget-id "$BUDGET_ID" --display-name "$name" --all --query 'data[0].id' --raw-output 2>/dev/null || true)"
  if ! nullish "$existing"; then
    log "Budget alert already exists: $name"
    return 0
  fi
  local args=(oci budgets budget alert-rule create --budget-id "$BUDGET_ID" --display-name "$name" --threshold "$threshold" --threshold-type PERCENTAGE --type "$type" --message "Bridge School DDS3 Oracle trial budget guard: $name")
  if [[ -n "$BUDGET_EMAIL" ]]; then args+=(--recipients "$BUDGET_EMAIL"); fi
  if "${args[@]}" >/dev/null 2>/tmp/dds3-budget-alert.err; then
    log "Created budget alert: $name"
  else
    warn "Could not create budget alert $name: $(tail -n 1 /tmp/dds3-budget-alert.err 2>/dev/null || true)"
  fi
}
create_alert_if_missing actual-50 ACTUAL 50
create_alert_if_missing actual-75 ACTUAL 75
create_alert_if_missing actual-90 ACTUAL 90
create_alert_if_missing actual-100 ACTUAL 100
create_alert_if_missing forecast-90 FORECAST 90

SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new "ubuntu@$PUBLIC_IP")
log "Wait for SSH"
for _ in $(seq 1 36); do
  if "${SSH[@]}" 'echo SSH_OK' >/dev/null 2>&1; then break; fi
  sleep 5
done
"${SSH[@]}" 'echo SSH_OK' >/dev/null || die "SSH is not reachable."

log "Productionize existing VM: updated runtime, OIDC, HTTPS, auto-recovery, updates, logs, load test"
"${SSH[@]}" "sudo PUBLIC_IP='$PUBLIC_IP' REPO_URL='$REPO_URL' REPO_REF='$REPO_REF' VERCEL_TEAM_SLUG='$VERCEL_TEAM_SLUG' VERCEL_PROJECT_NAME='$VERCEL_PROJECT_NAME' VERCEL_TEAM_ID='$VERCEL_TEAM_ID' VERCEL_PROJECT_ID='$VERCEL_PROJECT_ID' VERCEL_ENVIRONMENT='$VERCEL_ENVIRONMENT' VERCEL_ISSUER_MODE='$VERCEL_ISSUER_MODE' bash -s" <<'REMOTE'
set -Eeuo pipefail
mkdir -p /opt/bridge-school
exec > >(tee -a /var/log/dds3-productionize.log) 2>&1
trap 'rc=$?; trap - ERR; printf "FAILED rc=%s at %s\n" "$rc" "$(date -u +%FT%TZ)" > /opt/bridge-school/DDS3_PRODUCTION_FAILED; exit "$rc"' ERR

REPO_DIR=/opt/bridge-school/bridge-video-free
DEAL='N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3'
export DEBIAN_FRONTEND=noninteractive

echo "=== DDS3 productionize $(date -u +%FT%TZ) ==="
apt-get update -qq
apt-get install -y --no-install-recommends \
  ca-certificates curl git docker.io jq openssl python3 python3-venv nginx logrotate unattended-upgrades >/dev/null
systemctl enable --now docker nginx >/dev/null

cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
cat >/etc/apt/apt.conf.d/52bridge-school-no-auto-reboot <<'EOF'
Unattended-Upgrade::Automatic-Reboot "false";
EOF

cat >/etc/ssh/sshd_config.d/99-bridge-school-dds3.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
EOF
systemctl reload ssh || systemctl reload sshd || true

if [[ ! -d "$REPO_DIR/.git" ]]; then
  rm -rf "$REPO_DIR"
  git clone "$REPO_URL" "$REPO_DIR"
fi
cd "$REPO_DIR"
git fetch --quiet origin "$REPO_REF"
git checkout --detach FETCH_HEAD
DEPLOY_SHA="$(git rev-parse HEAD)"
printf '%s\n' "$DEPLOY_SHA" >/opt/bridge-school/deployed-git-sha

docker build -f Dockerfile.dds3 -t bridge-school-dds3 .
docker run --rm bridge-school-dds3 N None "$DEAL" >/opt/bridge-school/golden-core.json
python3 - <<'PY'
import json
d=json.load(open('/opt/bridge-school/golden-core.json'))
assert d['par_score_ns']==-110
assert d['par_contracts']==['2S-EW']
assert d['dd_table']=={'S':[5,8,5,8],'H':[6,6,6,6],'D':[5,7,5,7],'C':[7,5,7,5],'NT':[6,6,6,6]}
print('DDS3_CORE_GOLDEN_PASS')
PY

docker build -f dds3_runtime/Dockerfile -t bridge-school-dds3-runtime .

umask 077
ENV_FILE=/opt/bridge-school/dds3-runtime.env
if [[ -f "$ENV_FILE" ]]; then
  STATIC_TOKEN="$(sed -n 's/^DDS3_RUNTIME_TOKEN=//p' "$ENV_FILE" | head -n1)"
else
  STATIC_TOKEN=""
fi
if [[ -z "$STATIC_TOKEN" ]]; then STATIC_TOKEN="$(openssl rand -hex 32)"; fi
cat >"$ENV_FILE" <<EOF
DDS3_RUNTIME_TOKEN=$STATIC_TOKEN
DDS3_TRUST_VERCEL_OIDC=true
DDS3_VERCEL_ISSUER_MODE=$VERCEL_ISSUER_MODE
DDS3_VERCEL_TEAM_SLUG=$VERCEL_TEAM_SLUG
DDS3_VERCEL_PROJECT_NAME=$VERCEL_PROJECT_NAME
DDS3_VERCEL_TEAM_ID=$VERCEL_TEAM_ID
DDS3_VERCEL_PROJECT_ID=$VERCEL_PROJECT_ID
DDS3_VERCEL_ENVIRONMENT=$VERCEL_ENVIRONMENT
EOF
chmod 600 "$ENV_FILE"

docker rm -f bridge-school-dds3-runtime >/dev/null 2>&1 || true
docker run -d \
  --name bridge-school-dds3-runtime \
  --restart unless-stopped \
  --security-opt no-new-privileges:true \
  --log-opt max-size=10m --log-opt max-file=5 \
  --env-file "$ENV_FILE" \
  -p 127.0.0.1:8080:8080 \
  bridge-school-dds3-runtime >/opt/bridge-school/runtime-container-id.txt

for _ in $(seq 1 60); do
  curl -fsS http://127.0.0.1:8080/readyz >/opt/bridge-school/runtime-ready.json && break
  sleep 2
done
curl -fsS http://127.0.0.1:8080/readyz >/opt/bridge-school/runtime-ready.json
curl -fsS -H "Authorization: Bearer $STATIC_TOKEN" -H 'Content-Type: application/json' \
  -d '{"operation":"dd_table","pbn":"N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3","dealer":"N","vulnerability":"None"}' \
  http://127.0.0.1:8080/v1/compute >/opt/bridge-school/runtime-result.json
python3 - <<'PY'
import json
r=json.load(open('/opt/bridge-school/runtime-result.json'))
assert r['engine']=='DDS3' and r['fallback_used'] is False
assert r['par_score_ns']==-110
assert r['dd_table']=={'S':[5,8,5,8],'H':[6,6,6,6],'D':[5,7,5,7],'C':[7,5,7,5],'NT':[6,6,6,6]}
print('DDS3_RUNTIME_STATIC_AUTH_PASS')
PY

mkdir -p /var/www/certbot/.well-known/acme-challenge
rm -f /etc/nginx/sites-enabled/default
cat >/etc/nginx/sites-available/dds3 <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name $PUBLIC_IP;
    server_tokens off;
    location ^~ /.well-known/acme-challenge/ {
        root /var/www/certbot;
        default_type text/plain;
        try_files \$uri =404;
    }
    location / { return 404; }
}
EOF
ln -sfn /etc/nginx/sites-available/dds3 /etc/nginx/sites-enabled/dds3
nginx -t
systemctl reload nginx

echo acme-ok >/var/www/certbot/.well-known/acme-challenge/bridge-school-probe
curl -fsS "http://$PUBLIC_IP/.well-known/acme-challenge/bridge-school-probe" | grep -qx acme-ok
rm -f /var/www/certbot/.well-known/acme-challenge/bridge-school-probe

if [[ ! -x /opt/certbot/bin/certbot ]]; then
  python3 -m venv /opt/certbot
  /opt/certbot/bin/pip install --quiet --upgrade pip
  /opt/certbot/bin/pip install --quiet 'certbot==5.7.0'
fi
/opt/certbot/bin/certbot --version

if [[ ! -s "/etc/letsencrypt/live/$PUBLIC_IP/fullchain.pem" ]]; then
  /opt/certbot/bin/certbot certonly \
    --non-interactive --agree-tos --register-unsafely-without-email \
    --preferred-profile shortlived \
    --webroot --webroot-path /var/www/certbot \
    --ip-address "$PUBLIC_IP"
fi
[[ -s "/etc/letsencrypt/live/$PUBLIC_IP/fullchain.pem" ]]
[[ -s "/etc/letsencrypt/live/$PUBLIC_IP/privkey.pem" ]]

cat >/etc/nginx/sites-available/dds3 <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name $PUBLIC_IP;
    server_tokens off;
    location ^~ /.well-known/acme-challenge/ {
        root /var/www/certbot;
        default_type text/plain;
        try_files \$uri =404;
    }
    location / { return 301 https://\$host\$request_uri; }
}
server {
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server;
    server_name $PUBLIC_IP;
    server_tokens off;
    ssl_certificate /etc/letsencrypt/live/$PUBLIC_IP/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$PUBLIC_IP/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_timeout 1d;
    ssl_session_cache shared:DDS3SSL:10m;
    client_max_body_size 16m;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_connect_timeout 5s;
        proxy_send_timeout 180s;
        proxy_read_timeout 180s;
        add_header X-Content-Type-Options nosniff always;
        add_header Referrer-Policy no-referrer always;
    }
}
EOF
nginx -t
systemctl reload nginx
curl -fsS "https://$PUBLIC_IP/readyz" >/opt/bridge-school/https-ready.json

mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat >/etc/letsencrypt/renewal-hooks/deploy/10-reload-nginx <<'EOF'
#!/bin/sh
systemctl reload nginx
EOF
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/10-reload-nginx
cat >/etc/systemd/system/dds3-cert-renew.service <<'EOF'
[Unit]
Description=Renew Bridge School DDS3 short-lived TLS certificate
After=network-online.target nginx.service
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=/opt/certbot/bin/certbot renew --quiet
EOF
cat >/etc/systemd/system/dds3-cert-renew.timer <<'EOF'
[Unit]
Description=Check DDS3 TLS certificate renewal twice daily
[Timer]
OnCalendar=*-*-* 00,12:17:00
RandomizedDelaySec=45m
Persistent=true
[Install]
WantedBy=timers.target
EOF

cat >/usr/local/sbin/dds3-healthcheck <<'EOF'
#!/bin/bash
set -u
if curl -fsS --max-time 8 http://127.0.0.1:8080/readyz >/dev/null; then
  exit 0
fi
logger -t dds3-healthcheck 'DDS3 readiness failed; restarting bridge-school-dds3-runtime'
docker restart bridge-school-dds3-runtime >/dev/null || exit 1
sleep 4
curl -fsS --max-time 8 http://127.0.0.1:8080/readyz >/dev/null
EOF
chmod 755 /usr/local/sbin/dds3-healthcheck
cat >/etc/systemd/system/dds3-healthcheck.service <<'EOF'
[Unit]
Description=Bridge School DDS3 health check and auto-recovery
After=docker.service
Requires=docker.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/dds3-healthcheck
EOF
cat >/etc/systemd/system/dds3-healthcheck.timer <<'EOF'
[Unit]
Description=Run Bridge School DDS3 health check every minute
[Timer]
OnBootSec=2m
OnUnitActiveSec=1m
AccuracySec=15s
Persistent=true
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now dds3-healthcheck.timer dds3-cert-renew.timer >/dev/null

cat >/etc/logrotate.d/bridge-school-dds3 <<'EOF'
/var/log/dds3-*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
EOF

cat >/opt/bridge-school/load_test.py <<'PY'
from __future__ import annotations
import concurrent.futures, json, os, statistics, time, urllib.request

TOKEN=open('/opt/bridge-school/dds3-runtime.env').read().split('DDS3_RUNTIME_TOKEN=',1)[1].splitlines()[0].strip()
URL='http://127.0.0.1:8080/v1/compute'
PAYLOAD=json.dumps({
    'operation':'dd_table',
    'pbn':'N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3',
    'dealer':'N','vulnerability':'None'
}).encode()

def one(_):
    req=urllib.request.Request(URL, data=PAYLOAD, method='POST', headers={
        'Content-Type':'application/json','Authorization':f'Bearer {TOKEN}'
    })
    start=time.perf_counter()
    with urllib.request.urlopen(req, timeout=30) as r:
        d=json.loads(r.read())
    elapsed=time.perf_counter()-start
    if d.get('engine')!='DDS3' or d.get('fallback_used') is not False or d.get('par_score_ns')!=-110:
        raise RuntimeError('DDS3 provenance/golden mismatch')
    return elapsed

one(0)
results=[]
requests_per_level=24
for concurrency in (1,2,4,6):
    start=time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as ex:
        lat=list(ex.map(one, range(requests_per_level)))
    total=time.perf_counter()-start
    results.append({
        'concurrency':concurrency,
        'requests':requests_per_level,
        'seconds':round(total,6),
        'requests_per_second':round(requests_per_level/total,6),
        'latency_mean_ms':round(statistics.mean(lat)*1000,3),
        'latency_p95_ms':round(sorted(lat)[max(0, int(len(lat)*0.95)-1)]*1000,3),
        'failures':0,
    })
best=max(results, key=lambda x:x['requests_per_second'])
out={
    'status':'PASS','engine':'DDS3','fallback_used':False,
    'logical_cpus':os.cpu_count(),
    'levels':results,
    'recommended_concurrency':best['concurrency'],
    'best_requests_per_second':best['requests_per_second'],
}
print(json.dumps(out, indent=2, sort_keys=True))
PY
python3 /opt/bridge-school/load_test.py >/opt/bridge-school/load-test.json
cat /opt/bridge-school/load-test.json

systemctl is-active nginx docker >/dev/null
systemctl is-enabled dds3-healthcheck.timer dds3-cert-renew.timer >/dev/null
curl -fsS "https://$PUBLIC_IP/readyz" >/opt/bridge-school/https-ready.json
openssl x509 -in "/etc/letsencrypt/live/$PUBLIC_IP/fullchain.pem" -noout -dates -ext subjectAltName >/opt/bridge-school/tls-certificate.txt

docker image prune -f >/dev/null || true
rm -f /opt/bridge-school/DDS3_PRODUCTION_FAILED
printf 'READY %s\n' "$(date -u +%FT%TZ)" >/opt/bridge-school/DDS3_PRODUCTION_READY

printf '\n=== SERVER PRODUCTION RESULT ===\n'
printf 'Git SHA: %s\n' "$DEPLOY_SHA"
printf 'HTTPS: https://%s/readyz\n' "$PUBLIC_IP"
printf 'Public ports: 22,80,443; DDS3 container: 127.0.0.1:8080\n'
printf 'OIDC: Vercel %s/%s environment=%s issuer=%s\n' "$VERCEL_TEAM_SLUG" "$VERCEL_PROJECT_NAME" "$VERCEL_ENVIRONMENT" "$VERCEL_ISSUER_MODE"
printf 'Health timer: %s\n' "$(systemctl is-active dds3-healthcheck.timer)"
printf 'Cert timer: %s\n' "$(systemctl is-active dds3-cert-renew.timer)"
cat /opt/bridge-school/load-test.json
REMOTE

log "External HTTPS verification from Oracle Cloud Shell"
curl -fsS --max-time 20 "https://$PUBLIC_IP/readyz" | tee /tmp/dds3-external-ready.json

MONTHLY_ESTIMATE="unknown"
if [[ "$SHAPE" == "VM.Standard.E5.Flex" || "$SHAPE" == "VM.Standard.E6.Flex" ]] && [[ "$OCPU" =~ ^[0-9.]+$ ]] && [[ "$MEM" =~ ^[0-9.]+$ ]]; then
  MONTHLY_ESTIMATE="$(python3 - "$OCPU" "$MEM" <<'PY'
import sys
oc=float(sys.argv[1]); mem=float(sys.argv[2]); print(f"{(oc*0.03+mem*0.002)*720:.2f}")
PY
)"
elif [[ "$SHAPE" == "VM.Standard.E4.Flex" ]] && [[ "$OCPU" =~ ^[0-9.]+$ ]] && [[ "$MEM" =~ ^[0-9.]+$ ]]; then
  MONTHLY_ESTIMATE="$(python3 - "$OCPU" "$MEM" <<'PY'
import sys
oc=float(sys.argv[1]); mem=float(sys.argv[2]); print(f"{(oc*0.025+mem*0.0015)*720:.2f}")
PY
)"
fi

printf '\n========== FINAL OCI DDS3 SUMMARY ==========\n'
printf 'Shape:              %s\n' "$SHAPE"
printf 'OCPU / RAM:         %s / %s GB\n' "$OCPU" "$MEM"
printf 'Region / AD:        %s / %s\n' "$REGION" "$AD"
printf 'HTTPS endpoint:     https://%s\n' "$PUBLIC_IP"
printf 'Budget guard:       %s USD/month (%s)\n' "$BUDGET_AMOUNT" "${BUDGET_ID:-not-created}"
printf 'Compute estimate:   $%s per 720h (reference estimate)\n' "$MONTHLY_ESTIMATE"
printf 'Account upgrade:    NOT PERFORMED\n'
printf 'Second VM:          NOT CREATED\n'
printf '============================================\n'

log "Load-test result from VM"
"${SSH[@]}" 'sudo cat /opt/bridge-school/load-test.json'
log "TLS certificate"
"${SSH[@]}" 'sudo cat /opt/bridge-school/tls-certificate.txt'

printf '\nNEXT: deploy the matching application commit to Vercel and verify /dds3/readyz returns authenticated_compute=ready.\n'
