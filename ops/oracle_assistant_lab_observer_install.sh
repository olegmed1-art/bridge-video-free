#!/usr/bin/env bash
set -Eeuo pipefail

# Install/stage Assistant Lab Oracle Observer v0.1 plus its localhost-only Control API.
# Neither service changes production routing, DDS3, the Universal Video Analyzer, or school canon.
# Optional: ASSISTANT_LAB_OBSERVER_ACTIVATE=1 enables/starts both services after preflight.

OBS_USER="${ASSISTANT_LAB_OBSERVER_UNIX_USER:-assistant-lab-observer}"
OBS_GROUP="${ASSISTANT_LAB_OBSERVER_UNIX_GROUP:-assistant-lab-observer}"
OBS_DIR="${ASSISTANT_LAB_OBSERVER_DIR:-/opt/bridge-school/assistant-lab-observer}"
REPO_DIR="${ASSISTANT_LAB_REPO_DIR:-/opt/bridge-school/bridge-video-free}"
SERVICE_NAME="${ASSISTANT_LAB_OBSERVER_SERVICE_NAME:-assistant-lab-observer.service}"
CONTROL_SERVICE_NAME="${ASSISTANT_LAB_CONTROL_SERVICE_NAME:-assistant-lab-control.service}"
SERVICE_SRC="$REPO_DIR/deploy/oracle-assistant-lab/assistant-lab-observer.service"
CONTROL_SERVICE_SRC="$REPO_DIR/deploy/oracle-assistant-lab/assistant-lab-control.service"
SERVICE_DST="/etc/systemd/system/$SERVICE_NAME"
CONTROL_SERVICE_DST="/etc/systemd/system/$CONTROL_SERVICE_NAME"
ACTIVATE="${ASSISTANT_LAB_OBSERVER_ACTIVATE:-0}"
PSUTIL_VERSION="${ASSISTANT_LAB_OBSERVER_PSUTIL_VERSION:-7.0.0}"
FASTAPI_VERSION="${ASSISTANT_LAB_CONTROL_FASTAPI_VERSION:-0.116.1}"
UVICORN_VERSION="${ASSISTANT_LAB_CONTROL_UVICORN_VERSION:-0.35.0}"

