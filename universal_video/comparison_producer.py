"""Protected opt-in orchestration of the existing offline comparison CLI.

No credentials, publication, clip extraction or gold generation. A failed or
interrupted attempt retains evidence and never authorizes an automatic rerun.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from . import comparison_artifacts as artifacts
from .comparison_requirement import comparison_required, require_comparison_package, receipt_requirement
from .durable_drive import SOURCE_RECEIPT, atomic_json

CONFIG_ENV = "UNIVERSAL_VIDEO_COMPARISON_PRODUCERS_FILE"
SCHEMA = "universal-video-comparison-producers-v1"
FIELDS = {
    "job_id", "job_hash", "source_file_id", "source_version", "source_sha256",
    "replay_authorized", "runner_root", "runner_commit", "runner_sha256",
    "sealed_manifest_path", "sealed_manifest_sha256",
    "clip_binding_path", "clip_binding_sha256", "timeout_seconds",
}


class ComparisonProducerError(RuntimeError):
    error_code = "UV_COMPARISON_PRODUCER_BLOCKED"


def _blocked(message):
    raise ComparisonProducerError(message)


def _sealed(path, digest):
    artifacts._hex(digest)
    raw = artifacts.read(path, artifacts.MAX_JSON_BYTES)
    if hashlib.sha256(raw).hexdigest() != digest:
        _blocked("comparison pinned input changed")
    value = artifacts.decode(raw)
    if not isinstance(value, dict):
        _blocked("comparison object required")
    return value


def _configuration(job_id, expected):
    raw = os.getenv(CONFIG_ENV, "").strip()
    if not raw:
        _blocked("comparison producer disabled; required package unavailable")
    path = Path(raw)
    if not path.is_absolute():
        _blocked("absolute protected comparison registry required")
    info = artifacts.checked(path)
    if info.st_mode & 0o022:
        _blocked("comparison registry must not be group/world writable")
    registry = artifacts.decode(artifacts.read(path, 1024 * 1024))
    if (not isinstance(registry, dict) or set(registry) != {"schema", "jobs"}
            or registry["schema"] != SCHEMA or not isinstance(registry["jobs"], list)):
        _blocked("invalid comparison producer registry")
    matches = [item for item in registry["jobs"]
               if isinstance(item, dict) and item.get("job_id") == job_id]
    if len(matches) != 1:
        _blocked("comparison producer binding missing or duplicated")
    config = matches[0]
    if set(config) != FIELDS or config["replay_authorized"] is not True:
        _blocked("exact authorized comparison binding required")
    if any(config[key] != value for key, value in expected.items()):
        _blocked("comparison producer job/source binding mismatch")
    if type(config["timeout_seconds"]) is not int or not 1 <= config["timeout_seconds"] <= 900:
        _blocked("comparison timeout must be between 1 and 900")
    artifacts._hex(config["runner_commit"], artifacts.HEX40)
    artifacts._hex(config["runner_sha256"])
    for key in ("runner_root", "sealed_manifest_path", "clip_binding_path"):
        if not isinstance(config[key], str) or not Path(config[key]).is_absolute():
            _blocked("absolute comparison paths required")
    return config


def _offline_env():
    # Clearing environment variables does not isolate credential files.
    # A future qualified sandbox executor must enforce filesystem boundaries.
    return {"PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}


def _runner_file(config):
    # Qualification is not an inbox/registry boolean. There is no reviewed
    # credential-isolated comparison executor wired into this draft.
    _blocked("comparison requires a qualified credential-isolated sandbox executor")


def _preflight(config):
    sealed = _sealed(config["sealed_manifest_path"], config["sealed_manifest_sha256"])
    if (sealed.get("schema") != artifacts.RESULT_KIND
            or sealed.get("gold_frozen_before_outputs") is not True
            or not isinstance(sealed.get("case_id"), str)
            or not artifacts.CASE.fullmatch(sealed["case_id"])
            or type(sealed.get("source_offset_ms")) is not int or sealed["source_offset_ms"] < 0):
        _blocked("sealed pre-output gold and clip required")
    inputs = sealed.get("inputs")
    if not isinstance(inputs, dict) or set(inputs) != {"video", "reference", "profile", "sprite", "gold"}:
        _blocked("exact frozen comparison inputs required")
    for item in inputs.values():
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            _blocked("invalid frozen comparison input")
        artifacts._hex(item["sha256"])
        if not isinstance(item["path"], str) or not Path(item["path"]).is_absolute():
            _blocked("absolute frozen input required")
        if hashlib.sha256(artifacts.read(item["path"])).hexdigest() != item["sha256"]:
            _blocked("frozen comparison input changed")
    clip = _sealed(config["clip_binding_path"], config["clip_binding_sha256"])
    if (clip.get("schema") != "universal-video-source-clip-binding-v1"
            or clip.get("status") != "PASS"
            or any(clip.get(key) != config[key] for key in
                   ("source_file_id", "source_version", "source_sha256"))
            or clip.get("clip_sha256") != inputs["video"]["sha256"]
            or clip.get("source_offset_ms") != sealed["source_offset_ms"]
            or type(clip.get("duration_ms")) is not int or not 1 <= clip["duration_ms"] <= 120000):
        _blocked("independently verified original-to-clip mapping required")
    artifacts._hex(clip.get("verification_receipt_sha256"))
    roots = []
    for variant in ("baseline", "candidate"):
        runtime = sealed.get(variant)
        if not isinstance(runtime, dict) or not isinstance(runtime.get("root"), str):
            _blocked("exact comparison runtime required")
        artifacts._hex(runtime.get("sha"), artifacts.HEX40)
        if not Path(runtime["root"]).is_absolute():
            _blocked("absolute runtime checkout required")
        roots.append(Path(runtime["root"]).resolve())
    if roots[0] == roots[1]:
        _blocked("separate comparison runtime checkouts required")
    return sealed


def _launch(config, runner, raw, log):
    # Never execute under the credential-bearing resident UID as a fallback.
    _blocked("comparison requires a qualified credential-isolated sandbox executor")


def ensure_comparison(result_dir, *, job_id, job_hash, source_file_id, source_dir):
    """Attach before review; reuse valid typed packages without executing again."""
    result_dir = Path(result_dir)
    manifest_path = result_dir / "manifest.json"
    artifacts.checked(result_dir, directory=True)
    before = artifacts.read(manifest_path, 5 * 1024**2)
    manifest = artifacts.decode(before)
    if not isinstance(manifest, dict) or not comparison_required(manifest.get("metadata")):
        _blocked("comparison requirement differs from intake")
    if (manifest.get("job_id") != job_id or manifest.get("job_hash") != job_hash
            or (manifest.get("source") or {}).get("file_id") != source_file_id):
        _blocked("comparison parent differs from intake")
    if manifest.get("comparison_artifacts") is not None or (result_dir / "comparison").exists():
        require_comparison_package(result_dir, expected_required=True)
        return manifest
    if (manifest.get("status") != "COMPLETED" or source_dir is None
            or (result_dir / "server_review.json").exists()
            or (result_dir / "DRIVE_FINALIZATION.json").exists()):
        _blocked("comparison needs completed staged source before review/finalization")
    pin_path = Path(source_dir) / SOURCE_RECEIPT
    pin_raw = artifacts.read(pin_path, 1024 * 1024)
    pin = artifacts.decode(pin_raw)
    if not isinstance(pin, dict):
        _blocked("comparison original stage object required")
    source = manifest.get("source") or {}
    version = str((pin.get("original") or {}).get("version") or "")
    if (not re.fullmatch(r"[1-9][0-9]*", version)
            or ("version" in source and str(source["version"]) != version)):
        _blocked("comparison source version conflicts with staged original")
    expected = {"job_id": job_id, "job_hash": job_hash, "source_file_id": source_file_id,
                "source_version": version,
                "source_sha256": (manifest.get("media") or {}).get("sha256")}
    if (pin.get("schema") != "universal-video-source-integrity-v1"
            or pin.get("job_id") != job_id or pin.get("job_hash") != job_hash
            or receipt_requirement(pin) is not True
            or (pin.get("original") or {}).get("id") != source_file_id
            or str((pin.get("original") or {}).get("version")) != expected["source_version"]
            or pin.get("sha256") != expected["source_sha256"]
            or source.get("file_id") != source_file_id):
        _blocked("comparison original stage pin mismatch")
    config = _configuration(job_id, expected)
    runner = _runner_file(config)
    _preflight(config)
    # Exclusive reservation is the durable attempt budget. Never remove it on
    # failure or automatically rerun a crashed, unknown or conflicting attempt.
    attempt = result_dir.parent / (result_dir.name + ".comparison-replay")
    attempt.mkdir(mode=0o700)
    if os.name != "nt":
        fd = os.open(attempt.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    atomic_json(attempt / "attempt.json", {"schema": "universal-video-comparison-attempt-v1",
                "state": "STARTED", **expected,
                "sealed_manifest_sha256": config["sealed_manifest_sha256"],
                "runner_commit": config["runner_commit"], "runner_sha256": config["runner_sha256"]})
    raw = attempt / "raw"
    _launch(config, runner, raw, attempt / "runner.log")
    _runner_file(config)
    _preflight(config)
    if (_configuration(job_id, expected) != config
            or artifacts.read(pin_path, 1024 * 1024) != pin_raw
            or (result_dir / "server_review.json").exists()
            or (result_dir / "DRIVE_FINALIZATION.json").exists()
            or artifacts.read(manifest_path, 5 * 1024**2) != before):
        _blocked("comparison parent changed during replay")
    args = {key: config[key] for key in (
        "sealed_manifest_path", "sealed_manifest_sha256", "runner_commit", "runner_sha256",
        "job_id", "job_hash", "source_file_id", "source_version", "source_sha256",
        "clip_binding_path", "clip_binding_sha256")}
    # Main compute currently omits Drive version from provenance. Attach the
    # independently pinned version, never a queue/config-provided substitution.
    manifest["source"] = {**source, "version": version}
    manifest["comparison_artifacts"] = artifacts.build_comparison_package(
        raw, result_dir / "comparison", **args)
    atomic_json(manifest_path, manifest)
    require_comparison_package(result_dir, expected_required=True)
    atomic_json(attempt / "attempt.json", {"schema": "universal-video-comparison-attempt-v1",
                "state": "ATTACHED", **expected,
                "comparison_manifest_sha256": manifest["comparison_artifacts"]["manifest_sha256"]})
    return manifest
