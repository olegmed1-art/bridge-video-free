#!/usr/bin/env bash
set -Eeuo pipefail

# Read-only Oracle state classifier. It never stops, restarts, or mutates work.
# IDLE is emitted only when every required source freshly proves no work.
# Any unavailable, stale, malformed, or only partially checked source is UNKNOWN.

LAB_DIR="${ASSISTANT_LAB_DIR:-/opt/bridge-school/assistant-lab}"
PYTHON="${ASSISTANT_LAB_PYTHON:-$LAB_DIR/.venv/bin/python}"
export ASSISTANT_LAB_ENV_FILE="${ASSISTANT_LAB_ENV_FILE:-$LAB_DIR/assistant-lab.env}"
export BRIDGE_VIDEO_QUEUE_DSN_FILE="${BRIDGE_VIDEO_QUEUE_DSN_FILE:-/opt/bridge-school/universal-video/secrets/video-queue-dsn}"
export BRIDGE_VIDEO_SPOOL_ROOT="${BRIDGE_VIDEO_SPOOL_ROOT:-/opt/bridge-school/universal-video/spool}"
export ORACLE_HOST_LEASE_FILE="${ORACLE_HOST_LEASE_FILE:-/run/bridge-school/oracle-host-lease}"
export ORACLE_IDLE_SPOOL_PATHS="${ORACLE_IDLE_SPOOL_PATHS:-/opt/bridge-school/assistant-lab/spool:/opt/bridge-school/assistant-lab/feedback-spool:/var/lib/bridge-school/uv-spool:/var/lib/bridge-school/feedback-spool}"
export ORACLE_IDLE_MAX_TELEMETRY_AGE_SECONDS="${ORACLE_IDLE_MAX_TELEMETRY_AGE_SECONDS:-120}"
export ORACLE_IDLE_MAX_LEASE_SECONDS="${ORACLE_IDLE_MAX_LEASE_SECONDS:-3600}"
export ORACLE_IDLE_CLOCK_SKEW_SECONDS="${ORACLE_IDLE_CLOCK_SKEW_SECONDS:-5}"

if [[ ! -x "$PYTHON" ]]; then
  printf 'ORACLE_IDLE_REASON=assistant_lab_python_missing\n'
  printf 'ORACLE_IDLE_STATE=UNKNOWN\n'
  exit 0
fi

set +e
result="$("$PYTHON" - <<'PY' 2>/dev/null
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
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


