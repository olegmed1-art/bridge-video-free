#!/usr/bin/env bash
set -Eeuo pipefail

# Read-only three-valued classifier for the exact Oracle workload host.
# Output is always exactly two bounded lines. Any missing/stale/malformed source
# wins over known BUSY evidence so UNKNOWN can never be interpreted as IDLE.
ASSISTANT_LAB_PYTHON="${ASSISTANT_LAB_PYTHON:-/opt/bridge-school/assistant-lab/.venv/bin/python}"
ASSISTANT_LAB_ENV_FILE="${ASSISTANT_LAB_ENV_FILE:-/opt/bridge-school/assistant-lab/.env}"
BRIDGE_VIDEO_QUEUE_DSN_FILE="${BRIDGE_VIDEO_QUEUE_DSN_FILE:-/opt/bridge-school/universal-video/secrets/video-queue-dsn}"
BRIDGE_VIDEO_SPOOL_ROOT="${BRIDGE_VIDEO_SPOOL_ROOT:-/opt/bridge-school/universal-video/spool}"
ORACLE_HOST_LEASE_FILE="${ORACLE_HOST_LEASE_FILE:-/run/bridge-school/oracle-host-lease}"
ORACLE_IDLE_MAX_AGE_SECONDS="${ORACLE_IDLE_MAX_AGE_SECONDS:-180}"
ORACLE_IDLE_CLOCK_SKEW_SECONDS="${ORACLE_IDLE_CLOCK_SKEW_SECONDS:-15}"
ORACLE_HOST_LEASE_MAX_SECONDS="${ORACLE_HOST_LEASE_MAX_SECONDS:-3600}"
ORACLE_IDLE_SPOOL_PATHS="${ORACLE_IDLE_SPOOL_PATHS:-/opt/bridge-school/assistant-lab/spool:/opt/bridge-school/assistant-lab/feedback-spool:/opt/bridge-school/assistant-lab-observer/jobs/pending:/opt/bridge-school/assistant-lab-observer/jobs/running:/var/lib/bridge-school/uv-spool:/var/lib/bridge-school/feedback-spool}"

# Service/process probes are independent safety sources. Unknown systemd state
# or probe failure is UNKNOWN, not idle. A live observer daemon is BUSY even
# when its queue directories have not yet been inspected successfully.
run_probe() {
  local label="$1"; shift
  local output rc
  set +e
  output="$("$@" 2>/dev/null)"
  rc=$?
  set -e
  if ((rc == 0)); then
    printf '%s=ACTIVE\n' "$label"
  elif ((rc == 1 || rc == 3 || rc == 4)); then
    printf '%s=INACTIVE\n' "$label"
  else
    printf '%s=UNKNOWN\n' "$label"
  fi
}

assistant_service="$(run_probe ASSISTANT_LAB_SERVICE systemctl is-active --quiet assistant-lab.service)"
observer_service="$(run_probe ASSISTANT_LAB_OBSERVER_SERVICE systemctl is-active --quiet assistant-lab-observer.service)"
video_service="$(run_probe UNIVERSAL_VIDEO_SERVICE systemctl is-active --quiet universal-video-container.service)"

set +e
observer_process_output="$(pgrep -f '[a]ssistant_lab.*observer.*experiment|[o]racle_assistant_lab_observer.*run|[a]ssistant_lab\.observer[[:space:]]+(daemon|run)' 2>/dev/null)"
observer_process_rc=$?
set -e
case "$observer_process_rc" in
  0) observer_process='ACTIVE' ;;
  1) observer_process='INACTIVE' ;;
  *) observer_process='UNKNOWN' ;;
esac

