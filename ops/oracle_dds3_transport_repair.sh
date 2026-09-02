#!/usr/bin/env bash
set -Eeuo pipefail

# Targeted repair for existing Bridge School DDS3 Frankfurt VM.
# Reuses the existing VM. No second VM. No Oracle account Upgrade.
REGION="${REGION:-eu-frankfurt-1}"
INSTANCE_NAME="${INSTANCE_NAME:-bridge-school-dds3-frankfurt}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/bridge_school_dds3_oracle}"
export OCI_CLI_REGION="$REGION"

log(){ printf '\n[%s] %s\n' "$(date -u +'%FT%TZ')" "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }
nullish(){ [[ -z "${1:-}" || "${1:-}" == "null" || "${1:-}" == "None" ]]; }
command -v oci >/dev/null || die "Run in Oracle Cloud Shell"
[[ -f "$SSH_KEY" ]] || die "Missing $SSH_KEY"

ROOT_JSON="$(oci iam compartment list --include-root --all --output json)"
TENANCY_ID="$(printf '%s' "$ROOT_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin).get("data",[]); print(next((x.get("id","") for x in d if str(x.get("id","")).startswith("ocid1.tenancy.")), ""))')"
nullish "$TENANCY_ID" && die "Cannot resolve tenancy"
COMPARTMENT_ID="${COMPARTMENT_ID:-$TENANCY_ID}"
INSTANCES="$(oci compute instance list -c "$COMPARTMENT_ID" --display-name "$INSTANCE_NAME" --all --output json)"
INSTANCE_ID="$(printf '%s' "$INSTANCES" | python3 -c 'import json,sys; xs=json.load(sys.stdin).get("data",[]); xs=[x for x in xs if x.get("lifecycle-state") not in {"TERMINATED","TERMINATING"}]; xs.sort(key=lambda x:x.get("time-created", ""), reverse=True); print(xs[0].get("id","") if xs else "")')"
nullish "$INSTANCE_ID" && die "Existing VM not found; will not create a new one"
VNIC_JSON="$(oci compute instance list-vnics --instance-id "$INSTANCE_ID" --all --output json)"
PUBLIC_IP="$(printf '%s' "$VNIC_JSON" | python3 -c 'import json,sys; x=json.load(sys.stdin).get("data",[]); print((x[0].get("public-ip") if x else "") or "")')"
SUBNET_ID="$(printf '%s' "$VNIC_JSON" | python3 -c 'import json,sys; x=json.load(sys.stdin).get("data",[]); print((x[0].get("subnet-id") if x else "") or "")')"
nullish "$PUBLIC_IP" && die "No public IP"

log "Re-assert OCI ingress 22/80/443"
SUBNET_JSON="$(oci network subnet get --subnet-id "$SUBNET_ID" --output json)"
SECURITY_LIST_ID="$(printf '%s' "$SUBNET_JSON" | python3 -c 'import json,sys; x=json.load(sys.stdin)["data"].get("security-list-ids") or []; print(x[0] if x else "")')"
INGRESS='[{"source":"0.0.0.0/0","protocol":"6","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":22,"max":22}}},{"source":"0.0.0.0/0","protocol":"6","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":80,"max":80}}},{"source":"0.0.0.0/0","protocol":"6","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":443,"max":443}}}]'
oci network security-list update --security-list-id "$SECURITY_LIST_ID" --ingress-security-rules "$INGRESS" --force >/dev/null

SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new "ubuntu@$PUBLIC_IP")
"${SSH[@]}" 'echo SSH_OK' >/dev/null || die "SSH unreachable"

log "Repair OCI host firewall, listeners, certificate, nginx, health timer"
"${SSH[@]}" "sudo PUBLIC_IP='$PUBLIC_IP' bash -s" <<'REMOTE'
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends nginx curl python3 python3-venv ca-certificates iptables >/dev/null
systemctl enable --now nginx docker >/dev/null

