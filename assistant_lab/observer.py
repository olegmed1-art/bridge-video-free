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
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EVIDENCE_LEVELS = frozenset({"DOCUMENTED", "OBSERVED", "INFERRED", "CONFIRMED", "UNKNOWN"})
_RESERVED_ENV = frozenset(
    {
        "HOME",
        "PATH",
        "PYTHONPATH",
        "TMPDIR",
        "ASSISTANT_LAB_EXPERIMENT_ID",
        "ASSISTANT_LAB_EXPERIMENT_ROOT",
        "ASSISTANT_LAB_SOURCE_PATH",
        "ASSISTANT_LAB_TOOL_OUTPUT_DIR",
    }
)


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
    source_root: Path | None = None
    archive_root: Path | None = None
    require_archive: bool = False
    sandbox_binary: str = "bwrap"
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

    @property
    def work(self) -> Path:
        return self.state_root / "work"

    @property
    def knowledge_updates(self) -> Path:
        return self.state_root / "knowledge-updates"

    @property
    def quarantine(self) -> Path:
        return self.state_root / "quarantine"

    def ensure(self) -> None:
        for path in (
            self.pending, self.running, self.done, self.failed, self.experiments,
            self.work, self.knowledge_updates, self.quarantine,
        ):
            path.mkdir(parents=True, exist_ok=True)
        if self.source_root is not None:
            self.source_root.mkdir(parents=True, exist_ok=True)
        if self.archive_root is not None:
            self.archive_root.mkdir(parents=True, exist_ok=True)


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
    tool_id = raw.get("tool_id")
    command = raw.get("command")
    if not isinstance(experiment_id, str) or not _ID_RE.fullmatch(experiment_id):
        raise ValueError("invalid experiment_id")
    if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
        raise ValueError("command must be a non-empty argv string array")
    if not isinstance(tool_id, str) or not _ID_RE.fullmatch(tool_id):
        raise ValueError("invalid tool_id")
    source = raw.get("source")
    if not isinstance(source, dict) or set(source) != {"path", "sha256"}:
        raise ValueError("source must contain exactly path and sha256")
    source_path = str(source.get("path") or "").strip()
    source_sha256 = str(source.get("sha256") or "").strip().lower()
    if not Path(source_path).is_absolute() or any(mark in source_path for mark in ("*", "?", "[", "]", "\x00")):
        raise ValueError("source.path must be one explicit absolute path")
    if not _SHA256_RE.fullmatch(source_sha256):
        raise ValueError("source.sha256 must pin the exact source bytes")
    timeout = raw.get("timeout_seconds")
    if timeout is not None and (not isinstance(timeout, int) or timeout < 1 or timeout > 86400):
        raise ValueError("timeout_seconds must be 1..86400")
    env = raw.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise ValueError("env must be a string map")
    forbidden_env = sorted(set(env) & _RESERVED_ENV)
    if forbidden_env:
        raise ValueError(f"reserved environment keys are forbidden: {forbidden_env}")
    return {
        "experiment_id": experiment_id,
        "tool_id": tool_id,
        "source": {"path": source_path, "sha256": source_sha256},
        "command": command,
        "timeout_seconds": timeout,
        "env": env,
        "label": str(raw.get("label", ""))[:256],
    }


def _prepare_source(config: ObserverConfig, job: dict[str, Any], input_dir: Path) -> Path:
    source = Path(job["source"]["path"]).resolve()
    if config.source_root is None:
        raise RuntimeError("observer source root is not configured")
    root = config.source_root.resolve()
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("source escapes configured observer source root") from exc
    if not source.is_file():
        raise RuntimeError("source is not a regular file")
    actual = _sha256(source)
    if actual != job["source"]["sha256"]:
        raise RuntimeError("source SHA-256 mismatch")
    destination = input_dir / "source" / source.name
    destination.parent.mkdir(parents=True, exist_ok=False)
    shutil.copy2(source, destination)
    if _sha256(destination) != actual:
        raise RuntimeError("isolated source copy SHA-256 mismatch")
    return destination


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _verify_checksum_manifest(root: Path) -> None:
    manifest = root / "checksums.sha256"
    for line in manifest.read_text(encoding="utf-8").splitlines():
        expected, separator, relative = line.partition("  ")
        if not separator or not _SHA256_RE.fullmatch(expected):
            raise RuntimeError("invalid checksum manifest")
        target = (root / relative).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError as exc:
            raise RuntimeError("checksum path escapes experiment") from exc
        if not target.is_file() or _sha256(target) != expected:
            raise RuntimeError(f"artifact checksum mismatch: {relative}")