set +e
result="$(
  ASSISTANT_SERVICE="$assistant_service" \
  OBSERVER_SERVICE="$observer_service" \
  VIDEO_SERVICE="$video_service" \
  OBSERVER_PROCESS="$observer_process" \
  ASSISTANT_LAB_ENV_FILE="$ASSISTANT_LAB_ENV_FILE" \
  BRIDGE_VIDEO_QUEUE_DSN_FILE="$BRIDGE_VIDEO_QUEUE_DSN_FILE" \
  BRIDGE_VIDEO_SPOOL_ROOT="$BRIDGE_VIDEO_SPOOL_ROOT" \
  ORACLE_HOST_LEASE_FILE="$ORACLE_HOST_LEASE_FILE" \
  ORACLE_IDLE_MAX_AGE_SECONDS="$ORACLE_IDLE_MAX_AGE_SECONDS" \
  ORACLE_IDLE_CLOCK_SKEW_SECONDS="$ORACLE_IDLE_CLOCK_SKEW_SECONDS" \
  ORACLE_HOST_LEASE_MAX_SECONDS="$ORACLE_HOST_LEASE_MAX_SECONDS" \
  ORACLE_IDLE_SPOOL_PATHS="$ORACLE_IDLE_SPOOL_PATHS" \
  "$ASSISTANT_LAB_PYTHON" - <<'PY'
from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path
from typing import Any

unknown: list[str] = []
busy: list[str] = []


def mark_unknown(reason: str) -> None:
    if reason not in unknown:
        unknown.append(reason)


def mark_busy(reason: str) -> None:
    if reason not in busy:
        busy.append(reason)


def positive_int(name: str) -> int | None:
    raw = os.environ.get(name, "")
    try:
        value = int(raw)
        if value <= 0:
            raise ValueError
        return value
    except (TypeError, ValueError):
        mark_unknown(f"{name.lower()}_invalid")
        return None


max_age = positive_int("ORACLE_IDLE_MAX_AGE_SECONDS")
clock_skew = positive_int("ORACLE_IDLE_CLOCK_SKEW_SECONDS")
max_lease = positive_int("ORACLE_HOST_LEASE_MAX_SECONDS")
now = int(time.time())


def fresh(observed: Any) -> bool:
    if max_age is None or clock_skew is None:
        return False
    try:
        epoch = int(observed)
    except (TypeError, ValueError):
        return False
    return now - max_age <= epoch <= now + clock_skew


for label, value in (
    ("assistant_lab_service", os.environ.get("ASSISTANT_SERVICE", "UNKNOWN")),
    ("assistant_lab_observer_service", os.environ.get("OBSERVER_SERVICE", "UNKNOWN")),
    ("universal_video_service", os.environ.get("VIDEO_SERVICE", "UNKNOWN")),
):
    if value == "ACTIVE":
        mark_busy(f"{label}_active")
    elif value == "UNKNOWN":
        mark_unknown(f"{label}_unknown")
    elif value != "INACTIVE":
        mark_unknown(f"{label}_invalid")

observer_process = os.environ.get("OBSERVER_PROCESS", "UNKNOWN")
if observer_process == "ACTIVE":
    mark_busy("assistant_lab_observer_process_active")
elif observer_process == "UNKNOWN":
    mark_unknown("assistant_lab_observer_process_unknown")
elif observer_process != "INACTIVE":
    mark_unknown("assistant_lab_observer_process_invalid")


def scan_generic_spool(path_text: str) -> None:
    if not path_text:
        mark_unknown("spool_path_empty")
        return
    path = Path(path_text)
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise OSError
        entries = list(os.scandir(path))
    except (FileNotFoundError, PermissionError, OSError):
        mark_unknown(f"spool_unavailable:{path}")
        return
    try:
        if entries:
            mark_busy(f"spool_nonempty:{path}")
    finally:
        for entry in entries:
            try:
                entry.close()  # type: ignore[attr-defined]
            except (AttributeError, OSError):
                pass


for spool in os.environ.get("ORACLE_IDLE_SPOOL_PATHS", "").split(":"):
    scan_generic_spool(spool)

video_root = Path(os.environ.get("BRIDGE_VIDEO_SPOOL_ROOT", ""))
try:
    root_info = video_root.lstat()
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise OSError
except (FileNotFoundError, PermissionError, OSError):
    mark_unknown("video_spool_unavailable")
