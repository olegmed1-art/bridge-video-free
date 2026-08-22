#!/usr/bin/env bash
set -Eeuo pipefail

# Host-local repair for the existing Oracle Frankfurt DDS3 VM.
# Intended to run through OCI Compute Instance Run Command (or an authenticated
# root shell) when public SSH is unavailable. It never creates an OCI resource.

PUBLIC_IP="${PUBLIC_IP:-}"
[[ "$(id -u)" -eq 0 ]] || { echo 'ERROR: root required' >&2; exit 40; }
[[ "$PUBLIC_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || { echo 'ERROR: PUBLIC_IP required' >&2; exit 41; }

log(){ printf '\n[%s] %s\n' "$(date -u +'%FT%TZ')" "$*"; }
die(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die 'docker missing'
command -v python3 >/dev/null 2>&1 || die 'python3 missing'

if ! command -v curl >/dev/null 2>&1 || ! command -v nginx >/dev/null 2>&1 || ! command -v iptables >/dev/null 2>&1; then
  command -v apt-get >/dev/null 2>&1 || die 'required packages missing and apt-get unavailable'
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y --no-install-recommends nginx curl python3-venv ca-certificates iptables >/dev/null
fi

log 'Verify hot local DDS3 before changing transport'
docker inspect bridge-school-dds3-runtime >/dev/null 2>&1 || die 'bridge-school-dds3-runtime missing'
LOCAL_READY="$(curl -fsS --max-time 8 http://127.0.0.1:8080/readyz)" || die 'local DDS3 readyz failed'
printf '%s' "$LOCAL_READY" | python3 -c 'import json,sys; x=json.load(sys.stdin); assert x.get("status")=="ready" and x.get("engine")=="DDS3" and x.get("fallback_used") is False, x' \
  || die 'local DDS3 provenance invalid'

log 'Open only HTTP/HTTPS in host firewall'
cat >/usr/local/sbin/dds3-open-web-ports <<'EOF'
#!/bin/sh
set -eu
iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p tcp --dport 80 -j ACCEPT
iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p tcp --dport 443 -j ACCEPT
EOF
chmod 0755 /usr/local/sbin/dds3-open-web-ports
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
systemctl enable --now nginx >/dev/null

log 'Prepare HTTP ACME endpoint'
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
nginx -t >/dev/null
systemctl reload nginx

if [[ ! -x /opt/certbot/bin/certbot ]]; then
  python3 -m venv /opt/certbot
  /opt/certbot/bin/pip install --quiet --upgrade pip
  /opt/certbot/bin/pip install --quiet 'certbot==5.7.0'
fi

log 'Ensure short-lived public IP TLS certificate'
if [[ ! -s "/etc/letsencrypt/live/$PUBLIC_IP/fullchain.pem" || ! -s "/etc/letsencrypt/live/$PUBLIC_IP/privkey.pem" ]]; then
  /opt/certbot/bin/certbot certonly \
    --non-interactive --agree-tos --register-unsafely-without-email \
    --preferred-profile shortlived \
    --webroot --webroot-path /var/www/certbot \
    --ip-address "$PUBLIC_IP"
fi

log 'Publish only nginx -> localhost DDS3'
cat >/etc/nginx/sites-available/dds3 <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name $PUBLIC_IP;
    server_tokens off;
    location ^~ /.well-known/acme-challenge/ { root /var/www/certbot; try_files \$uri =404; }
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
nginx -t >/dev/null
systemctl reload nginx
curl -fsS --max-time 12 "https://$PUBLIC_IP/readyz" >/tmp/assistant-lab-public-ready.json
python3 - <<'PY'
import json
x=json.load(open('/tmp/assistant-lab-public-ready.json'))
assert x.get('status') == 'ready', x
assert x.get('engine') == 'DDS3', x
assert x.get('fallback_used') is False, x
print('PUBLIC_DDS3_READINESS_PASS')
PY

mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat >/etc/letsencrypt/renewal-hooks/deploy/10-reload-nginx <<'EOF'
#!/bin/sh
systemctl reload nginx
EOF
chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/10-reload-nginx

printf 'OCI_DDS3_HOST_REPAIR_PASS public_ip=%s\n' "$PUBLIC_IP"
