#!/usr/bin/env python3
"""Read-only production queue proofs for the Issue #881 pre-canary gate."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlsplit


PROJECT = "misty-poetry-18012774"
BRANCH = "br-wispy-lab-b1rq54of"
DATABASE = "neondb"
OWNER = "neondb_owner"
RUNTIME_PRINCIPAL = "bridge_school_worker_principal"
RUNTIME_HOST = "ep-noisy-pine-b1pe30sf-pooler.c-5.eu-central-1.aws.neon.tech"
OWNER_HOSTS = {
    RUNTIME_HOST,
    RUNTIME_HOST.replace("-pooler.c-5", ".c-5"),
}
RUNTIME_DSN_FILE = Path("/run/secrets/video-queue-dsn")
BASELINE_SCHEMA = "issue881-precanary-owner-baseline-v1"
OWNER_RELEASE_LOCK_SQL = (
    "LOCK TABLE video_queue.batch, video_queue.job, video_queue.job_event "
    "IN SHARE MODE NOWAIT"
)
OWNER_RELEASE_SIGNAL = b"RELEASE\n"
OWNER_ABORT_SIGNAL = b"ABORT\n"


class QueueProofError(RuntimeError):
    """The selected database, credential, queue, or state is not exact and safe."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise QueueProofError(message)


def _validated_dsn(value: str, *, principal: str, hosts: set[str]) -> str:
    _require(0 < len(value) <= 4096, "unsafe DSN size")
    _require(
        not any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in value
        ),
        "malformed DSN",
    )
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise QueueProofError("malformed DSN authority") from exc
    _require(parsed.scheme in {"postgres", "postgresql"}, "wrong DSN scheme")
    _require(parsed.hostname in hosts and port in {None, 5432}, "wrong DSN host")
    _require(parsed.username == principal and bool(parsed.password), "wrong DSN principal")
    _require(parsed.path == f"/{DATABASE}" and not parsed.fragment, "wrong DSN database")
    try:
        pairs = parse_qsl(parsed.query, strict_parsing=True, keep_blank_values=True)
    except ValueError as exc:
        raise QueueProofError("malformed DSN options") from exc
    _require(len(pairs) == len(dict(pairs)), "duplicate DSN option")
    options = dict(pairs)
    _require(set(options) <= {"sslmode", "channel_binding"}, "unexpected DSN option")
    _require(
        options.get("sslmode") in {"require", "verify-ca", "verify-full"},
        "unsafe TLS mode",
    )
    _require(options.get("channel_binding") == "require", "channel binding is not required")
    return value


def _runtime_dsn() -> str:
    _require(not os.getenv("BRIDGE_VIDEO_QUEUE_DATABASE_URL", "").strip(), "direct DSN override")
    _require(not os.getenv("BRIDGE_WORKER_DATABASE_URL", "").strip(), "worker DSN override")
    configured = os.getenv("BRIDGE_VIDEO_QUEUE_DATABASE_URL_FILE", "").strip()
    _require(configured == str(RUNTIME_DSN_FILE), "runtime DSN file path mismatch")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(RUNTIME_DSN_FILE, flags)
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            _require(stat.S_ISREG(metadata.st_mode), "DSN is not a regular file")
            _require(
                metadata.st_uid == 0 and stat.S_IMODE(metadata.st_mode) == 0o640,
                "unsafe DSN metadata",
            )
            _require(
                metadata.st_nlink == 1 and 0 < metadata.st_size <= 4096,
                "unsafe DSN size or links",
            )
            raw = source.read(4097)
        _require(0 < len(raw) <= 4096, "unsafe DSN read size")
        value = raw.decode("utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise QueueProofError("runtime DSN is unreadable") from exc
    return _validated_dsn(value, principal=RUNTIME_PRINCIPAL, hosts={RUNTIME_HOST})


def _database_snapshot_on_connection(connection: Any, *, owner: bool) -> dict[str, Any]:
    from psycopg.rows import dict_row

    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SET LOCAL statement_timeout='10s'")
        cursor.execute("SET LOCAL lock_timeout='2s'")
        cursor.execute(
            """SELECT current_setting('neon.project_id', true) AS project,
                      current_setting('neon.branch_id', true) AS branch,
                      current_database() AS database,
                      current_user AS principal,
                      EXISTS (SELECT 1 FROM pg_namespace
                              WHERE nspname='video_queue') AS schema_exists,
                      to_regprocedure('video_queue.precanary_idle_snapshot()')
                          IS NOT NULL AS function_exists"""
        )
        identity = cursor.fetchone()
        _require(identity is not None, "database identity is missing")
        cursor.execute(
            """SELECT claimable_jobs AS claimable, leased_jobs AS leased
                 FROM video_queue.precanary_idle_snapshot()"""
        )
        idle = cursor.fetchone()
        _require(idle is not None and len(idle) == 2, "idle snapshot is missing")
        result = dict(identity)
        result["claimable"] = idle["claimable"]
        result["leased"] = idle["leased"]
        if owner:
            cursor.execute(
                """SELECT
                    (SELECT count(*) FROM video_queue.batch) AS batches,
                    (SELECT count(*) FROM video_queue.job) AS jobs,
                    (SELECT count(*) FROM video_queue.job_event) AS events,
                    (SELECT max(event_id) FROM video_queue.job_event) AS max_event_id,
                    (SELECT last_value FROM video_queue.job_event_event_id_seq)
                        AS sequence_last_value,
                    (SELECT is_called FROM video_queue.job_event_event_id_seq)
                        AS sequence_is_called"""
            )
            queue_state = cursor.fetchone()
            _require(queue_state is not None, "owner queue state is missing")
            result.update(dict(queue_state))
        return result


def _database_snapshot(dsn: str, *, owner: bool) -> dict[str, Any]:
    try:
        import psycopg

        with psycopg.connect(
            dsn,
            connect_timeout=8,
            application_name=(
                "issue881-precanary-owner-proof" if owner else "issue881-precanary-runtime-proof"
            ),
        ) as connection:
            connection.read_only = True
            return _database_snapshot_on_connection(connection, owner=owner)
    except QueueProofError:
        raise
    except Exception as exc:
        raise QueueProofError("read-only production query failed") from exc


def validate_runtime_snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "project": PROJECT,
        "branch": BRANCH,
        "database": DATABASE,
        "principal": RUNTIME_PRINCIPAL,
        "schema_exists": True,
        "function_exists": True,
        "claimable": 0,
        "leased": 0,
    }
    _require(type(value) is dict and set(value) == set(expected), "runtime snapshot shape mismatch")
    _require(
        all(type(value[key]) is type(expected_value) and value[key] == expected_value
            for key, expected_value in expected.items()),
        "runtime snapshot does not identify an idle production queue",
    )
    return dict(value)


