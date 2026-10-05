"""Opt-in autonomous Drive finalization, with no credentials or source mutations.

The protected registry is operator-owned, not a queue-provided publication grant.
Its exact source/job hash and existing folder IDs must match before any POST.
No registry means retained local evidence, not an implicit upload permission.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path

from .comparison_artifacts import verify_comparison_original
from .drive_adapter import access_token, file_metadata, hash_remote_file, original_snapshot
from .drive_results import (PublishArtifact, _upload_or_verify_file, _verify_folder,
                            _get_file_metadata, _verify_remote_artifact,
                            artifact_set_sha256, collect_compact_artifacts)
from .result_conformance import verify_result

READBACK = "CONTENT_READBACK_SHA256"
SOURCE_RECEIPT = "SOURCE_INTEGRITY.json"
FINAL_RECEIPT = "DRIVE_FINALIZATION.json"
ID = re.compile(r"^[A-Za-z0-9_-]{10,200}$")


def atomic_json(path: Path, data: dict) -> None:
    """Durable atomic stage receipt; never acknowledge before file+dir fsync."""
    if path.is_symlink() or path.parent.is_symlink():
        raise RuntimeError("unsafe receipt path")
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temp.open("x", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        if os.name != "nt":
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        temp.unlink(missing_ok=True)


def read_receipt(path: Path) -> dict:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
        raise RuntimeError("unsafe receipt")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("invalid receipt")
    return data


def prepare_compute_recovery(result_dir: Path, recovery_root: Path, attempts_path: Path,
                              *, job_hash: str) -> None:
    """Idempotently quarantine one partial tree, then reserve one compute retry.

    PREPARED survives a crash before/after rename. STARTED without a complete
    result is an unknown compute outcome and never permits an automatic repeat.
    """
    recovery_root.mkdir(parents=True, exist_ok=True)
    if recovery_root.is_symlink() or result_dir.is_symlink():
        raise RuntimeError("unsafe compute recovery path")
    previous = read_receipt(attempts_path) if attempts_path.exists() else None
    if previous:
        if previous.get("job_hash") != job_hash or previous.get("state") != "PREPARED":
            raise RuntimeError("compute recovery outcome unknown/budget exhausted; evidence retained")
        name = previous.get("quarantine_name", "")
        if not isinstance(name, str) or not re.fullmatch(r"partial-[0-9]+", name):
            raise RuntimeError("invalid compute recovery target")
    else:
        import time
        name = f"partial-{time.time_ns()}"
        previous = {"job_hash": job_hash, "state": "PREPARED", "attempts": 1, "quarantine_name": name}
        atomic_json(attempts_path, previous)
    quarantine = recovery_root / name
    if quarantine.is_symlink():
        raise RuntimeError("unsafe quarantine target")
    if result_dir.exists():
        if quarantine.exists():
            raise RuntimeError("compute recovery directory conflict")
        result_dir.rename(quarantine)
        if os.name != "nt":
            for directory in (result_dir.parent, recovery_root):
                fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
    elif not quarantine.is_dir():
        raise RuntimeError("compute recovery evidence unavailable")
    atomic_json(attempts_path, {**previous, "state": "STARTED"})


def configured_binding(job_id: str, source_id: str, job_hash: str, *, kind: str = "video") -> dict | None:
    raw = os.getenv("UNIVERSAL_VIDEO_DRIVE_BINDINGS_FILE", "").strip()
    if not raw:
        return None
    if kind == "book":
        from .book_runner import book_registry
        registry = book_registry(Path(raw))
    else:
        registry = None
    path = Path(raw)
    if not path.is_absolute() or path.is_symlink():
        raise RuntimeError("unsafe Drive binding registry")
    info = path.stat()
    if info.st_mode & 0o022:
        raise RuntimeError("Drive binding registry must not be group/world writable")
    registry = read_receipt(path) if registry is None else registry
    if not (registry.get("schema") == "universal-worker-drive-bindings-v2"
            or kind == "video" and registry.get("schema") == "universal-video-drive-bindings-v1"):
        raise RuntimeError("invalid Drive binding registry")
    bindings = registry.get("jobs")
    if not isinstance(bindings, list):
        raise RuntimeError("invalid Drive jobs registry")
    matches = [b for b in bindings if isinstance(b, dict) and b.get("job_id") == job_id]
    if len(matches) != 1:
        raise RuntimeError("Drive job binding missing or duplicated")
    binding = matches[0]
    if binding.get("kind", "video") != kind:
        raise RuntimeError("Drive workload kind mismatch")
    if (binding.get("source_file_id") != source_id or binding.get("job_hash") != job_hash
            or binding.get("processing_upload_authorized") is not True):
        raise RuntimeError("Drive job/source authorization mismatch")
    folders = binding.get("folders", {})
    roles = ({"school_root", "processing", "book", "source_job", "analysis", "checks"}
             if kind == "book" else
             {"school_root", "processing", "video", "source_job", "transcript", "frames", "analysis", "checks"})
    if (not isinstance(folders, dict) or set(folders) != roles
            or not all(isinstance(v, str) and ID.fullmatch(v) for v in folders.values())
            or len(set(folders.values())) != len(roles) or source_id in folders.values()):
        raise RuntimeError("Drive output IDs must be exact, distinct and exclude original")
    return binding


def _result_contract(profile: str):
    if profile == "book_single_atom":
        from .book_runner import collect_book_artifacts, verify_book_result
        parents = {"processing": "school_root", "book": "processing",
                   "source_job": "book", "analysis": "source_job", "checks": "source_job"}
        return collect_book_artifacts, verify_book_result, parents, {"analysis", "checks"}
    parents = {"processing": "school_root", "video": "processing", "source_job": "video",
               "transcript": "source_job", "frames": "source_job",
               "analysis": "source_job", "checks": "source_job"}
    return collect_compact_artifacts, verify_result, parents, {"transcript", "frames", "analysis", "checks"}


def verify_source(source_dir: Path, token: str) -> dict:
    receipt = read_receipt(source_dir / SOURCE_RECEIPT)
    expected = receipt["original"]
    source_id = expected["id"]
    before = original_snapshot(file_metadata(source_id, token))
    if before != expected:
        raise RuntimeError("Drive original changed since intake")
    observed = hash_remote_file(source_id, token, expected_size=int(expected["size"]),
                                expected_sha256=receipt["sha256"])
    after = original_snapshot(file_metadata(source_id, token))
    if after != expected:
        raise RuntimeError("Drive original changed during verification")
    # PDF jobs pin all six immutable inputs, including the reviewed executable.
    if "book_inputs" in receipt:
        inputs = receipt["book_inputs"]
        if not isinstance(inputs, list) or len(inputs) != 6:
            raise RuntimeError("book input pin incomplete")
        if len({item["role"] for item in inputs}) != 6:
            raise RuntimeError("book input pin duplicated")
        for item in inputs:
            pinned = item["original"]
            observed_before = original_snapshot(file_metadata(pinned["id"], token))
            if observed_before != pinned:
                raise RuntimeError("book input changed before finalization")
            hash_remote_file(pinned["id"], token, expected_size=item["size"],
                             expected_sha256=item["sha256"])
            if original_snapshot(file_metadata(pinned["id"], token)) != pinned:
                raise RuntimeError("book input changed during finalization")
    return {"file_id": source_id, "before": expected, "after": after,
            "sha256_before": receipt["sha256"], "sha256_after": observed["sha256"],
            "verification": READBACK, "unchanged": True}



def _book_owner_only(metadata, binding):
    owner = binding.get("drive_owner_permission_id")
    permissions = metadata.get("permissions")
    if (not isinstance(owner, str) or not owner or not isinstance(permissions, list)
            or len(permissions) != 1 or not isinstance(permissions[0], dict)
            or permissions[0].get("id") != owner or permissions[0].get("type") != "user"
            or permissions[0].get("role") != "owner" or metadata.get("trashed") is True):
        raise RuntimeError("book owner-only Drive ACL required")


def _book_folder_acl(folder_id, token, binding):
    from .drive_adapter import DRIVE
    from .drive_results import requests, _headers
    response = requests.get(
        f"{DRIVE}/files/{folder_id}", headers=_headers(token),
        params={"fields": "id,permissions(id,type,role),trashed", "supportsAllDrives": True},
        timeout=30,
    )
    response.raise_for_status()
    _book_owner_only(response.json(), binding)

def finalize_drive_job(result_dir: Path, source_dir: Path, binding: dict, *,
                       job_id: str, profile: str, job_hash: str) -> dict:
    """Idempotent finalization; failures/crashes retain input and local results.

    Caller holds an exclusive worker claim fence through compute/finalization.
    Existing remote items are always read back again, including after a crash.
    Only create POSTs to the exact configured output folders; no original PATCH,
    DELETE, move or rename path exists here.
    """
    collect, verify, parents, writable_roles = _result_contract(profile)
    prior = result_dir / FINAL_RECEIPT
    previous_path = result_dir / "DRIVE_FINALIZATION.previous.json"
    # A stale PASS cannot authorize cleanup while a retry is being revalidated.
    # Keep the old receipt as evidence even if a subsequent read/upload fails.
    if prior.exists():
        previous_proof = read_receipt(prior)
        if previous_proof.get("status") == "PUBLISHED_VERIFIED":
            atomic_json(previous_path, previous_proof)
        elif previous_proof.get("status") != "REVALIDATING":
            raise RuntimeError("invalid previous finalization receipt")
        atomic_json(prior, {"schema": "universal-video-drive-finalization-v1", "status": "REVALIDATING"})
    token = access_token()  # existing resident credentials only
    intake = read_receipt(source_dir / SOURCE_RECEIPT)
    if (intake.get("schema") != "universal-video-source-integrity-v1"
            or intake.get("job_id") != job_id or intake.get("job_hash") != job_hash):
        raise RuntimeError("source pin/job identity mismatch")
    source = verify_source(source_dir, token)  # reject changed source before writes
    if source["file_id"] != binding["source_file_id"]:
        raise RuntimeError("Drive source binding mismatch")
    artifacts = collect(result_dir)
    if profile != "book_single_atom":
        verify_comparison_original(result_dir, source)
    bundle = artifact_set_sha256(artifacts)
    conformance = verify(result_dir, expected_job_id=job_id, expected_profile=profile,
                                expected_job_hash=job_hash,
                                expected_source_file_id=source["file_id"],
                                expected_artifact_set_sha256=bundle,
                                evidence_phase="PUBLICATION_PREFLIGHT", require_server_review=True)
    if conformance.get("state") != "PASS" or conformance.get("artifact_set_sha256") != bundle:
        raise RuntimeError("result conformance is not PASS")
    folders = binding["folders"]
    if profile == "book_single_atom":
        for folder_id in folders.values():
            _book_folder_acl(folder_id, token, binding)
    def remote_metadata(file_id):
        metadata = _get_file_metadata(file_id, token)
        if profile == "book_single_atom":
            _book_owner_only(metadata, binding)
        return metadata
    for role, folder_id in folders.items():
        _verify_folder(folder_id, token,
                       expected_parent_id=folders[parents[role]] if role in parents else None,
                       require_writable=role in writable_roles)
    if previous_path.exists():
        previous = read_receipt(previous_path)
        if (previous.get("job_hash") != job_hash or previous.get("artifact_set_sha256") != bundle
                or previous.get("folders") != folders or previous.get("source", {}).get("before") != source["before"]):
            raise RuntimeError("finalization receipt conflicts with retry")
    records = []
    uploaded = []
    for artifact in artifacts:
        name = artifact.relative_name
        role = ("analysis" if profile == "book_single_atom" and name.startswith("renders/") else
                "checks" if profile == "book_single_atom" else
                "frames" if name.startswith("frames/") else "transcript" if name in
                {"transcript.jsonl", "transcript.txt", "speaker_diarization.json"} else
                "analysis" if name == "algorithm_3_1_test.json" or name.startswith("comparison/part-") else "checks")
        # Versioned stable names allow crash recovery without replacing any item.
        remote_name = f"{job_id}-{bundle}-{name.replace('/', '__')}"
        upload = PublishArtifact(artifact.path, remote_name, artifact.size_bytes, artifact.sha256, artifact.md5)
        if profile == "book_single_atom":
            _book_folder_acl(folders[role], token, binding)
        record = _upload_or_verify_file(folders[role], upload, token)
        remote_metadata(record["file_id"])
        if record["file_id"] == source["file_id"]:
            raise RuntimeError("output item aliases original")
        record.update(relative_name=name, folder_id=folders[role])
        records.append(record)
        uploaded.append((upload, record))
    # Re-check both identity and byte content after the entire transfer set.
    for upload, record in uploaded:
        _verify_remote_artifact(remote_metadata(record["file_id"]), upload,
                                token, record["folder_id"])
    # Original can change during output transfers: verify again before receipt.
    source = verify_source(source_dir, token)
    receipt = {"schema": "universal-video-drive-finalization-v1", "status": "PUBLISHED_VERIFIED",
               "job_id": job_id, "job_hash": job_hash, "profile": profile,
               "artifact_set_sha256": bundle, "folders": folders, "source": source,
               "remote_verification": READBACK, "remote_artifacts": records,
               "canonical_promotion_allowed": False}
    # Save the per-job recovery receipt itself to Drive as the last output.
    # No timestamps/random fields: after a crash the exact marker is reusable.
    marker_path = result_dir / ".drive-finalization-marker.json"
    atomic_json(marker_path, receipt)
    marker_bytes = marker_path.read_bytes()
    if any(a.relative_name.startswith("comparison/") for a in artifacts) and len(marker_bytes) > 1024**2:
        raise RuntimeError("comparison completion marker exceeds reserved quota")
    marker = PublishArtifact(marker_path, f"{job_id}-{bundle}-PUBLICATION_COMPLETE.json",
                             len(marker_bytes), hashlib.sha256(marker_bytes).hexdigest(),
                             hashlib.md5(marker_bytes, usedforsecurity=False).hexdigest())
    if profile == "book_single_atom":
        _book_folder_acl(folders["checks"], token, binding)
    receipt["remote_receipt"] = _upload_or_verify_file(folders["checks"], marker, token)
    if receipt["remote_receipt"]["file_id"] == source["file_id"]:
        raise RuntimeError("remote receipt aliases original")
    for upload, record in uploaded:
        _verify_remote_artifact(remote_metadata(record["file_id"]), upload,
                                token, record["folder_id"])
    _verify_remote_artifact(remote_metadata(receipt["remote_receipt"]["file_id"]),
                            marker, token, folders["checks"])
    # Also guard mutations concurrent with marker upload and folder reparenting.
    receipt["source"] = verify_source(source_dir, token)
    for role in sorted(writable_roles):
        _verify_folder(folders[role], token, expected_parent_id=folders["source_job"], require_writable=True)
    if profile == "book_single_atom":
        for folder_id in folders.values():
            _book_folder_acl(folder_id, token, binding)
    atomic_json(prior, receipt)
    return receipt


def cleanup_proof_matches(result_dir: Path, source_dir: Path | None, *, job_id: str) -> bool:
    """Local durable proof must bind exact source and full retained artifact set."""
    try:
        proof = read_receipt(result_dir / FINAL_RECEIPT)
        source = proof["source"]
        intake = (read_receipt(source_dir / SOURCE_RECEIPT) if source_dir is not None else
                  {"original": source["before"], "sha256": source["sha256_before"]})
        manifest = read_receipt(result_dir / "manifest.json")
        collect, verify, _, _ = _result_contract(str(manifest.get("profile") or ""))
        if manifest.get("profile") == "book_single_atom":
            verify(result_dir, expected_job_id=job_id, expected_job_hash=manifest.get("job_hash"),
                   expected_source_file_id=proof["source"]["file_id"])
        artifacts = collect(result_dir)
        if manifest.get("profile") != "book_single_atom":
            verify_comparison_original(result_dir, source)
        inventory = {a.relative_name: (a.size_bytes, a.sha256) for a in artifacts}
        records = proof["remote_artifacts"]
        remote = {r["relative_name"]: (r["size_bytes"], r["sha256"]) for r in records}
        manifest = read_receipt(result_dir / "manifest.json")
        return (proof.get("schema") == "universal-video-drive-finalization-v1"
                and proof.get("status") == "PUBLISHED_VERIFIED" and proof.get("job_id") == job_id
                and proof.get("job_hash") == manifest.get("job_hash")
                and original_snapshot(source["before"]) == source["before"]
                and len(source["sha256_before"]) == 64
                and proof.get("remote_verification") == READBACK and source.get("unchanged") is True
                and proof.get("remote_receipt", {}).get("verification") == READBACK
                and bool(proof.get("remote_receipt", {}).get("file_id"))
                and proof.get("remote_receipt", {}).get("file_id") != source["file_id"]
                and source.get("before") == source.get("after") == intake["original"]
                and source.get("sha256_before") == source.get("sha256_after") == intake["sha256"]
                and proof.get("artifact_set_sha256") == artifact_set_sha256(artifacts)
                and len(remote) == len(records) == len(inventory) and remote == inventory
                and len({r.get("file_id") for r in records}) == len(records)
                and all(r.get("verification") == READBACK and bool(r.get("file_id")) and r.get("file_id") != source["file_id"]
                        and r.get("folder_id") in proof["folders"].values() for r in records))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return False