def bounded_int(name: str, *, minimum: int, maximum: int) -> int | None:
    raw = os.environ.get(name, "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        mark_unknown(f"invalid_config:{name}")
        return None
    if not minimum <= value <= maximum:
        mark_unknown(f"invalid_config:{name}")
        return None
    return value


max_age = bounded_int("ORACLE_IDLE_MAX_TELEMETRY_AGE_SECONDS", minimum=1, maximum=3600)
max_lease = bounded_int("ORACLE_IDLE_MAX_LEASE_SECONDS", minimum=60, maximum=86400)
clock_skew = bounded_int("ORACLE_IDLE_CLOCK_SKEW_SECONDS", minimum=0, maximum=60)
now = int(time.time())


def fresh(value: Any) -> bool:
    if max_age is None or clock_skew is None or isinstance(value, bool):
        return False
    try:
        epoch = int(value)
    except (TypeError, ValueError):
        return False
    return now - max_age <= epoch <= now + clock_skew


def run_probe(argv: list[str], *, timeout: int = 5) -> subprocess.CompletedProcess[str] | None:
    if shutil.which(argv[0]) is None:
        mark_unknown(f"{argv[0]}_missing")
        return None
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        mark_unknown(f"{argv[0]}_unavailable")
        return None


service = run_probe(["systemctl", "is-active", "assistant-lab.service"])
if service is not None and (service.returncode != 0 or service.stdout.strip() != "active"):
    mark_unknown(f"assistant_lab_service_{service.stdout.strip() or 'unknown'}")

observer = run_probe([
    "pgrep",
    "-f",
    r"[a]ssistant_lab.*observer.*experiment|[o]racle_assistant_lab_observer.*run",
])
if observer is not None:
    if observer.returncode == 0:
        mark_busy("observer_experiment_process")
    elif observer.returncode != 1:
        mark_unknown("process_telemetry_unavailable")


def checked_dir(path: Path, reason: str) -> bool:
    try:
        info = path.lstat()
    except (FileNotFoundError, PermissionError, OSError):
        mark_unknown(reason)
        return False
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        mark_unknown(reason)
        return False
    return True


def scan_generic_spool(path: Path) -> None:
    if not checked_dir(path, f"spool_unavailable:{path}"):
        return
    stack = [path]
    try:
        while stack:
            current = stack.pop()
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.is_symlink():
                        mark_unknown(f"spool_entry_unsafe:{path}")
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        mark_busy(f"pending_spool:{path}")
                    else:
                        mark_unknown(f"spool_entry_unsafe:{path}")
    except (PermissionError, OSError):
        mark_unknown(f"spool_enumeration_failed:{path}")


spool_raw = os.environ.get("ORACLE_IDLE_SPOOL_PATHS", "")
spool_paths = [Path(item) for item in spool_raw.split(":") if item]
if not spool_paths:
    mark_unknown("spool_configuration_empty")
for spool_path in spool_paths:
    scan_generic_spool(spool_path)

# Universal Video host spool is a separate mandatory family. Only terminal
# progress receipts may remain without making the host busy.
video_root = Path(os.environ.get("BRIDGE_VIDEO_SPOOL_ROOT", ""))
active_progress = {"DOWNLOADING_FROM_DRIVE", "SOURCE_READY_ON_ORACLE", "PROCESSING"}
terminal_progress = {"RESULT_READY", "REVIEW", "FAILED"}
if checked_dir(video_root, "video_spool_unavailable"):
    video_leaves: dict[str, Path] = {}
    for leaf in ("inbox", "running", "progress"):
        candidate = video_root / leaf
        if checked_dir(candidate, f"video_spool_{leaf}_unavailable"):
            video_leaves[leaf] = candidate
    for leaf in ("inbox", "running"):
        path = video_leaves.get(leaf)
        if path is None:
            continue
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                        mark_unknown(f"video_spool_{leaf}_entry_invalid")
                    elif not entry.name.endswith(".json"):
                        mark_unknown(f"video_spool_{leaf}_entry_unknown")
                    else:
                        mark_busy(f"video_spool_{leaf}_pending")
        except (PermissionError, OSError):
            mark_unknown(f"video_spool_{leaf}_traversal_failed")
    progress = video_leaves.get("progress")
    if progress is not None:
        try:
            with os.scandir(progress) as entries:
                for entry in entries:
                    if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                        mark_unknown("video_spool_progress_entry_invalid")
                        continue
                    if not entry.name.endswith(".json"):
                        mark_unknown("video_spool_progress_entry_unknown")
                        continue
                    try:
                        payload = json.loads(Path(entry.path).read_text(encoding="utf-8"))
                    except (OSError, UnicodeError, json.JSONDecodeError):
                        mark_unknown("video_spool_progress_invalid")
                        continue
                    if not isinstance(payload, dict) or payload.get("schema") != "universal-video-pipeline-progress-v1":
                        mark_unknown("video_spool_progress_invalid")
                        continue
                    progress_state = str(payload.get("state") or "")
                    if progress_state in active_progress:
                        if not fresh(payload.get("observed_at_unix")):
                            mark_unknown("video_spool_progress_stale")
                        else:
                            mark_busy("video_spool_progress_active")
                    elif progress_state not in terminal_progress:
                        mark_unknown("video_spool_progress_state_unknown")
        except (PermissionError, OSError):
            mark_unknown("video_spool_progress_traversal_failed")

# A missing lease is a proved absence only if its parent can be inspected.
lease_path = Path(os.environ.get("ORACLE_HOST_LEASE_FILE", ""))
lease_parent = lease_path.parent
if checked_dir(lease_parent, "host_lease_directory_unavailable"):
    try:
        lease_info = lease_path.lstat()
    except FileNotFoundError:
        lease_info = None
    except (PermissionError, OSError):
        mark_unknown("host_lease_unreadable")
        lease_info = None
    if lease_info is not None:
        if stat.S_ISLNK(lease_info.st_mode) or not stat.S_ISREG(lease_info.st_mode):
            mark_unknown("host_lease_unreadable")
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
        if core is not None:
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
            if video is not None:
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
   || [[ "$(printf '%s\n' "$result" | grep -Ec '^ORACLE_IDLE_REASON=' || true)" != "1" ]] \
   || [[ "$(printf '%s\n' "$result" | grep -Ec '^ORACLE_IDLE_STATE=(IDLE|BUSY|UNKNOWN)$' || true)" != "1" ]] \
   || [[ "$(printf '%s\n' "$result" | grep -Ec '.*' || true)" != "2" ]]; then
  printf 'ORACLE_IDLE_REASON=classifier_failed_or_malformed\n'
  printf 'ORACLE_IDLE_STATE=UNKNOWN\n'
  exit 0
fi
printf '%s\n' "$result"
exit 0
