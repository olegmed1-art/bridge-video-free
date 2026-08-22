#!/usr/bin/env bash
set -Eeuo pipefail

# Deterministic listener/firewall repair for the existing Bridge School DDS3 OCI VM.
# No VM creation. No account Upgrade. No billing changes.
REGION="${REGION:-eu-frankfurt-1}"
INSTANCE_NAME="${INSTANCE_NAME:-bridge-school-dds3-frankfurt}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/bridge_school_dds3_oracle}"
export OCI_CLI_REGION="$REGION"

log(){ printf '\n[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }
nullish(){ [[ -z "${1:-}" || "${1:-}" == "null" || "${1:-}" == "None" ]]; }

command -v oci >/dev/null 2>&1 || die "Run this script in Oracle Cloud Shell."
command -v python3 >/dev/null 2>&1 || die "python3 not found."
[[ -f "$SSH_KEY" ]] || die "SSH key not found: $SSH_KEY"
chmod 600 "$SSH_KEY"

log "Locate the existing Frankfurt DDS3 VM"
ROOT_JSON="$(oci iam compartment list --include-root --all --output json)"
TENANCY_ID="$(printf '%s' "$ROOT_JSON" | python3 -c 'import json,sys; xs=json.load(sys.stdin).get("data",[]); print(next((x.get("id","") for x in xs if str(x.get("id","")).startswith("ocid1.tenancy.")), ""))')"
nullish "$TENANCY_ID" && die "Cannot determine tenancy."
COMPARTMENT_ID="${COMPARTMENT_ID:-$TENANCY_ID}"
INSTANCES="$(oci compute instance list -c "$COMPARTMENT_ID" --display-name "$INSTANCE_NAME" --all --output json)"
INSTANCE_ID="$(printf '%s' "$INSTANCES" | python3 -c 'import json,sys; xs=json.load(sys.stdin).get("data",[]); xs=[x for x in xs if x.get("lifecycle-state") not in {"TERMINATED","TERMINATING"}]; xs.sort(key=lambda x:x.get("time-created", ""), reverse=True); print(xs[0].get("id","") if xs else "")')"
nullish "$INSTANCE_ID" && die "Existing VM not found. This repair will not create one."
VNIC_JSON="$(oci compute instance list-vnics --instance-id "$INSTANCE_ID" --all --output json)"
PUBLIC_IP="$(printf '%s' "$VNIC_JSON" | python3 -c 'import json,sys; xs=json.load(sys.stdin).get("data",[]); print((xs[0].get("public-ip") if xs else "") or "")')"
SUBNET_ID="$(printf '%s' "$VNIC_JSON" | python3 -c 'import json,sys; xs=json.load(sys.stdin).get("data",[]); print((xs[0].get("subnet-id") if xs else "") or "")')"
nullish "$PUBLIC_IP" && die "VM has no public IP."
nullish "$SUBNET_ID" && die "Cannot determine subnet."
printf 'VM: %s\nPublic IP: %s\n' "$INSTANCE_ID" "$PUBLIC_IP"

log "Re-assert OCI VCN ingress 22/80/443"
SUBNET_JSON="$(oci network subnet get --subnet-id "$SUBNET_ID" --output json)"
SECURITY_LIST_ID="$(printf '%s' "$SUBNET_JSON" | python3 -c 'import json,sys; xs=json.load(sys.stdin)["data"].get("security-list-ids") or []; print(xs[0] if xs else "")')"
nullish "$SECURITY_LIST_ID" && die "Subnet has no security list."
INGRESS='[{"source":"0.0.0.0/0","protocol":"6","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":22,"max":22}}},{"source":"0.0.0.0/0","protocol":"6","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":80,"max":80}}},{"source":"0.0.0.0/0","protocol":"6","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":443,"max":443}}}]'
oci network security-list update --security-list-id "$SECURITY_LIST_ID" --ingress-security-rules "$INGRESS" --force >/dev/null

SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new "ubuntu@$PUBLIC_IP")
"${SSH[@]}" 'echo SSH_OK' >/dev/null || die "SSH to the VM is not reachable."

