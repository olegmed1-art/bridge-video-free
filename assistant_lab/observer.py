from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import psutil  # type: ignore
except ImportError:  # pragma: no cover - deployment preflight requires it
    psutil = None

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class ObserverConfig:
    state_root: Path
    poll_seconds: float = 2.0
    sample_seconds: float = 1.0
    default_timeout_seconds: int = 3600

    @property
    def pending(self) -> Path:
        return self.state_root / "jobs" / "pending"

    @property
    def running(self) -> Path:
        return self.state_root / "jobs" / "running"

    @property
    def done(self) -> Path:
        return self.state_root / "jobs" / "done"

    @property
    def failed(self) -> Path:
        return self.state_root / "jobs" / "failed"

    @property
    def experiments(self) -> Path:
        return self.state_root / "experiments"

    def ensure(self) -> None:
        for path in (self.pending, self.running, self.done, self.failed, self.experiments):
            path.mkdir(parents=True, exist_ok=True)


class EventWriter:
    def __init__(self, path: Path, experiment_id: str):
        self.path = path
        self.experiment_id = experiment_id

    def write(self, event: str, **fields: Any) -> None:
        row = {"ts": utc_now(), "experiment_id": self.experiment_id, "event": event, **fields}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def validate_job(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("job must be a JSON object")
    experiment_id = raw.get("experiment_id")
    command = raw.get("command")
    if not isinstance(experiment_id, str) or not _ID_RE.fullmatch(experiment_id):
        raise ValueError("invalid experiment_id")
    if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
        raise ValueError("command must be a non-empty argv string array")
    timeout = raw.get("timeout_seconds")
    if timeout is not None and (not isinstance(timeout, int) or timeout < 1 or timeout > 86400):
        raise ValueError("timeout_seconds must be 1..86400")
    env = raw.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise ValueError("env must be a string map")
    return {
        "experiment_id": experiment_id,
        "command": command,
        "timeout_seconds": timeout,
        "env": env,
        "label": str(raw.get("label", ""))[:256],
    }


def _snapshot_files(root: Path) -> dict[str, tuple[int, int]]:
    result: dict[str, tuple[int, int]] = {}
    if not root.exists():
        return result
    for path in root.rglob("*"):
        if path.is_file():
            try:
                st = path.stat()
            except FileNotFoundError:
                continue
            result[str(path.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return result


def _process_snapshot(proc: Any) -> dict[str, Any]:
    if psutil is None:
        return {}
    try:
        family = [proc, *proc.children(recursive=True)]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        family = [proc]
    rows = []
    rss = 0
    cpu = 0.0
    connections: list[dict[str, Any]] = []
    for p in family:
        try:
            mem = p.memory_info().rss
            rss += mem
            pcpu = p.cpu_percent(interval=None)
            cpu += pcpu
            rows.append({"pid": p.pid, "ppid": p.ppid(), "name": p.name(), "rss": mem, "cpu_percent": pcpu})
            try:
                for conn in p.net_connections(kind="inet"):
                    connections.append({
                        "pid": p.pid,
                        "family": int(conn.family),
                        "type": int(conn.type),
                        "local": str(conn.laddr) if conn.laddr else None,
                        "remote": str(conn.raddr) if conn.raddr else None,
                        "status": conn.status,
                    })
            except (psutil.AccessDenied, AttributeError):
                pass
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return {"processes": rows, "rss_bytes": rss, "cpu_percent_sum": cpu, "connections": connections}


def run_experiment(config: ObserverConfig, job: dict[str, Any]) -> dict[str, Any]:
    experiment_id = job["experiment_id"]
    exp = config.experiments / experiment_id
    if exp.exists():
        raise RuntimeError(f"experiment already exists: {experiment_id}")

    dirs = {name: exp / name for name in ("input", "oracle_tool", "observer", "output", "telemetry", "logs")}
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema": "assistant-lab-observer/v0.1",
        "experiment_id": experiment_id,
        "label": job["label"],
        "created_at": utc_now(),
        "command": job["command"],
        "timeout_seconds": job["timeout_seconds"] or config.default_timeout_seconds,
        "separation": {
            "video_analyzer_result_consumed": False,
            "other_oracle_tool_results_consumed": False,
            "observer_output_is_separate": True,
        },
    }
    _json_dump(exp / "manifest.json", manifest)
    events = EventWriter(dirs["telemetry"] / "events.jsonl", experiment_id)
    events.write("experiment_started", command=job["command"], label=job["label"])

    env = os.environ.copy()
    env.update(job["env"])
    env.update({
        "ASSISTANT_LAB_EXPERIMENT_ID": experiment_id,
        "ASSISTANT_LAB_EXPERIMENT_ROOT": str(exp),
        "ASSISTANT_LAB_TOOL_OUTPUT_DIR": str(dirs["output"]),
    })

    stdout_path = dirs["logs"] / "stdout.log"
    stderr_path = dirs["logs"] / "stderr.log"
    start = time.monotonic()
    timed_out = False
    max_rss = 0
    max_cpu = 0.0
    seen_connections: set[str] = set()
    last_files = _snapshot_files(dirs["oracle_tool"])

    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(
            job["command"],
            cwd=dirs["oracle_tool"],
            env=env,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        events.write("process_started", pid=process.pid)
        ps_proc = psutil.Process(process.pid) if psutil is not None else None
        if ps_proc is not None:
            try:
                ps_proc.cpu_percent(interval=None)
            except psutil.Error:
                pass

        timeout = manifest["timeout_seconds"]
        while process.poll() is None:
            elapsed = time.monotonic() - start
            if elapsed > timeout:
                timed_out = True
                events.write("timeout", elapsed_seconds=round(elapsed, 3), timeout_seconds=timeout)
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                break

            sample = _process_snapshot(ps_proc) if ps_proc is not None else {}
            if sample:
                max_rss = max(max_rss, int(sample.get("rss_bytes", 0)))
                max_cpu = max(max_cpu, float(sample.get("cpu_percent_sum", 0.0)))
                events.write("resource_sample", **sample)
                for conn in sample.get("connections", []):
                    key = json.dumps(conn, sort_keys=True)
                    if key not in seen_connections:
                        seen_connections.add(key)
                        events.write("network_connection_seen", **conn)

            current_files = _snapshot_files(dirs["oracle_tool"])
            for rel, state in current_files.items():
                if rel not in last_files:
                    events.write("file_created", path=rel, size=state[0])
                elif state != last_files[rel]:
                    events.write("file_modified", path=rel, size=state[0])
            for rel in last_files.keys() - current_files.keys():
                events.write("file_deleted", path=rel)
            last_files = current_files
            time.sleep(config.sample_seconds)

        return_code = process.wait()

    duration = time.monotonic() - start
    artifacts: list[dict[str, Any]] = []
    for root in (dirs["oracle_tool"], dirs["output"]):
        for path in root.rglob("*"):
            if path.is_file():
                artifacts.append({
                    "scope": root.name,
                    "path": str(path.relative_to(root)),
                    "size": path.stat().st_size,
                    "sha256": _sha256(path),
                })

    report = {
        "schema": "assistant-lab-observer-report/v0.1",
        "experiment_id": experiment_id,
        "finished_at": utc_now(),
        "exit_code": return_code,
        "timed_out": timed_out,
        "duration_seconds": round(duration, 3),
        "max_rss_bytes_observed": max_rss,
        "max_cpu_percent_sum_observed": round(max_cpu, 3),
        "unique_network_connections_observed": len(seen_connections),
        "artifacts": artifacts,
        "tool_result_location": str(dirs["output"]),
        "observer_result_location": str(dirs["observer"]),
    }
    _json_dump(dirs["observer"] / "observer_report.json", report)
    events.write("experiment_finished", exit_code=return_code, timed_out=timed_out, duration_seconds=report["duration_seconds"])
    return report


def _claim_next(config: ObserverConfig) -> Path | None:
    for pending in sorted(config.pending.glob("*.json")):
        claimed = config.running / pending.name
        try:
            pending.rename(claimed)
            return claimed
        except FileNotFoundError:
            continue
    return None


def run_daemon(config: ObserverConfig, once: bool = False) -> int:
    if psutil is None:
        raise RuntimeError("psutil is required for Assistant Lab Observer")
    config.ensure()
    while True:
        claimed = _claim_next(config)
        if claimed is None:
            if once:
                return 0
            time.sleep(config.poll_seconds)
            continue
        destination = config.done / claimed.name
        try:
            raw = json.loads(claimed.read_text(encoding="utf-8"))
            job = validate_job(raw)
            report = run_experiment(config, job)
            destination = config.done / claimed.name
            _json_dump(destination.with_suffix(".result.json"), report)
        except Exception as exc:
            destination = config.failed / claimed.name
            _json_dump(destination.with_suffix(".error.json"), {"failed_at": utc_now(), "error": f"{type(exc).__name__}: {exc}"})
        finally:
            shutil.move(str(claimed), str(destination))
        if once:
            return 0


def submit_job(config: ObserverConfig, argv: list[str], experiment_id: str | None, timeout: int, label: str) -> Path:
    config.ensure()
    exp_id = experiment_id or datetime.now(timezone.utc).strftime("EXP-%Y%m%d-%H%M%S-%f")
    job = validate_job({"experiment_id": exp_id, "command": argv, "timeout_seconds": timeout, "label": label})
    path = config.pending / f"{exp_id}.json"
    tmp = config.pending / f".{exp_id}.{os.getpid()}.tmp"
    if path.exists() or (config.experiments / exp_id).exists():
        raise RuntimeError(f"experiment already exists or is queued: {exp_id}")
    _json_dump(tmp, job)
    tmp.rename(path)
    return path


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assistant Lab Oracle Observer v0.1")
    parser.add_argument("--state-root", default=os.getenv("ASSISTANT_LAB_OBSERVER_STATE_ROOT", "/opt/bridge-school/assistant-lab-observer"))
    sub = parser.add_subparsers(dest="mode", required=True)
    daemon = sub.add_parser("daemon")
    daemon.add_argument("--once", action="store_true")
    submit = sub.add_parser("submit")
    submit.add_argument("--experiment-id")
    submit.add_argument("--timeout", type=int, default=3600)
    submit.add_argument("--label", default="")
    submit.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(list(argv) if argv is not None else None)
    config = ObserverConfig(Path(args.state_root))
    if args.mode == "daemon":
        return run_daemon(config, once=args.once)
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("submit requires a command after --")
    path = submit_job(config, command, args.experiment_id, args.timeout, args.label)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
