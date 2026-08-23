#!/usr/bin/env bash
set -Eeuo pipefail

# Install/stage the Assistant Lab Oracle Observer v0.1 as an isolated resident service.
# It does not modify production routing, DDS3, the Universal Video Analyzer, or school canon.
# Optional: ASSISTANT_LAB_OBSERVER_ACTIVATE=1 enables/starts the service after preflight.

OBS_USER="${ASSISTANT_LAB_OBSERVER_UNIX_USER:-assistant-lab-observer}"
OBS_GROUP="${ASSISTANT_LAB_OBSERVER_UNIX_GROUP:-assistant-lab-observer}"
OBS_DIR="${ASSISTANT_LAB_OBSERVER_DIR:-/opt/bridge-school/assistant-lab-observer}"
REPO_DIR="${ASSISTANT_LAB_REPO_DIR:-/opt/bridge-school/bridge-video-free}"
SERVICE_NAME="${ASSISTANT_LAB_OBSERVER_SERVICE_NAME:-assistant-lab-observer.service}"
SERVICE_SRC="$REPO_DIR/deploy/oracle-assistant-lab/assistant-lab-observer.service"
SERVICE_DST="/etc/systemd/system/$SERVICE_NAME"
ACTIVATE="${ASSISTANT_LAB_OBSERVER_ACTIVATE:-0}"
PSUTIL_VERSION="${ASSISTANT_LAB_OBSERVER_PSUTIL_VERSION:-7.0.0}"

log(){ printf '\n[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "run as root on the existing Oracle host"
[[ "$ACTIVATE" =~ ^[01]$ ]] || die "ASSISTANT_LAB_OBSERVER_ACTIVATE must be 0 or 1"
[[ -d "$REPO_DIR/.git" ]] || die "expected repository checkout not found at $REPO_DIR"
[[ -f "$REPO_DIR/assistant_lab/observer.py" ]] || die "observer code missing from repository checkout"
[[ -f "$SERVICE_SRC" ]] || die "observer systemd unit missing: $SERVICE_SRC"
command -v python3 >/dev/null 2>&1 || die "python3 is required"
command -v systemctl >/dev/null 2>&1 || die "systemd is required"
command -v systemd-analyze >/dev/null 2>&1 || die "systemd-analyze is required"

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

log "Create bounded observer Python runtime"
python3 -m venv "$OBS_DIR/.venv"
"$OBS_DIR/.venv/bin/python" -m pip install --disable-pip-version-check --no-cache-dir "psutil==$PSUTIL_VERSION" >/dev/null
chown -R "$OBS_USER:$OBS_GROUP" "$OBS_DIR/.venv"

log "Compile observer and verify unprivileged state access"
PYTHONPATH="$REPO_DIR" "$OBS_DIR/.venv/bin/python" -m py_compile "$REPO_DIR/assistant_lab/observer.py"
runuser -u "$OBS_USER" -- test -r "$REPO_DIR/assistant_lab/observer.py" || die "observer user cannot read application code"
runuser -u "$OBS_USER" -- test -w "$OBS_DIR/jobs/pending" || die "observer user cannot write pending queue"
runuser -u "$OBS_USER" -- test -w "$OBS_DIR/experiments" || die "observer user cannot write experiment archive"

log "Install hardened observer systemd unit"
install -m 0644 -o root -g root "$SERVICE_SRC" "$SERVICE_DST"
systemctl daemon-reload
systemd-analyze verify "$SERVICE_DST" >/dev/null

log "Run isolated smoke experiment before activation"
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
  log "Enable resident Assistant Lab Observer"
  systemctl enable --now "$SERVICE_NAME"
  sleep 2
  systemctl is-active --quiet "$SERVICE_NAME" || {
    journalctl -u "$SERVICE_NAME" -n 60 --no-pager >&2 || true
    die "Assistant Lab Observer failed to become active"
  }
  printf 'ASSISTANT_LAB_OBSERVER_INSTALL_PASS activated=1\n'
else
  CURRENT_STATE="$(systemctl is-active "$SERVICE_NAME" 2>/dev/null || true)"
  log "Stage complete; service state left unchanged (${CURRENT_STATE:-unknown})"
  printf 'ASSISTANT_LAB_OBSERVER_INSTALL_PASS activated=0 state_unchanged=1\n'
fi