log "Normalize host firewall and HTTPS listener"
"${SSH[@]}" "sudo PUBLIC_IP='$PUBLIC_IP' bash -s" <<'REMOTE'
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends nginx curl ca-certificates iptables python3 python3-venv >/dev/null
systemctl enable --now docker >/dev/null

# Canonical DDS3 must already be healthy locally before touching public ingress.
docker inspect bridge-school-dds3-runtime >/dev/null
curl -fsS --max-time 8 http://127.0.0.1:8080/readyz >/tmp/dds3-local-ready.json
python3 - <<'PY'
import json
r=json.load(open('/tmp/dds3-local-ready.json'))
assert r.get('status')=='ready', r
assert r.get('engine')=='DDS3', r
assert r.get('fallback_used') is False, r
print('LOCAL_DDS3_READY_PASS')
PY

# Rebuild only our two INPUT rules deterministically. Delete every prior exact
# ACCEPT copy first, then insert 443 and 80 at the very top so they precede any
# OCI image REJECT rule. Do NOT flush/reload the rest of the OCI firewall.
cat >/usr/local/sbin/dds3-open-web-ports <<'EOF'
#!/bin/sh
set -eu
for port in 80 443; do
  while iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null; do
    iptables -D INPUT -p tcp --dport "$port" -j ACCEPT
  done
done
iptables -I INPUT 1 -p tcp --dport 443 -j ACCEPT
iptables -I INPUT 1 -p tcp --dport 80 -j ACCEPT
EOF
chmod 755 /usr/local/sbin/dds3-open-web-ports
/usr/local/sbin/dds3-open-web-ports

cat >/etc/systemd/system/dds3-host-firewall.service <<'EOF'
[Unit]
Description=Place Bridge School DDS3 web ACCEPT rules before OCI host rejects
After=network-pre.target
Before=nginx.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/dds3-open-web-ports
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable dds3-host-firewall.service >/dev/null

# Verify ordering, not merely rule existence.
iptables -L INPUT -n --line-numbers | head -n 15 >/tmp/dds3-iptables-head.txt
python3 - <<'PY'
import subprocess
raw=subprocess.check_output(['iptables','-S','INPUT'], text=True).splitlines()
for port in ('80','443'):
    accept=next((i for i,x in enumerate(raw) if f'--dport {port} -j ACCEPT' in x), None)
    reject=next((i for i,x in enumerate(raw) if ' -j REJECT' in x), None)
    assert accept is not None, (port, raw[:20])
    if reject is not None:
        assert accept < reject, (port, accept, reject, raw[:20])
print('HOST_FIREWALL_ORDER_PASS')
PY

mkdir -p /var/www/certbot/.well-known/acme-challenge
rm -f /etc/nginx/sites-enabled/default

# Start from HTTP-only config so a missing/incomplete prior certificate cannot
# prevent nginx from starting.
cat >/etc/nginx/sites-available/dds3 <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name $PUBLIC_IP;
    server_tokens off;
    location ^~ /.well-known/acme-challenge/ {
        root /var/www/certbot;
        try_files \$uri =404;
    }
    location / { return 404; }
}
EOF
ln -sfn /etc/nginx/sites-available/dds3 /etc/nginx/sites-enabled/dds3
nginx -t
systemctl restart nginx
ss -ltnp | grep -E '(^|[[:space:]])[^ ]*:80[[:space:]]' >/tmp/dds3-listen80.txt || {
  systemctl status nginx --no-pager >&2 || true
  journalctl -u nginx -n 80 --no-pager >&2 || true
  exit 20
}

# Ensure a publicly trusted IP certificate exists. Reuse a valid one when present.
CERT_DIR="/etc/letsencrypt/live/$PUBLIC_IP"
NEED_CERT=1
if [[ -s "$CERT_DIR/fullchain.pem" && -s "$CERT_DIR/privkey.pem" ]]; then
  if openssl x509 -checkend 86400 -noout -in "$CERT_DIR/fullchain.pem" >/dev/null 2>&1; then NEED_CERT=0; fi