else:
    for leaf in ("inbox", "running"):
        directory = video_root / leaf
        try:
            info = directory.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise OSError
            entries = list(os.scandir(directory))
        except (FileNotFoundError, PermissionError, OSError):
            mark_unknown(f"video_spool_{leaf}_unavailable")
            continue
        try:
            if entries:
                mark_busy(f"video_spool_{leaf}_nonempty")
        finally:
            for entry in entries:
                try:
                    entry.close()  # type: ignore[attr-defined]
                except (AttributeError, OSError):
                    pass

    progress = video_root / "progress"
    try:
        info = progress.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise OSError
        progress_entries = list(os.scandir(progress))
    except (FileNotFoundError, PermissionError, OSError):
        mark_unknown("video_spool_progress_unavailable")
    else:
        try:
            for entry in progress_entries:
                try:
                    if not entry.is_file(follow_symlinks=False):
                        mark_unknown("video_spool_progress_nonregular")
                        continue
                    with open(entry.path, "r", encoding="utf-8") as handle:
                        progress_data = json.load(handle)
                    if progress_data.get("schema") != "universal-video-pipeline-progress-v1":
                        raise ValueError
                    state_value = progress_data.get("state")
                    observed = progress_data.get("observed_at_unix")
                    if state_value in {"DOWNLOADING_FROM_DRIVE", "SOURCE_READY_ON_ORACLE", "PROCESSING"}:
                        if fresh(observed):
                            mark_busy(f"video_spool_progress_active:{state_value}")
                        else:
                            mark_unknown("video_spool_progress_stale")
                    elif state_value in {"RESULT_READY", "REVIEW", "FAILED"}:
                        if not fresh(observed):
                            mark_unknown("video_spool_progress_terminal_stale")
                    else:
                        mark_unknown("video_spool_progress_state_invalid")
                except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                    mark_unknown("video_spool_progress_invalid")
        finally:
            for entry in progress_entries:
                try:
                    entry.close()  # type: ignore[attr-defined]
                except (AttributeError, OSError):
                    pass

lease_path = Path(os.environ.get("ORACLE_HOST_LEASE_FILE", ""))
try:
    lease_info = lease_path.lstat()
except FileNotFoundError:
    pass
except (PermissionError, OSError):
    mark_unknown("host_lease_unavailable")
else:
    if stat.S_ISLNK(lease_info.st_mode) or not stat.S_ISREG(lease_info.st_mode):
        mark_unknown("host_lease_invalid_type")
    else:
        try:
            lines = lease_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            mark_unknown("host_lease_unreadable")
            lines = []
        purpose = "operator"
        issued: int | None = None
        expires: int | None = None
        try:
            if len(lines) == 1 and lines[0].startswith("expires_at_epoch="):
                expires = int(lines[0].split("=", 1)[1])
            elif len(lines) == 4:
                if lines[0] != "schema=oracle-host-lease-v1":
                    raise ValueError
                key, purpose = lines[1].split("=", 1)
                if key != "purpose" or purpose not in {"operator", "maintenance"}:
                    raise ValueError
                if not lines[2].startswith("issued_at_epoch=") or not lines[3].startswith("expires_at_epoch="):
                    raise ValueError
                issued = int(lines[2].split("=", 1)[1])
                expires = int(lines[3].split("=", 1)[1])
            else:
                raise ValueError
        except (ValueError, IndexError):
            mark_unknown("host_lease_invalid")
        if expires is not None and max_lease is not None and clock_skew is not None:
            if issued is not None and (issued > now + clock_skew or expires <= issued or expires - issued > max_lease):
                mark_unknown("host_lease_unbounded_or_invalid")
            elif expires <= now:
                mark_unknown("host_lease_stale")
            elif expires - now > max_lease:
                mark_unknown("host_lease_horizon_invalid")
            else:
                mark_busy(f"host_lease_active:{purpose}")


def regular_text(path: Path, missing_reason: str) -> str | None:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise OSError
        value = path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, PermissionError, OSError, UnicodeError):
        mark_unknown(missing_reason)
        return None
    if not value:
        mark_unknown(missing_reason.replace("unavailable", "empty"))
        return None
    return value


env_path = Path(os.environ.get("ASSISTANT_LAB_ENV_FILE", ""))
env_text = regular_text(env_path, "assistant_lab_env_unavailable")
assistant_dsn: str | None = None
if env_text is not None:
    matches = [line.split("=", 1)[1] for line in env_text.splitlines() if line.startswith("ASSISTANT_LAB_DATABASE_URL=")]
    if len(matches) != 1 or not matches[0]:
        mark_unknown("assistant_lab_dsn_missing_or_ambiguous")
    else:
        assistant_dsn = matches[0]
