#!/usr/bin/env bash
set -Eeuo pipefail

# Rebuild the localhost DDS3 runtime from an exact reviewed Git commit. The
# previous image is retained and automatically restored if the golden probe
# fails. No VM, network, database, or production-routing resource is created.

[[ "$(id -u)" -eq 0 ]] || {
  echo "install_dds3_runtime.sh requires root" >&2
  exit 30
}
target_ref="${DDS3_GIT_REF:-}"
[[ "$target_ref" =~ ^[0-9a-f]{40}$ ]] || {
  echo "DDS3_GIT_REF must be an exact commit SHA" >&2
  exit 31
}

repo=/opt/bridge-school/bridge-video-free
env_file=/opt/bridge-school/dds3-runtime.env
[[ -d "$repo/.git" && -s "$env_file" ]]
command -v docker >/dev/null
systemctl is-active --quiet docker

previous_ref="$(git -C "$repo" rev-parse HEAD)"
short_ref="${target_ref:0:12}"
runtime_image="bridge-school-dds3-runtime:git-$short_ref"
rollback_image="bridge-school-dds3-runtime:rollback-last"
runtime_changed=0

start_runtime() {
  local image="$1"
  docker rm -f bridge-school-dds3-runtime >/dev/null 2>&1 || true
  docker run -d \
    --name bridge-school-dds3-runtime \
    --restart unless-stopped \
    --security-opt no-new-privileges:true \
    --cap-drop ALL \
    --pids-limit 512 \
    --memory 8g --memory-swap 8g --cpus 3.0 \
    --log-opt max-size=10m --log-opt max-file=5 \
    --env-file "$env_file" \
    -p 127.0.0.1:8080:8080 \
    "$image" >/opt/bridge-school/runtime-container-id.txt
}

rollback_required=1
rollback() {
  rc=$?
  trap - ERR
  if [[ "$rollback_required" == 1 ]]; then
    echo "DDS3 rollout failed; restoring previous runtime state" >&2
    if [[ "$runtime_changed" == 1 ]]; then
      start_runtime "$rollback_image" || true
    fi
    git -C "$repo" checkout --force --quiet --detach "$previous_ref" || true
  fi
  exit "$rc"
}
trap rollback ERR

git -C "$repo" fetch --quiet origin "$target_ref"
git -C "$repo" cat-file -e "$target_ref^{commit}"
git -C "$repo" checkout --force --quiet --detach "$target_ref"
deployed_ref="$(git -C "$repo" rev-parse HEAD)"
[[ "$deployed_ref" == "$target_ref" ]]

cd "$repo"
docker build -f Dockerfile.dds3 -t bridge-school-dds3 .
docker build -f dds3_runtime/Dockerfile -t "$runtime_image" .

old_image_id="$(docker inspect -f '{{.Image}}' bridge-school-dds3-runtime)"
[[ "$old_image_id" == sha256:* ]]
docker tag "$old_image_id" "$rollback_image"
runtime_changed=1
start_runtime "$runtime_image"
for attempt in $(seq 1 90); do
  curl -fsS --max-time 8 http://127.0.0.1:8080/readyz >/tmp/dds3-runtime-ready.json && break
  sleep 2
done
curl -fsS --max-time 8 http://127.0.0.1:8080/readyz >/tmp/dds3-runtime-ready.json

static_token="$(sed -n 's/^DDS3_RUNTIME_TOKEN=//p' "$env_file" | head -n 1)"
[[ -n "$static_token" ]]
golden_pbn='N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3'
curl -fsS --max-time 30 \
  -H "Authorization: Bearer $static_token" \
  -H 'Content-Type: application/json' \
  -d "{\"operation\":\"dd_table\",\"pbn\":\"$golden_pbn\",\"dealer\":\"N\",\"vulnerability\":\"None\"}" \
  http://127.0.0.1:8080/v1/compute >/tmp/dds3-runtime-result.json
python3 - <<'PY'
import hashlib
import json
import re

with open('/tmp/dds3-runtime-result.json', encoding='utf-8') as handle:
    result = json.load(handle)
pbn = 'N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3'
assert result['engine'] == 'DDS3' and result['fallback_used'] is False
assert result['engine_version'] == 'v3.0.0+cdd13cf5b700788ac8c1391501b42445b3129b45'
assert result['input_validated'] is True
assert result['hand_order'] == ['N', 'E', 'S', 'W']
assert result['strain_order'] == ['S', 'H', 'D', 'C', 'NT']
assert result['deal_pbn_sha256'] == hashlib.sha256(pbn.encode()).hexdigest()
assert re.fullmatch(r'[0-9a-f]{64}', result['request_sha256'])
assert result['par_score_ns'] == -110
assert result['par_contracts'] == ['2S-EW']
PY

cat >/usr/local/sbin/dds3-healthcheck <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail

ready_url=http://127.0.0.1:8080/readyz
if curl -fsS --max-time 8 "$ready_url" >/dev/null; then
  exit 0
fi

logger -t dds3-healthcheck 'DDS3 readiness failed; restarting bridge-school-dds3-runtime'
docker restart bridge-school-dds3-runtime >/dev/null
for attempt in $(seq 1 30); do
  curl -fsS --max-time 8 "$ready_url" >/dev/null && exit 0
  sleep 2
done
curl -fsS --max-time 8 "$ready_url" >/dev/null
EOF
chmod 0755 /usr/local/sbin/dds3-healthcheck

cat >/etc/systemd/system/dds3-healthcheck.service <<'EOF'
[Unit]
Description=Probe and recover the localhost Bridge DDS3 runtime
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/dds3-healthcheck
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
EOF

cat >/etc/systemd/system/dds3-healthcheck.timer <<'EOF'
[Unit]
Description=Periodic Bridge DDS3 runtime watchdog

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
RandomizedDelaySec=10s
Persistent=true
Unit=dds3-healthcheck.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now dds3-healthcheck.timer
systemctl start dds3-healthcheck.service
systemctl is-active --quiet dds3-healthcheck.timer

systemctl restart assistant-lab.service
sleep 3
systemctl is-active --quiet assistant-lab.service
rollback_required=0
trap - ERR
printf '%s\n' "$target_ref" >/opt/bridge-school/dds3-runtime-git-ref
printf 'ORACLE_DDS3_RUNTIME_ROLLOUT_PASS\ndds3_ref=%s\n' "$target_ref"