# OCI-provided images can enforce a host firewall independently of VCN security lists.
# Add only the two web ports; do not flush or reload OCI's existing iptables rules.
cat >/usr/local/sbin/dds3-open-web-ports <<'EOF'
#!/bin/sh
set -eu
iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p tcp --dport 80 -j ACCEPT
iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p tcp --dport 443 -j ACCEPT
EOF
chmod 755 /usr/local/sbin/dds3-open-web-ports
cat >/etc/systemd/system/dds3-host-firewall.service <<'EOF'
[Unit]
Description=Allow Bridge School DDS3 HTTP/HTTPS through OCI host firewall
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
systemctl enable --now dds3-host-firewall.service >/dev/null
iptables -C INPUT -p tcp --dport 80 -j ACCEPT
iptables -C INPUT -p tcp --dport 443 -j ACCEPT

# Do not expose container directly; assert localhost runtime is healthy.
docker inspect bridge-school-dds3-runtime >/dev/null
curl -fsS http://127.0.0.1:8080/readyz >/tmp/dds3-local-ready.json

mkdir -p /var/www/certbot/.well-known/acme-challenge
cat >/etc/nginx/sites-available/dds3 <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name $PUBLIC_IP;
    location ^~ /.well-known/acme-challenge/ { root /var/www/certbot; try_files \$uri =404; }
    location / { return 404; }
}
EOF
rm -f /etc/nginx/sites-enabled/default
ln -sfn /etc/nginx/sites-available/dds3 /etc/nginx/sites-enabled/dds3
nginx -t
systemctl reload nginx

ss -ltnp | grep -E ':(80|8080)\b' || true

echo acme-ok >/var/www/certbot/.well-known/acme-challenge/bridge-school-probe
curl -fsS http://127.0.0.1/.well-known/acme-challenge/bridge-school-probe | grep -qx acme-ok

if [[ ! -x /opt/certbot/bin/certbot ]]; then
  python3 -m venv /opt/certbot
  /opt/certbot/bin/pip install --quiet --upgrade pip
  /opt/certbot/bin/pip install --quiet 'certbot==5.7.0'
fi
if [[ ! -s "/etc/letsencrypt/live/$PUBLIC_IP/fullchain.pem" ]]; then
  /opt/certbot/bin/certbot certonly --non-interactive --agree-tos --register-unsafely-without-email \
    --preferred-profile shortlived --webroot --webroot-path /var/www/certbot --ip-address "$PUBLIC_IP"
fi

cat >/etc/nginx/sites-available/dds3 <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name $PUBLIC_IP;
    location ^~ /.well-known/acme-challenge/ { root /var/www/certbot; try_files \$uri =404; }
    location / { return 301 https://\$host\$request_uri; }
}
server {
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server;
    server_name $PUBLIC_IP;
    ssl_certificate /etc/letsencrypt/live/$PUBLIC_IP/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$PUBLIC_IP/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 16m;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_connect_timeout 5s;
        proxy_read_timeout 180s;
    }
}
EOF
nginx -t
systemctl reload nginx
curl -fsS "https://$PUBLIC_IP/readyz" >/tmp/dds3-https-ready.json
ss -ltnp | grep -E ':(80|443|8080)\b'
systemctl restart dds3-healthcheck.timer 2>/dev/null || true
printf 'SERVER_TRANSPORT_REPAIR_PASS\n'
cat /tmp/dds3-https-ready.json
REMOTE

log "Verify from Cloud Shell public path"
curl -fsS --max-time 20 "http://$PUBLIC_IP/readyz" >/tmp/remote-http.json
curl -fsS --max-time 20 "https://$PUBLIC_IP/readyz" | tee /tmp/remote-https.json
printf '\nTRANSPORT_REPAIR_COMPLETE https://%s/readyz\n' "$PUBLIC_IP"
printf 'Account Upgrade: NOT PERFORMED\nSecond VM: NOT CREATED\n'