fi
if [[ "$NEED_CERT" == 1 ]]; then
  if [[ ! -x /opt/certbot/bin/certbot ]]; then
    python3 -m venv /opt/certbot
    /opt/certbot/bin/pip install --quiet --upgrade pip
    /opt/certbot/bin/pip install --quiet 'certbot==5.7.0'
  fi
  /opt/certbot/bin/certbot certonly \
    --non-interactive --agree-tos --register-unsafely-without-email \
    --preferred-profile shortlived \
    --webroot --webroot-path /var/www/certbot \
    --ip-address "$PUBLIC_IP"
fi
[[ -s "$CERT_DIR/fullchain.pem" && -s "$CERT_DIR/privkey.pem" ]]

cat >/etc/nginx/sites-available/dds3 <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name $PUBLIC_IP;
    server_tokens off;
    location ^~ /.well-known/acme-challenge/ {
        root /var/www/certbot;
        try_files \$uri =404;
    }
    location / { return 301 https://\$host\$request_uri; }
}
server {
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server;
    server_name $PUBLIC_IP;
    server_tokens off;
    ssl_certificate $CERT_DIR/fullchain.pem;
    ssl_certificate_key $CERT_DIR/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
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
systemctl restart nginx
systemctl is-active --quiet nginx

# 443 must be bound on a non-loopback address.
ss -ltnp | tee /tmp/dds3-listeners.txt
if ! ss -ltn | awk '$4 ~ /(^|:|\])443$/ {print $4}' | grep -Eq '(^0\.0\.0\.0:443$|^\*:443$|^\[::\]:443$|^:::443$)'; then
  echo '443 is not listening publicly' >&2
  systemctl status nginx --no-pager >&2 || true
  journalctl -u nginx -n 80 --no-pager >&2 || true
  exit 21
fi

curl -fsS --max-time 10 "https://$PUBLIC_IP/readyz" >/tmp/dds3-local-https-ready.json
python3 - <<'PY'
import json
r=json.load(open('/tmp/dds3-local-https-ready.json'))
assert r.get('status')=='ready' and r.get('engine')=='DDS3' and r.get('fallback_used') is False, r
print('LOCAL_HTTPS_DDS3_PASS')
PY

printf '\n=== VM LISTENER DIAGNOSTICS ===\n'
cat /tmp/dds3-iptables-head.txt
cat /tmp/dds3-listeners.txt
openssl x509 -in "$CERT_DIR/fullchain.pem" -noout -subject -issuer -dates -ext subjectAltName
printf 'VM_LISTENER_REPAIR_PASS\n'
REMOTE

log "Verify the public path from Oracle Cloud Shell"
HTTP_CODE="$(curl -sS --max-time 15 -o /tmp/dds3-public-http.out -w '%{http_code}' "http://$PUBLIC_IP/readyz")" || die "Public HTTP connection failed from Cloud Shell."
[[ "$HTTP_CODE" == "301" || "$HTTP_CODE" == "308" ]] || die "Expected HTTP redirect, got $HTTP_CODE"
curl -fsS --max-time 20 "https://$PUBLIC_IP/readyz" | tee /tmp/dds3-public-https.json
python3 - <<'PY'
import json
r=json.load(open('/tmp/dds3-public-https.json'))
assert r.get('status')=='ready' and r.get('engine')=='DDS3' and r.get('fallback_used') is False, r
print('CLOUD_SHELL_PUBLIC_HTTPS_PASS')
PY

printf '\n========== LISTENER REPAIR COMPLETE ==========\n'
printf 'HTTPS:          https://%s/readyz\n' "$PUBLIC_IP"
printf 'VM:             existing/reused\n'
printf 'Second VM:      NOT CREATED\n'
printf 'Account Upgrade: NOT PERFORMED\n'
printf '==============================================\n'