def validate_owner_snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "project": PROJECT,
        "branch": BRANCH,
        "database": DATABASE,
        "principal": OWNER,
        "schema_exists": True,
        "function_exists": True,
        "claimable": 0,
        "leased": 0,
        "batches": 0,
        "jobs": 0,
        "events": 0,
        "max_event_id": None,
        "sequence_last_value": 1,
        "sequence_is_called": False,
    }
    _require(type(value) is dict and set(value) == set(expected), "owner snapshot shape mismatch")
    _require(
        all(type(value[key]) is type(expected_value) and value[key] == expected_value
            for key, expected_value in expected.items()),
        "owner snapshot does not prove a pristine production queue",
    )
    return dict(value)


def _owner_marker(label: str, snapshot: Mapping[str, Any], *, unchanged: bool = False) -> str:
    suffix = " unchanged=true" if unchanged else ""
    return (
        f"UNIVERSAL_VIDEO_PRECANARY_{label} project={snapshot['project']} "
        f"branch={snapshot['branch']} database={snapshot['database']} "
        f"principal={snapshot['principal']} schema=true function=true "
        f"batches={snapshot['batches']} jobs={snapshot['jobs']} "
        f"events={snapshot['events']} max_event_id=NULL "
        f"sequence_last_value={snapshot['sequence_last_value']} "
        f"sequence_is_called=false claimable={snapshot['claimable']} leased={snapshot['leased']}"
        f"{suffix} result=PASS"
    )


