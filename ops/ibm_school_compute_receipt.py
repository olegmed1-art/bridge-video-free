#!/usr/bin/env python3
"""Offline, runner-only lifecycle evidence. Never starts or stops a provider.

A generated-world receipt precedes provider containment. This helper accepts a
complete lifecycle only when its captured process result, artifacts and terminal
provider observations agree. Missing cancellation evidence stays INCOMPLETE.
Raw controller logs and exception text are never copied to the public artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat

try:
    from . import ibm_school_worlds_probe as probe
    from . import ibm_worlds_validator as validator
except ImportError:
    import ibm_school_worlds_probe as probe
    import ibm_worlds_validator as validator

MAX_LOG = 1024 * 1024
MAX_FILE = 131072
QUEUE = "NOT_EXPOSED_BY_API"
EMPTY_SHA = hashlib.sha256(b"").hexdigest()
BINDING = {"run_id", "attempt", "head"}
TIMED = {"SCHOOL_WORLDS_ROUTE_PREPARED", "SCHOOL_WORLDS_CAPTURED", "SCHOOL_WORLDS_VERIFIED_RECEIPT"}
SHAPES = {
    "SCHOOL_WORLDS_ROUTE_PREPARED": BINDING | {"guest_network_requests"},
    "START_SUBMITTING": {"mode", "max_window_seconds", "running_window_seconds"},
    "START_ACCEPTED": set(), "TRIAL_STATE": {"status"},
    "SCHOOL_WORLDS_STARTED": BINDING | {"max_seconds", "seconds_since_start", "stop_submit_by_seconds", "confirmation_reserve_seconds"},
    "SCHOOL_WORLDS_CAPTURED": BINDING | {"remote_exit", "stdout_bytes", "stdout_sha256", "stderr_bytes", "stderr_sha256", "raw_output_exported"},
    "SCHOOL_WORLDS_VERIFIED_RECEIPT": BINDING | {"receipt", "artifact_path"},
    "SCHOOL_WORLDS_RESULT": {"generated"}, "TRIAL_CONTAINMENT": {"reason"},
    "STOP_STATE": {"status", "startable", "action_queue"},
    "ORDINARY_STOP_SUBMITTING": set(), "ORDINARY_STOP_ACCEPTED": set(),
    "STOPPED_OBSERVED": {"action_queue", "stop_post_unknown"},
    "BLOCKED": {"reason"}, "TRIAL_ABORT": {"reason", "start_outcome_unknown"},
    "GUEST_DIAGNOSTIC_SKIPPED": {"reason"}, "GUEST_DIAGNOSTIC_FAILED": {"reason"},
    "STOP_READ_BLOCKED": {"reason"}, "ORDINARY_STOP_REFUSED": {"reason"},
    "ORDINARY_STOP_OUTCOME_UNKNOWN": {"reason"},
    "STOP_NOT_CONFIRMED": {"reason", "action_queue"},
    "STOPPED_BUT_START_OUTCOME_UNRESOLVED": set(),
}


class EvidenceError(ValueError):
    pass


def need(condition):
    if not condition:
        raise EvidenceError("EVIDENCE_INVALID")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_file(path, limit=MAX_FILE):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        need(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_size <= limit)
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        need(len(raw) <= limit)
        return raw
    finally:
        os.close(fd)


def owned_directory(path):
    need(path.is_absolute() and path.resolve(strict=True) == path)
    info = path.stat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid())


def parse_log(raw, binding):
    need(type(raw) is bytes and 0 < len(raw) <= MAX_LOG and raw.endswith(b"\n"))
    lines = raw.splitlines()
    need(0 < len(lines) <= 1024 and all(0 < len(line) <= 65536 for line in lines))
    rows = []
    for line in lines:
        row = probe.strict_json(line)
        need(type(row) is dict and type(row.get("event")) is str and row["event"] in SHAPES)
        event = row["event"]
        need(set(row) == SHAPES[event] | {"event"} | ({"at_utc"} if event in TIMED else set()))
        for key, value in row.items():
            if key == "receipt":
                need(type(value) is dict)
            else:
                need(value is None or type(value) in (str, int, float, bool))
                if type(value) is str:
                    need(len(value) <= 4096)
                if type(value) is float:
                    need(math.isfinite(value))
        for key in BINDING & set(row):
            need(type(row[key]) is type(binding[key]) and row[key] == binding[key])
        if "at_utc" in row:
            need(type(row["at_utc"]) is str and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+\+00:00", row["at_utc"]) is not None)
        if "reason" in row:
            need(type(row["reason"]) is str and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", row["reason"]) is not None)
        if "status" in row:
            need(row["status"] in {"stopped", "starting", "running", "stopping", "restarting", "pending", "failed"})
        if "action_queue" in row:
            need(row["action_queue"] == QUEUE)
        for key in {"startable", "stop_post_unknown", "start_outcome_unknown", "generated", "raw_output_exported"} & set(row):
            need(type(row[key]) is bool)
        rows.append(row)
    return rows


def validate_artifacts(directory, binding, logged, *, revision=probe.LEGACY_BUNDLE_REVISION):
    spec=probe.bundle_revision(revision)
    owned_directory(directory)
    raw = {name: read_file(directory / name) for name in ("receipt.json", "manifest.json", "input.json", "worlds.json", "worlds.pbn")}
    receipt = probe.strict_json(raw["receipt.json"])
    probe.validate_receipt(receipt, binding, revision=revision)
    need(receipt == logged and raw["receipt.json"] == validator.canonical_json_bytes(receipt))
    need(receipt["result"] == "WORLD_GENERATED" and receipt["reason"] == "OK" and receipt["stage"] == "COMPLETE")
    need(receipt["artifact"] == dict(name="worlds.json", bytes=72174, sha256=validator.EXPECTED_RESULT_SHA256, encoding="zlib+base64"))
    worlds = probe.strict_json(raw["worlds.json"])
    checked = validator.validate_result(worlds, expected_result_sha256=validator.EXPECTED_RESULT_SHA256)
    need(raw["worlds.json"] == validator.canonical_json_bytes(worlds)
         and raw["worlds.pbn"] == validator.render_pbn(worlds) and raw["input.json"] == validator.INPUT_BYTES)
    need(probe.validation_base(receipt["validation"], receipt["python_version"]) == checked)
    files = {name: dict(bytes=len(value), sha256=digest(value)) for name, value in raw.items() if name != "manifest.json"}
    manifest = probe.strict_json(raw["manifest.json"])
    expected = dict(schema="bridge.ibm.school-worlds.export.v1", source_commit=binding["head"],
                    run_id=binding["run_id"], attempt=binding["attempt"], result="WORLD_GENERATED",
                    snapshot_id=probe.RESUME_SNAPSHOT_ID, snapshot_sha256=probe.RESUME_SNAPSHOT_SHA256,
                     input_sha256=validator.INPUT_SHA256, generator_sha256=spec.source_pins["ibm_worlds_generator"],
                     reviewed_sources=dict(spec.source_pins), host_bundle_sha256=spec.host_source_sha,
                    files=files, validation=checked, purpose="COMPUTE_VERIFICATION_AND_TRAINING_INPUT_ONLY", service_changes=[])
    need(validator.canonical_json_bytes(manifest) == validator.canonical_json_bytes(expected)
         and raw["manifest.json"] == validator.canonical_json_bytes(manifest))
    return {name: dict(bytes=len(value), sha256=digest(value)) for name, value in raw.items()}


def validate_lifecycle(raw, binding, directory, *, revision=probe.LEGACY_BUNDLE_REVISION):
    need(type(binding) is dict and set(binding) == BINDING
         and type(binding["run_id"]) is str and re.fullmatch("[1-9][0-9]{0,19}", binding["run_id"]) is not None
         and type(binding["attempt"]) is int and binding["attempt"] == 1
         and type(binding["head"]) is str and re.fullmatch("[0-9a-f]{40}", binding["head"]) is not None)
    rows = parse_log(raw, binding)
    names = [row["event"] for row in rows]
    sequence = ["SCHOOL_WORLDS_ROUTE_PREPARED", "START_SUBMITTING", "START_ACCEPTED", "SCHOOL_WORLDS_STARTED",
                "SCHOOL_WORLDS_CAPTURED", "SCHOOL_WORLDS_VERIFIED_RECEIPT", "SCHOOL_WORLDS_RESULT", "TRIAL_CONTAINMENT", "STOPPED_OBSERVED"]
    need(all(names.count(name) == 1 for name in sequence))
    positions = [names.index(name) for name in sequence]
    need(positions == sorted(positions) and names[-1] == "STOPPED_OBSERVED")
    allowed = set(sequence) | {"TRIAL_STATE", "STOP_STATE", "ORDINARY_STOP_SUBMITTING", "ORDINARY_STOP_ACCEPTED", "ORDINARY_STOP_OUTCOME_UNKNOWN"}
    need(set(names) <= allowed)
    by_name = {row["event"]: row for row in rows}
    start = by_name["START_SUBMITTING"]
    need(start == dict(event="START_SUBMITTING", mode="trial", max_window_seconds=600, running_window_seconds=None))
    need(by_name["SCHOOL_WORLDS_ROUTE_PREPARED"]["guest_network_requests"] == 0
         and type(by_name["SCHOOL_WORLDS_ROUTE_PREPARED"]["guest_network_requests"]) is int)
    work = by_name["SCHOOL_WORLDS_STARTED"]
    need(type(work["max_seconds"]) in (int, float) and 265 <= work["max_seconds"] <= 378
         and type(work["seconds_since_start"]) in (int, float) and 0 <= work["seconds_since_start"] <= 120
         and type(work["stop_submit_by_seconds"]) is int and work["stop_submit_by_seconds"] == 420
         and type(work["confirmation_reserve_seconds"]) is int and work["confirmation_reserve_seconds"] == 180)
    capture = by_name["SCHOOL_WORLDS_CAPTURED"]
    need(type(capture["remote_exit"]) is int and capture["remote_exit"] == 0
         and type(capture["stdout_bytes"]) is int and 0 < capture["stdout_bytes"] <= 65536
         and type(capture["stderr_bytes"]) is int and capture["stderr_bytes"] == 0
         and re.fullmatch("[0-9a-f]{64}", capture["stdout_sha256"]) is not None
         and capture["stderr_sha256"] == EMPTY_SHA and capture["raw_output_exported"] is False)
    need(by_name["SCHOOL_WORLDS_RESULT"]["generated"] is True
         and by_name["TRIAL_CONTAINMENT"]["reason"] == "school_worlds_complete")
    containment = names.index("TRIAL_CONTAINMENT")
    need(all(names.index("START_ACCEPTED") < index < names.index("SCHOOL_WORLDS_STARTED")
             for index, name in enumerate(names) if name == "TRIAL_STATE"))
    stop_names = {"STOP_STATE", "ORDINARY_STOP_SUBMITTING", "ORDINARY_STOP_ACCEPTED", "ORDINARY_STOP_OUTCOME_UNKNOWN", "STOPPED_OBSERVED"}
    need(all(index > containment for index, name in enumerate(names) if name in stop_names))
    need(names.count("ORDINARY_STOP_SUBMITTING") <= 1
         and names.count("ORDINARY_STOP_ACCEPTED") + names.count("ORDINARY_STOP_OUTCOME_UNKNOWN") == names.count("ORDINARY_STOP_SUBMITTING"))
    if "ORDINARY_STOP_SUBMITTING" in names:
        outcome = "ORDINARY_STOP_ACCEPTED" if "ORDINARY_STOP_ACCEPTED" in names else "ORDINARY_STOP_OUTCOME_UNKNOWN"
        need(names.index(outcome) == names.index("ORDINARY_STOP_SUBMITTING") + 1)
    terminal = rows[-1]
    need(terminal["stop_post_unknown"] is ("ORDINARY_STOP_OUTCOME_UNKNOWN" in names))
    observations = []
    consecutive = 0
    for row in rows[containment + 1:]:
        if row["event"] == "STOP_STATE":
            consecutive = consecutive + 1 if row["status"] == "stopped" and row["startable"] is True else 0
            observations.append(dict(status=row["status"], startable=row["startable"]))
    need(consecutive == 3 and 3 <= len(observations) <= 200 and names[-2] == "STOP_STATE")
    logged = by_name["SCHOOL_WORLDS_VERIFIED_RECEIPT"]
    need(logged["artifact_path"] == str(directory))
    files = validate_artifacts(directory, binding, logged["receipt"], revision=revision)
    return dict(files=files, provider_stop=dict(instance_id=probe.IDENTITY["instance_id"],
                instance_name=probe.IDENTITY["instance_name"], observations=observations,
                consecutive_stopped_startable=3, terminal_event="STOPPED_OBSERVED",
                action_queue=QUEUE, stop_post_unknown=terminal["stop_post_unknown"]))


def build_receipt(raw, binding, directory, controller_exit, capture_exit, *, revision=probe.LEGACY_BUNDLE_REVISION):
    receipt = dict(schema="bridge.ibm.school-compute.lifecycle.v1", **binding,
                   result="INCOMPLETE", reason="INCOMPLETE", controller_exit=controller_exit,
                   capture_exit=capture_exit, controller_log_sha256=None, files={}, provider_stop=None,
                   supervision="OPERATOR_REQUIRED_FOR_CANCEL_TIMEOUT_OR_RUNNER_LOSS", raw_logs_exported=False,
                   artifact_delivery="NOT_ESTABLISHED_BY_THIS_RECEIPT")
    if type(raw) is bytes and len(raw) <= MAX_LOG:
        receipt["controller_log_sha256"] = digest(raw)
    if controller_exit is None or capture_exit is None:
        return receipt
    if type(controller_exit) is not int or type(capture_exit) is not int or not (0 <= controller_exit <= 255 and 0 <= capture_exit <= 255):
        receipt.update(controller_exit=None, capture_exit=None, reason="EVIDENCE_INVALID")
        return receipt
    if controller_exit != 0 or capture_exit != 0:
        receipt["reason"] = "CONTROLLER_FAILED" if controller_exit else "CAPTURE_FAILED"
        return receipt
    try:
        checked = validate_lifecycle(raw, binding, directory, revision=revision)
    except (Exception, KeyboardInterrupt):
        receipt["reason"] = "EVIDENCE_INVALID"
        return receipt
    receipt.update(checked, result="COMPUTE_CONTAINED", reason="OK")
    return receipt


def exit_code(value):
    return int(value) if re.fullmatch(r"(?:0|[1-9][0-9]{0,2})", value) and int(value) <= 255 else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller-exit", default="")
    parser.add_argument("--capture-exit", default="")
    parser.add_argument("--revision", choices=(probe.LEGACY_BUNDLE_REVISION, probe.CONSTRAINT_KEYS_REVISION),
                        default=probe.LEGACY_BUNDLE_REVISION, help="Reviewed bundle revision; never inferred from artifacts.")
    args = parser.parse_args(argv)
    try:
        binding = probe.context()
        parent = Path(os.environ.get("RUNNER_TEMP", ""))
        owned_directory(parent)
        control = parent / "ibm-school-compute-control"
        owned_directory(control)
        try:
            raw = read_file(control / "controller.jsonl", MAX_LOG)
        except (OSError, EvidenceError):
            raw = None
        receipt = build_receipt(raw, binding, parent / "ibm-school-worlds", exit_code(args.controller_exit), exit_code(args.capture_exit),
                                revision=args.revision)
        destination = parent / "ibm-school-compute-artifact"
        os.mkdir(destination, 0o700)  # No reused or partial artifact can become a new success.
        files = {}
        if receipt["result"] == "COMPUTE_CONTAINED":
            for name, expected in receipt["files"].items():
                raw = read_file(parent / "ibm-school-worlds" / name)
                need(dict(bytes=len(raw), sha256=digest(raw)) == expected)
                files[name] = raw
        # Publish the success marker last: interrupted copies have no completed
        # lifecycle receipt, even if upload-artifact later captures partial files.
        files["lifecycle.json"] = validator.canonical_json_bytes(receipt)
        for name, raw in files.items():
            fd = os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        print("IBM_SCHOOL_COMPUTE_LIFECYCLE=" + receipt["result"])
        return 0 if receipt["result"] == "COMPUTE_CONTAINED" else 3
    except (Exception, KeyboardInterrupt):
        print("IBM_SCHOOL_COMPUTE_LIFECYCLE=EVIDENCE_UNAVAILABLE")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
