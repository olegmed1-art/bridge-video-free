#!/usr/bin/env bash
set -Eeuo pipefail

MASS_USER="${DDS3_MASS_UNIX_USER:-dds3-mass}"
MASS_GROUP="${DDS3_MASS_UNIX_GROUP:-dds3-mass}"
STATE_ROOT="${DDS3_MASS_STATE_ROOT:-/opt/bridge-school/dds3-mass-validation}"
REPO_ROOT="${BRIDGE_SCHOOL_REPO_ROOT:-/opt/bridge-school/bridge-video-free}"
UNIT_SRC="$REPO_ROOT/deploy/oracle-dds3-mass/dds3-mass@.service"
UNIT_DST="/etc/systemd/system/dds3-mass@.service"
ACTIVATE="${DDS3_MASS_ACTIVATE:-0}"
BOOTSTRAP="${DDS3_MASS_BOOTSTRAP:-0}"

log(){ printf '\n[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "run as root on the existing Oracle host"
[[ "$ACTIVATE" =~ ^[01]$ ]] || die "DDS3_MASS_ACTIVATE must be 0 or 1"
[[ "$BOOTSTRAP" =~ ^[01]$ ]] || die "DDS3_MASS_BOOTSTRAP must be 0 or 1"
[[ "$STATE_ROOT" == /* && "$REPO_ROOT" == /* ]] || die "state/repo roots must be absolute"
[[ -d "$REPO_ROOT/.git" ]] || die "repository checkout missing: $REPO_ROOT"
[[ -f "$REPO_ROOT/dds_training/oracle_mass_dispatch.py" ]] || die "Oracle mass dispatcher missing"
[[ -f "$REPO_ROOT/dds_training/run_stage.py" ]] || die "canonical run_stage.py missing"
[[ -f "$UNIT_SRC" ]] || die "systemd template missing: $UNIT_SRC"
command -v systemctl >/dev/null 2>&1 || die "systemd is required"
command -v systemd-analyze >/dev/null 2>&1 || die "systemd-analyze is required"

log "Create dedicated DDS3 mass identity and confined state root"
if ! getent group "$MASS_GROUP" >/dev/null 2>&1; then groupadd --system "$MASS_GROUP"; fi
if ! id "$MASS_USER" >/dev/null 2>&1; then
  useradd --system --gid "$MASS_GROUP" --home-dir "$STATE_ROOT" --shell /usr/sbin/nologin "$MASS_USER"
fi
# The state root and evidence directory are traversable/readable so the bounded
# operator can publish sanitized evidence without privileged file reads. Inputs,
# work state and raw logs remain group-confined.
install -d -m 0755 -o "$MASS_USER" -g "$MASS_GROUP" "$STATE_ROOT"
install -d -m 0750 -o root -g "$MASS_GROUP" "$STATE_ROOT/requests"
install -d -m 0750 -o "$MASS_USER" -g "$MASS_GROUP" "$STATE_ROOT/work" "$STATE_ROOT/logs"
install -d -m 0755 -o "$MASS_USER" -g "$MASS_GROUP" "$STATE_ROOT/evidence"

if [[ "$BOOTSTRAP" == "1" ]]; then
  log "Bootstrap pinned DDS3 v3 training runtime without starting mass evaluation"
  (cd "$REPO_ROOT" && DDS_RUN_PREFLIGHT=1 bash dds_training/bootstrap_linux.sh)
fi
PYTHON="$REPO_ROOT/dds_training/.venv/bin/python"
[[ -x "$PYTHON" ]] || die "pinned DDS3 training venv missing; run with DDS3_MASS_BOOTSTRAP=1"

log "Compile dispatcher and verify canonical engine import"
"$PYTHON" -m py_compile "$REPO_ROOT/dds_training/oracle_mass_dispatch.py" "$REPO_ROOT/dds_training/run_stage.py"
if [[ -f "$REPO_ROOT/dds_training/oracle_mass_dispatch_v2.py" ]]; then
  "$PYTHON" -m py_compile "$REPO_ROOT/dds_training/oracle_mass_dispatch_v2.py"
fi
(
  cd "$REPO_ROOT/dds_training"
  "$PYTHON" - <<'PY'
from dds_engine import engine_info
x=engine_info()
assert isinstance(x, dict), x
text=str(x)
assert 'DDS3' in text or 'dds3' in text.lower(), x
print('DDS3_MASS_ENGINE_IMPORT_PASS')
PY
)

log "Verify service identity can reach the pinned runtime before installation"
sudo -u "$MASS_USER" test -x "$PYTHON" || die "mass service identity cannot execute pinned Python"
sudo -u "$MASS_USER" test -r "$REPO_ROOT/dds_training/oracle_mass_dispatch_v2.py" || die "mass service identity cannot read v2 dispatcher"
sudo -u "$MASS_USER" test -r "$REPO_ROOT/dds_training/run_stage.py" || die "mass service identity cannot read canonical run_stage.py"
sudo -u "$MASS_USER" "$PYTHON" - <<PY
import sys
sys.path.insert(0, '$REPO_ROOT/dds_training')
import oracle_mass_dispatch_v2
print('DDS3_MASS_SERVICE_IDENTITY_IMPORT_PASS')
PY

log "Install hardened Oracle-only systemd template"
install -m 0644 -o root -g root "$UNIT_SRC" "$UNIT_DST"
[[ "$STATE_ROOT" == "/opt/bridge-school/dds3-mass-validation" ]] || die "custom state root is unsupported by the fixed unit template"
[[ "$REPO_ROOT" == "/opt/bridge-school/bridge-video-free" ]] || die "custom repo root is unsupported by the fixed unit template"
systemctl daemon-reload
systemd-analyze verify "$UNIT_DST" >/dev/null

log "Verify mass targets are bounded and production DDS3 is not modified"
for target in 10000 30000 40000; do
  systemctl show "dds3-mass@${target}.service" -p FragmentPath >/dev/null
done
! grep -qE 'systemctl +(stop|restart|disable).*dds3' "$REPO_ROOT/dds_training/oracle_mass_dispatch.py" || die "dispatcher may not stop/restart DDS3"

if [[ "$ACTIVATE" == "1" ]]; then
  log "Activation is installation-only; no mass stage is auto-started"
fi
printf 'DDS3_MASS_INSTALL_PASS state_root=%s auto_started=0 targets=10000,30000,40000 evidence_readable=1\n' "$STATE_ROOT"