def append_knowledge(
    config: ObserverConfig,
    experiment_id: str,
    level: str,
    statement: str,
    evidence_refs: list[str],
    limitations: str = "",
) -> Path:
    if not _ID_RE.fullmatch(experiment_id):
        raise ValueError("invalid experiment_id")
    normalized_level = level.strip().upper()
    if normalized_level not in EVIDENCE_LEVELS:
        raise ValueError("invalid evidence level")
    if not statement.strip() or len(statement) > 4000:
        raise ValueError("knowledge statement must be 1..4000 characters")
    sealed = config.experiments / experiment_id / "SEALED.json"
    if not sealed.is_file():
        raise RuntimeError("knowledge can only reference a sealed experiment")
    if not evidence_refs or not all(isinstance(x, str) and 0 < len(x) <= 500 for x in evidence_refs):
        raise ValueError("at least one bounded evidence reference is required")
    row = {
        "recorded_at": utc_now(),
        "experiment_id": experiment_id,
        "evidence_level": normalized_level,
        "statement": statement.strip(),
        "evidence_refs": evidence_refs,
        "limitations": limitations[:2000],
    }
    path = config.knowledge_updates / experiment_id / f"{normalized_level.lower()}.jsonl"
    _append_jsonl(path, row)
    if config.archive_root is not None:
        durable_path = config.archive_root / "knowledge-updates" / experiment_id / f"{normalized_level.lower()}.jsonl"
        _append_jsonl(durable_path, row)
    return path


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
    final_exp = config.experiments / experiment_id
    exp = config.work / experiment_id
    if exp.exists() or final_exp.exists():
        raise RuntimeError(f"experiment already exists: {experiment_id}")

    if config.require_archive and config.archive_root is None:
        raise RuntimeError("durable observer archive is required but not configured")
    hidden_roots = [config.experiments, config.quarantine, config.knowledge_updates]
    if config.archive_root is not None:
        hidden_roots.append(config.archive_root)
    if any(str(root.resolve()) in argument for root in hidden_roots for argument in job["command"]):
        raise RuntimeError("command may not reference observer result stores")
    sandbox = shutil.which(config.sandbox_binary)
    if sandbox is None:
        raise RuntimeError("bubblewrap is required for experiment filesystem isolation")

    dirs = {
        name: exp / name
        for name in ("input", "oracle_tool", "observer", "output", "telemetry", "logs", "knowledge", "tmp")
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)

    isolated_source = _prepare_source(config, job, dirs["input"])

    manifest = {
        "schema": "assistant-lab-observer/v0.2",
        "experiment_id": experiment_id,
        "tool_id": job["tool_id"],
        "label": job["label"],
        "created_at": utc_now(),
        "command": job["command"],
        "timeout_seconds": job["timeout_seconds"] or config.default_timeout_seconds,
        "source": {
            "original_path": job["source"]["path"],
            "isolated_path": str(isolated_source),
            "sha256": job["source"]["sha256"],
        },
        "separation": {
            "video_analyzer_result_consumed": False,
            "other_oracle_tool_results_consumed": False,
            "observer_output_is_separate": True,
            "single_source_copy_verified": True,
            "environment_sanitized": True,
            "shared_experiment_path_references_rejected": True,
            "shared_experiments_tree_hidden_by_mount_namespace": True,
            "durable_archive_hidden_by_mount_namespace": config.archive_root is not None,
        },
        "archive_required": config.require_archive,
        "archive_configured": config.archive_root is not None,
    }
    _json_dump(exp / "manifest.json", manifest)
    events = EventWriter(dirs["telemetry"] / "events.jsonl", experiment_id)
    events.write("experiment_started", command=job["command"], label=job["label"])

    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "HOME": str(dirs["oracle_tool"]),
        "TMPDIR": str(dirs["tmp"]),
    }
    env.update(job["env"])
    env.update({
        "ASSISTANT_LAB_EXPERIMENT_ID": experiment_id,
        "ASSISTANT_LAB_EXPERIMENT_ROOT": str(exp),
        "ASSISTANT_LAB_SOURCE_PATH": str(isolated_source),
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
        sandboxed_command = [
            sandbox,
            "--die-with-parent",
            "--unshare-all",
            "--share-net",
            "--ro-bind", "/", "/",
            "--dev", "/dev",
            "--proc", "/proc",
            "--tmpfs", str(config.experiments.resolve()),
            "--tmpfs", str(config.quarantine.resolve()),
            "--tmpfs", str(config.knowledge_updates.resolve()),
            "--bind", str(exp.resolve()), str(exp.resolve()),
        ]
        if config.archive_root is not None:
            sandboxed_command.extend(["--tmpfs", str(config.archive_root.resolve())])
        sandboxed_command.extend(["--chdir", str(dirs["oracle_tool"].resolve()), "--", *job["command"]])
        process = subprocess.Popen(
            sandboxed_command,
            cwd=dirs["oracle_tool"],
            env=env,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        events.write("process_started", pid=process.pid)
        try:
            ps_proc = psutil.Process(process.pid) if psutil is not None else None
        except psutil.NoSuchProcess:
            ps_proc = None
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

    archive_location = config.archive_root / experiment_id if config.archive_root is not None else None
    report = {
        "schema": "assistant-lab-observer-report/v0.2",
        "experiment_id": experiment_id,
        "tool_id": job["tool_id"],
        "source_sha256": job["source"]["sha256"],
        "finished_at": utc_now(),
        "exit_code": return_code,
        "timed_out": timed_out,
        "duration_seconds": round(duration, 3),
        "max_rss_bytes_observed": max_rss,
        "max_cpu_percent_sum_observed": round(max_cpu, 3),
        "unique_network_connections_observed": len(seen_connections),
        "artifacts": artifacts,
        "tool_result_location": str(final_exp / "output"),
        "observer_result_location": str(final_exp / "observer"),
        "sealed": True,
        "archive_status": "PENDING" if archive_location is not None else "NOT_CONFIGURED",
        "archive_location": str(archive_location) if archive_location is not None else None,
    }
    _json_dump(dirs["observer"] / "observer_report.json", report)
    events.write("experiment_finished", exit_code=return_code, timed_out=timed_out, duration_seconds=report["duration_seconds"])

    observed = {
        "recorded_at": utc_now(),
        "experiment_id": experiment_id,
        "evidence_level": "OBSERVED",
        "statement": "Observer recorded the bounded process execution and artifact inventory.",
        "evidence_refs": ["telemetry/events.jsonl", "observer/observer_report.json"],
        "limitations": "Managed-service internals not exposed through logs or APIs remain unknown.",
    }
    _append_jsonl(dirs["knowledge"] / "observed.jsonl", observed)

    checksum_rows: list[str] = []
    for path in sorted(exp.rglob("*")):
        if path.is_file() and path.name not in {"checksums.sha256", "SEALED.json"}:
            checksum_rows.append(f"{_sha256(path)}  {path.relative_to(exp)}")
    (exp / "checksums.sha256").write_text("\n".join(checksum_rows) + "\n", encoding="utf-8")
    seal = {
        "schema": "assistant-lab-observer-seal/v0.2",
        "experiment_id": experiment_id,
        "tool_id": job["tool_id"],
        "source_sha256": job["source"]["sha256"],
        "sealed_at": utc_now(),
        "status": "SEALED",
        "checksums_sha256": _sha256(exp / "checksums.sha256"),
    }
    _json_dump(exp / "SEALED.json", seal)

    if archive_location is not None:
        if archive_location.exists():
            raise RuntimeError("durable archive experiment already exists")
        temporary_archive = config.archive_root / f".{experiment_id}.{os.getpid()}.tmp"
        shutil.copytree(exp, temporary_archive)
        if _sha256(temporary_archive / "checksums.sha256") != seal["checksums_sha256"]:
            raise RuntimeError("durable archive checksum manifest mismatch")
        _verify_checksum_manifest(temporary_archive)
        temporary_archive.rename(archive_location)
    exp.rename(final_exp)
    if archive_location is not None:
        report["archive_status"] = "COPIED"
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
    if config.require_archive and config.archive_root is None:
        raise RuntimeError("durable observer archive is required but not configured")
    config.ensure()
    while True:
        claimed = _claim_next(config)
        if claimed is None:
            if once:
                return 0
            time.sleep(config.poll_seconds)
            continue
        destination = config.done / claimed.name
        job: dict[str, Any] | None = None
        try:
            raw = json.loads(claimed.read_text(encoding="utf-8"))
            job = validate_job(raw)
            report = run_experiment(config, job)
            destination = config.done / claimed.name
            _json_dump(destination.with_suffix(".result.json"), report)
        except Exception as exc:
            destination = config.failed / claimed.name
            _json_dump(destination.with_suffix(".error.json"), {"failed_at": utc_now(), "error": f"{type(exc).__name__}: {exc}"})
            if job is not None:
                partial = config.work / job["experiment_id"]
                quarantine = config.quarantine / job["experiment_id"]
                if partial.exists() and not quarantine.exists():
                    partial.rename(quarantine)
        finally:
            shutil.move(str(claimed), str(destination))
        if once:
            return 0


def submit_job(
    config: ObserverConfig,
    argv: list[str],
    experiment_id: str | None,
    timeout: int,
    label: str,
    tool_id: str,
    source_path: str,
    source_sha256: str,
) -> Path:
    config.ensure()
    exp_id = experiment_id or datetime.now(timezone.utc).strftime("EXP-%Y%m%d-%H%M%S-%f")
    job = validate_job({
        "experiment_id": exp_id,
        "tool_id": tool_id,
        "source": {"path": source_path, "sha256": source_sha256},
        "command": argv,
        "timeout_seconds": timeout,
        "label": label,
    })
    path = config.pending / f"{exp_id}.json"
    tmp = config.pending / f".{exp_id}.{os.getpid()}.tmp"
    if path.exists() or (config.experiments / exp_id).exists():
        raise RuntimeError(f"experiment already exists or is queued: {exp_id}")
    _json_dump(tmp, job)
    tmp.rename(path)
    return path


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assistant Lab Oracle Observer v0.2")
    parser.add_argument("--state-root", default=os.getenv("ASSISTANT_LAB_OBSERVER_STATE_ROOT", "/opt/bridge-school/assistant-lab-observer"))
    parser.add_argument("--source-root", default=os.getenv("ASSISTANT_LAB_OBSERVER_SOURCE_ROOT"))
    parser.add_argument("--archive-root", default=os.getenv("ASSISTANT_LAB_OBSERVER_ARCHIVE_ROOT"))
    parser.add_argument(
        "--require-archive",
        action="store_true",
        default=os.getenv("ASSISTANT_LAB_OBSERVER_REQUIRE_ARCHIVE", "0") == "1",
    )
    sub = parser.add_subparsers(dest="mode", required=True)
    daemon = sub.add_parser("daemon")
    daemon.add_argument("--once", action="store_true")
    submit = sub.add_parser("submit")
    submit.add_argument("--experiment-id")
    submit.add_argument("--timeout", type=int, default=3600)
    submit.add_argument("--label", default="")
    submit.add_argument("--tool-id", required=True)
    submit.add_argument("--source", required=True)
    submit.add_argument("--source-sha256", required=True)
    submit.add_argument("command", nargs=argparse.REMAINDER)
    note = sub.add_parser("note")
    note.add_argument("--experiment-id", required=True)
    note.add_argument("--level", required=True, choices=sorted(EVIDENCE_LEVELS))
    note.add_argument("--statement", required=True)
    note.add_argument("--evidence-ref", action="append", required=True)
    note.add_argument("--limitations", default="")
    args = parser.parse_args(list(argv) if argv is not None else None)
    config = ObserverConfig(
        Path(args.state_root),
        source_root=Path(args.source_root) if args.source_root else None,
        archive_root=Path(args.archive_root) if args.archive_root else None,
        require_archive=args.require_archive,
    )
    if args.mode == "daemon":
        return run_daemon(config, once=args.once)
    if args.mode == "note":
        config.ensure()
        path = append_knowledge(
            config, args.experiment_id, args.level, args.statement, args.evidence_ref, args.limitations
        )
        print(path)
        return 0
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("submit requires a command after --")
    path = submit_job(
        config, command, args.experiment_id, args.timeout, args.label,
        args.tool_id, args.source, args.source_sha256,
    )
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