log(){ printf '\n[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "run as root on the existing Oracle host"
[[ "$ACTIVATE" =~ ^[01]$ ]] || die "ASSISTANT_LAB_OBSERVER_ACTIVATE must be 0 or 1"
[[ -d "$REPO_DIR/.git" ]] || die "expected repository checkout not found at $REPO_DIR"
[[ -f "$REPO_DIR/assistant_lab/observer.py" ]] || die "observer code missing from repository checkout"
[[ -f "$REPO_DIR/assistant_lab/control_api.py" ]] || die "control API code missing from repository checkout"
[[ -f "$SERVICE_SRC" ]] || die "observer systemd unit missing: $SERVICE_SRC"
[[ -f "$CONTROL_SERVICE_SRC" ]] || die "control systemd unit missing: $CONTROL_SERVICE_SRC"
command -v python3 >/dev/null 2>&1 || die "python3 is required"
command -v systemctl >/dev/null 2>&1 || die "systemd is required"
command -v systemd-analyze >/dev/null 2>&1 || die "systemd-analyze is required"
command -v curl >/dev/null 2>&1 || die "curl is required"

log "Create isolated observer Unix identity"
if ! getent group "$OBS_GROUP" >/dev/null 2>&1; then
  groupadd --system "$OBS_GROUP"
fi
if ! id "$OBS_USER" >/dev/null 2>&1; then
  useradd --system --gid "$OBS_GROUP" --home-dir "$OBS_DIR" --shell /usr/sbin/nologin "$OBS_USER"
fi
install -d -m 0750 -o "$OBS_USER" -g "$OBS_GROUP" "$OBS_DIR"
install -d -m 0750 -o "$OBS_USER" -g "$OBS_GROUP" \
  "$OBS_DIR/jobs/pending" "$OBS_DIR/jobs/running" "$OBS_DIR/jobs/done" "$OBS_DIR/jobs/failed" "$OBS_DIR/experiments"

log "Create bounded observer/control Python runtime"
python3 -m venv "$OBS_DIR/.venv"
"$OBS_DIR/.venv/bin/python" -m pip install --disable-pip-version-check --no-cache-dir \
  "psutil==$PSUTIL_VERSION" "fastapi==$FASTAPI_VERSION" "uvicorn==$UVICORN_VERSION" >/dev/null
chown -R "$OBS_USER:$OBS_GROUP" "$OBS_DIR/.venv"

log "Compile observer/control and verify unprivileged state access"
PYTHONPATH="$REPO_DIR" "$OBS_DIR/.venv/bin/python" -m py_compile \
  "$REPO_DIR/assistant_lab/observer.py" "$REPO_DIR/assistant_lab/control_api.py"
runuser -u "$OBS_USER" -- test -r "$REPO_DIR/assistant_lab/observer.py" || die "observer user cannot read observer code"
runuser -u "$OBS_USER" -- test -r "$REPO_DIR/assistant_lab/control_api.py" || die "observer user cannot read control API code"
runuser -u "$OBS_USER" -- test -w "$OBS_DIR/jobs/pending" || die "observer user cannot write pending queue"
runuser -u "$OBS_USER" -- test -w "$OBS_DIR/experiments" || die "observer user cannot write experiment archive"

log "Install root-controlled tool registry and bearer secret"
cat >"$OBS_DIR/tool_registry.json" <<'JSON'
{
  "schema": "assistant-lab-control-tools/v0.1",
  "tools": {
    "health.noop": {
      "argv": ["/bin/true"]
    }
  }
}
JSON
chown root:"$OBS_GROUP" "$OBS_DIR/tool_registry.json"
chmod 0640 "$OBS_DIR/tool_registry.json"

if [[ ! -f "$OBS_DIR/control.env" ]]; then
  CONTROL_TOKEN="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)"
  umask 077
  printf 'ASSISTANT_LAB_CONTROL_TOKEN=%s\n' "$CONTROL_TOKEN" >"$OBS_DIR/control.env"
fi
chown root:"$OBS_GROUP" "$OBS_DIR/control.env"
chmod 0640 "$OBS_DIR/control.env"

log "Install hardened observer and localhost-only control systemd units"
install -m 0644 -o root -g root "$SERVICE_SRC" "$SERVICE_DST"
install -m 0644 -o root -g root "$CONTROL_SERVICE_SRC" "$CONTROL_SERVICE_DST"
systemctl daemon-reload
systemd-analyze verify "$SERVICE_DST" "$CONTROL_SERVICE_DST" >/dev/null

log "Run isolated observer smoke experiment before activation"
SMOKE_ID="INSTALL-SMOKE-$(date -u +%Y%m%d%H%M%S)"
runuser -u "$OBS_USER" -- env PYTHONPATH="$REPO_DIR" ASSISTANT_LAB_OBSERVER_STATE_ROOT="$OBS_DIR" \
  "$OBS_DIR/.venv/bin/python" -m assistant_lab.observer submit --experiment-id "$SMOKE_ID" --timeout 30 --label install-smoke -- \
  "$OBS_DIR/.venv/bin/python" -c "from pathlib import Path; Path('smoke.txt').write_text('observer-ok')"
runuser -u "$OBS_USER" -- env PYTHONPATH="$REPO_DIR" ASSISTANT_LAB_OBSERVER_STATE_ROOT="$OBS_DIR" \
  "$OBS_DIR/.venv/bin/python" -m assistant_lab.observer daemon --once
SMOKE_REPORT="$OBS_DIR/experiments/$SMOKE_ID/observer/observer_report.json"
[[ -f "$SMOKE_REPORT" ]] || die "observer smoke report missing"
"$OBS_DIR/.venv/bin/python" - "$SMOKE_REPORT" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    x=json.load(fh)
assert x["exit_code"] == 0, x
assert x["timed_out"] is False, x
assert x["schema"] == "assistant-lab-observer-report/v0.1", x
print("ASSISTANT_LAB_OBSERVER_SMOKE_PASS")
PY

if [[ "$ACTIVATE" == "1" ]]; then
  log "Enable resident Assistant Lab Observer and localhost Control API"
  systemctl enable --now "$SERVICE_NAME"
  systemctl enable --now "$CONTROL_SERVICE_NAME"
  sleep 2
  systemctl is-active --quiet "$SERVICE_NAME" || {
    journalctl -u "$SERVICE_NAME" -n 60 --no-pager >&2 || true
    die "Assistant Lab Observer failed to become active"
  }
  systemctl is-active --quiet "$CONTROL_SERVICE_NAME" || {
    journalctl -u "$CONTROL_SERVICE_NAME" -n 60 --no-pager >&2 || true
    die "Assistant Lab Control API failed to become active"
  }
  CONTROL_TOKEN="$(sed -n 's/^ASSISTANT_LAB_CONTROL_TOKEN=//p' "$OBS_DIR/control.env")"
  HEALTH_JSON="$(curl -fsS --max-time 5 -H "Authorization: Bearer $CONTROL_TOKEN" http://127.0.0.1:8765/healthz)" \
    || die "Assistant Lab Control API health check failed"
  printf '%s' "$HEALTH_JSON" | "$OBS_DIR/.venv/bin/python" -c '
import json,sys
x=json.load(sys.stdin)
assert x.get("status")=="ready", x
assert x.get("arbitrary_shell") is False, x
assert x.get("video_analyzer_result_access") is False, x
assert x.get("other_oracle_result_access") is False, x
' || die "Assistant Lab Control API contract health invalid"
  printf 'ASSISTANT_LAB_OBSERVER_INSTALL_PASS activated=1 control=localhost-only\n'
else
  OBS_STATE="$(systemctl is-active "$SERVICE_NAME" 2>/dev/null || true)"
  CONTROL_STATE="$(systemctl is-active "$CONTROL_SERVICE_NAME" 2>/dev/null || true)"
  log "Stage complete; service states left unchanged (observer=${OBS_STATE:-unknown}, control=${CONTROL_STATE:-unknown})"
  printf 'ASSISTANT_LAB_OBSERVER_INSTALL_PASS activated=0 state_unchanged=1 control=localhost-only\n'
fi