def _write_baseline(path: Path, snapshot: Mapping[str, Any]) -> None:
    payload = json.dumps(
        {"schema": BASELINE_SCHEMA, "snapshot": dict(snapshot)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _write_private_file(path: Path, payload: bytes) -> None:
    _require(path.is_absolute(), "control path is not absolute")
    _require(0 < len(payload) <= 4096, "control payload size is unsafe")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except OSError as exc:
        raise QueueProofError("cannot create private control file") from exc


def _read_control_signal(path: Path) -> bytes | None:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise QueueProofError("unsafe owner gate control") from exc
    try:
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            _require(
                stat.S_ISREG(metadata.st_mode)
                and metadata.st_uid == os.getuid()
                and stat.S_IMODE(metadata.st_mode) == 0o600
                and metadata.st_nlink == 1
                and 0 < metadata.st_size <= 16,
                "unsafe owner gate control metadata",
            )
            signal = source.read(17)
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    _require(signal in {OWNER_RELEASE_SIGNAL, OWNER_ABORT_SIGNAL}, "invalid owner gate control")
    return signal


def hold_owner_release_gate(
    connection: Any,
    *,
    baseline: Mapping[str, Any],
    ready_file: Path,
    control_file: Path,
    timeout_seconds: int,
) -> str:
    """Hold queue-table write locks through the resident release boundary."""

    _require(30 <= timeout_seconds <= 900, "owner gate timeout is unsafe")
    _require(ready_file.is_absolute() and control_file.is_absolute(), "control path is not absolute")
    _require(ready_file != control_file, "owner gate control paths overlap")
    _require(not ready_file.exists() and not ready_file.is_symlink(), "owner gate ready file exists")
    _require(not control_file.exists() and not control_file.is_symlink(), "owner gate control exists")
    connection.read_only = True
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL statement_timeout='10s'")
        cursor.execute("SET LOCAL lock_timeout='2s'")
        cursor.execute(OWNER_RELEASE_LOCK_SQL)
    first = validate_owner_snapshot(_database_snapshot_on_connection(connection, owner=True))
    _require(first == baseline, "locked owner snapshot differs from baseline")
    owner_marker = _owner_marker("POSTRESTORE_OWNER", first, unchanged=True)
    _write_private_file(ready_file, (owner_marker + "\n").encode("utf-8"))

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        signal = _read_control_signal(control_file)
        if signal is None:
            time.sleep(0.2)
            continue
        if signal == OWNER_ABORT_SIGNAL:
            connection.rollback()
            raise QueueProofError("owner release gate was aborted")
        final = validate_owner_snapshot(_database_snapshot_on_connection(connection, owner=True))
        _require(final == baseline, "final locked owner snapshot differs from baseline")
        connection.rollback()
        return (
            "UNIVERSAL_VIDEO_PRECANARY_DB_ENQUEUE_FENCE "
            "tables=batch,job,job_event lock=SHARE owner_release=observed "
            "final_snapshot=unchanged result=PASS"
        )
    connection.rollback()
    raise QueueProofError("owner release gate timed out")


def _owner_release_gate(
    dsn: str,
    *,
    baseline_path: Path,
    ready_file: Path,
    control_file: Path,
    timeout_seconds: int,
) -> str:
    try:
        import psycopg

        baseline = _read_baseline(baseline_path)
        with psycopg.connect(
            dsn,
            connect_timeout=8,
            application_name="issue881-precanary-owner-release-gate",
        ) as connection:
            return hold_owner_release_gate(
                connection,
                baseline=baseline,
                ready_file=ready_file,
                control_file=control_file,
                timeout_seconds=timeout_seconds,
            )
    except QueueProofError:
        raise
    except Exception as exc:
        raise QueueProofError("production owner release gate failed") from exc


def _read_baseline(path: Path) -> dict[str, Any]:
    _require(path.is_file() and not path.is_symlink(), "unsafe owner baseline")
    metadata = path.stat()
    _require(
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and stat.S_IMODE(metadata.st_mode) == 0o600
        and metadata.st_nlink == 1,
        "unsafe owner baseline metadata",
    )
    _require(0 < metadata.st_size <= 4096, "owner baseline size is unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_strict_json_object)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QueueProofError("invalid owner baseline") from exc
    _require(type(value) is dict and set(value) == {"schema", "snapshot"}, "baseline shape mismatch")
    _require(value["schema"] == BASELINE_SCHEMA, "baseline schema mismatch")
    return validate_owner_snapshot(value["snapshot"])


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        _require(key not in value, "duplicate baseline key")
        value[key] = item
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("runtime")
    before = subparsers.add_parser("owner-before")
    before.add_argument("--output", type=Path, required=True)
    after = subparsers.add_parser("owner-after")
    after.add_argument("--baseline", type=Path, required=True)
    release = subparsers.add_parser("owner-release-gate")
    release.add_argument("--baseline", type=Path, required=True)
    release.add_argument("--ready-file", type=Path, required=True)
    release.add_argument("--control-file", type=Path, required=True)
    release.add_argument("--timeout-seconds", type=int, default=600)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "runtime":
            snapshot = validate_runtime_snapshot(_database_snapshot(_runtime_dsn(), owner=False))
            print(
                f"project={snapshot['project']} branch={snapshot['branch']} "
                f"database={snapshot['database']} principal={snapshot['principal']} "
                "schema=true function=true claimable=0 leased=0"
            )
            return 0

        dsn = _validated_dsn(
            os.getenv("NEON_DATABASE_URL", "").strip(),
            principal=OWNER,
            hosts=OWNER_HOSTS,
        )
        if args.command == "owner-release-gate":
            print(
                _owner_release_gate(
                    dsn,
                    baseline_path=args.baseline,
                    ready_file=args.ready_file,
                    control_file=args.control_file,
                    timeout_seconds=args.timeout_seconds,
                )
            )
            return 0
        snapshot = validate_owner_snapshot(_database_snapshot(dsn, owner=True))
        if args.command == "owner-before":
            _write_baseline(args.output, snapshot)
            print(_owner_marker("OWNER_BEFORE", snapshot))
            return 0
        baseline = _read_baseline(args.baseline)
        _require(snapshot == baseline, "post-restore owner state differs from baseline")
        print(_owner_marker("POSTRESTORE_OWNER", snapshot, unchanged=True))
        return 0
    except QueueProofError as exc:
        print(f"UNIVERSAL_VIDEO_PRECANARY_QUEUE_PROOF_FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