queue_dsn = regular_text(Path(os.environ.get("BRIDGE_VIDEO_QUEUE_DSN_FILE", "")), "video_queue_dsn_unavailable")

# Query both live databases only after their credentials are proved. The second
# failure is explicitly distinguished to prove partial success cannot authorize STOP.
if assistant_dsn is not None and queue_dsn is not None and max_age is not None and clock_skew is not None:
    try:
        import psycopg
    except Exception:
        mark_unknown("psycopg_unavailable")
    else:
        core: Any = None
        try:
            with psycopg.connect(assistant_dsn, connect_timeout=8, application_name="oracle-idle-state") as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT telemetry_schema, observed_at_epoch, active_jobs, active_research_jobs, "
                        "active_research_child_jobs, active_control_commands "
                        "FROM assistant_lab.oracle_idle_snapshot()"
                    )
                    core = cursor.fetchone()
        except Exception:
            mark_unknown("assistant_lab_telemetry_unavailable")
        core_valid = False
        if core is None:
            mark_unknown("assistant_lab_telemetry_empty")
        else:
            if len(core) != 6 or core[0] != "assistant-lab-oracle-idle-v2":
                mark_unknown("assistant_lab_telemetry_invalid")
            elif not fresh(core[1]):
                mark_unknown("assistant_lab_telemetry_stale")
            else:
                try:
                    core_counts = [int(value) for value in core[2:]]
                    if any(value < 0 for value in core_counts):
                        raise ValueError
                except (TypeError, ValueError):
                    mark_unknown("assistant_lab_counts_invalid")
                else:
                    core_valid = True
                    if any(core_counts):
                        mark_busy(
                            "assistant_lab_active:"
                            f"jobs={core_counts[0]},research={core_counts[1]},"
                            f"research_children={core_counts[2]},control={core_counts[3]}"
                        )
        if core_valid:
            video: Any = None
            try:
                with psycopg.connect(queue_dsn, connect_timeout=8, application_name="oracle-idle-video-queue") as connection:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT 'universal-video-idle-v1', "
                            "floor(extract(epoch FROM clock_timestamp()))::bigint, count(*)::bigint "
                            "FROM video_queue.job_status "
                            "WHERE status IN ('PENDING_CANARY','QUEUED','LEASED')"
                        )
                        video = cursor.fetchone()
            except Exception:
                mark_unknown("video_queue_telemetry_unavailable_after_assistant_lab_success")
            if video is None:
                mark_unknown("video_queue_telemetry_empty_after_assistant_lab_success")
            else:
                if len(video) != 3 or video[0] != "universal-video-idle-v1":
                    mark_unknown("video_queue_telemetry_invalid")
                elif not fresh(video[1]):
                    mark_unknown("video_queue_telemetry_stale")
                else:
                    try:
                        video_count = int(video[2])
                        if video_count < 0:
                            raise ValueError
                    except (TypeError, ValueError):
                        mark_unknown("video_queue_count_invalid")
                    else:
                        if video_count:
                            mark_busy(f"universal_video_active:{video_count}")

if unknown:
    final_state = "UNKNOWN"
    reasons = unknown + busy
elif busy:
    final_state = "BUSY"
    reasons = busy
else:
    final_state = "IDLE"
    reasons = ["all_required_sources_proved_idle"]
print("ORACLE_IDLE_REASON=" + ";".join(reasons))
print("ORACLE_IDLE_STATE=" + final_state)
PY
)"
python_rc=$?
set -e

if ((python_rc != 0)) \
   || [[ "$(printf '%s\n' "$result" | grep -Ec '^ORACLE_IDLE_REASON=.+$' || true)" != "1" ]] \
   || [[ "$(printf '%s\n' "$result" | grep -Ec '^ORACLE_IDLE_STATE=(IDLE|BUSY|UNKNOWN)$' || true)" != "1" ]] \
   || [[ "$(printf '%s\n' "$result" | grep -Ec '.*' || true)" != "2" ]]; then
  printf 'ORACLE_IDLE_REASON=classifier_failed_or_malformed\n'
  printf 'ORACLE_IDLE_STATE=UNKNOWN\n'
  exit 0
fi
printf '%s\n' "$result"
exit 0
