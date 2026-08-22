from __future__ import annotations

import hashlib
import shlex
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import PlainTextResponse

from .db import connect

router = APIRouter()


def _bearer_token(authorization: str | None) -> str:
    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix):
        raise HTTPException(status_code=404, detail="bootstrap ticket not found")
    token = authorization[len(prefix) :].strip()
    if len(token) < 48 or len(token) > 128:
        raise HTTPException(status_code=404, detail="bootstrap ticket not found")
    return token


def _require_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=503, detail="bootstrap ticket is invalid")
    return value.strip()


def _build_bootstrap_script(payload: dict[str, Any]) -> str:
    database_url = _require_text(payload, "database_url")
    expected_db_user = _require_text(payload, "expected_db_user")
    public_ip = _require_text(payload, "public_ip")
    code_sha = _require_text(payload, "code_sha")
    repo_url = _require_text(payload, "repo_url")

    if not code_sha.isalnum() or len(code_sha) != 40:
        raise HTTPException(status_code=503, detail="bootstrap ticket is invalid")
    parts = public_ip.split(".")
    if len(parts) != 4 or any(not part.isdigit() or not 0 <= int(part) <= 255 for part in parts):
        raise HTTPException(status_code=503, detail="bootstrap ticket is invalid")

    q = shlex.quote
    return f"""#!/usr/bin/env bash
set -Eeuo pipefail
set +x

REPO_DIR=/opt/bridge-school/bridge-video-free
REPO_URL={q(repo_url)}
CODE_SHA={q(code_sha)}
PUBLIC_IP={q(public_ip)}
ASSISTANT_LAB_DATABASE_URL={q(database_url)}
ASSISTANT_LAB_EXPECTED_DB_USER={q(expected_db_user)}

[[ \"$(id -u)\" -eq 0 ]] || {{ echo 'BOOTSTRAP_NEEDS_ROOT' >&2; exit 40; }}

if ! command -v git >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y --no-install-recommends git ca-certificates curl >/dev/null
fi

mkdir -p /opt/bridge-school
if [[ ! -d \"$REPO_DIR/.git\" ]]; then
  rm -rf \"$REPO_DIR\"
  git clone --quiet \"$REPO_URL\" \"$REPO_DIR\"
fi
cd \"$REPO_DIR\"
git fetch --quiet origin main
git cat-file -e \"$CODE_SHA^{{commit}}\" 2>/dev/null || git fetch --quiet origin \"$CODE_SHA\"
git checkout --quiet --detach \"$CODE_SHA\"
[[ \"$(git rev-parse HEAD)\" == \"$CODE_SHA\" ]]

PUBLIC_IP=\"$PUBLIC_IP\" bash ops/oracle_dds3_host_repair.sh
bash ops/oracle_assistant_lab_preflight.sh
ASSISTANT_LAB_DATABASE_URL=\"$ASSISTANT_LAB_DATABASE_URL\" \\
ASSISTANT_LAB_EXPECTED_DB_USER=\"$ASSISTANT_LAB_EXPECTED_DB_USER\" \\
ASSISTANT_LAB_ACTIVATE=1 \\
bash ops/oracle_assistant_lab_install.sh

systemctl is-active --quiet assistant-lab.service
curl -fsS --max-time 8 http://127.0.0.1:8080/readyz >/tmp/assistant-lab-final-ready.json
mkdir -p /opt/bridge-school/assistant-lab
python3 - <<'PY'
import json, os, platform, subprocess
ready=json.load(open('/tmp/assistant-lab-final-ready.json'))
mem_kb=0
for line in open('/proc/meminfo'):
    if line.startswith('MemTotal:'):
        mem_kb=int(line.split()[1]); break
out={{
    'status':'BOOTSTRAP_PASS',
    'hostname':platform.node(),
    'arch':platform.machine(),
    'cpus':os.cpu_count(),
    'mem_total_kb':mem_kb,
    'code_sha':subprocess.check_output(['git','-C','/opt/bridge-school/bridge-video-free','rev-parse','HEAD'], text=True).strip(),
    'dds3_engine':ready.get('engine'),
    'dds3_fallback_used':ready.get('fallback_used'),
    'assistant_lab_service':'active',
}}
with open('/opt/bridge-school/assistant-lab/bootstrap-evidence.json','w') as f:
    json.dump(out,f,sort_keys=True)
print(json.dumps(out,sort_keys=True))
PY
chmod 0600 /opt/bridge-school/assistant-lab/bootstrap-evidence.json
unset ASSISTANT_LAB_DATABASE_URL
printf 'ASSISTANT_LAB_OCI_BOOTSTRAP_PASS\\n'
"""


@router.post("/v1/assistant-lab/bootstrap", response_class=PlainTextResponse)
def assistant_lab_bootstrap(authorization: str | None = Header(default=None)) -> PlainTextResponse:
    """Redeem a short-lived capability for one host-local bootstrap script.

    The long-lived Neon credential is never placed in the URL or repository. The
    bearer capability is stored only as a SHA-256 digest in Neon and expires.
    """
    token = _bearer_token(authorization)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT assistant_lab.claim_bootstrap_ticket(%s) AS payload", (digest,))
        row = cur.fetchone()
    if not row or not isinstance(row.get("payload"), dict):
        raise HTTPException(status_code=404, detail="bootstrap ticket not found")
    script = _build_bootstrap_script(row["payload"])
    return PlainTextResponse(
        script,
        status_code=200,
        media_type="text/x-shellscript",
        headers={
            "Cache-Control": "private, no-store, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )


__all__ = ["router"]
