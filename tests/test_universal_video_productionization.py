import json
import os
from pathlib import Path

import pytest

import universal_video.drive_preflight as drive_preflight
import universal_video.drive_results as drive_results
from universal_video.drive_preflight import credential_boundary_status
from universal_video.drive_results import PublishArtifact, collect_compact_artifacts, publish_result
from universal_video.maintenance import RetentionPolicy, apply_cleanup_plan, build_cleanup_plan

DAY = 24 * 3600


def _terminal_result(root: Path, job_id: str, *, age_seconds: int, now: float, payload: bytes = b"x") -> Path:
    job = root / "spool" / "results" / job_id
    job.mkdir(parents=True, exist_ok=True)
    (job / "manifest.json").write_text(json.dumps({"status": "COMPLETED", "job_id": job_id}), encoding="utf-8")
    (job / "transcript.txt").write_bytes(payload)
    stamp = now - age_seconds
    for path in (job / "manifest.json", job / "transcript.txt", job):
        os.utime(path, (stamp, stamp), follow_symlinks=False)
    return job


def _durable_publication_receipt(root: Path, job_id: str, *, age_seconds: int, now: float) -> Path:
    artifact_set = "a" * 64
    manifest_sha = "b" * 64
    receipt = root / "spool" / "done" / f"{job_id}.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps(
            {
                "status": "COMPLETED",
                "compute_status": "COMPLETED",
                "job_id": job_id,
                "publication_state": "REMOTE_VERIFIED",
                "publication": {
                    "status": "PUBLISHED_VERIFIED",
                    "remote_verification": "SIZE_MD5_SHA256_PROPERTY_MATCH",
                    "artifact_set_sha256": artifact_set,
                    "manifest_sha256": manifest_sha,
                    "remote_artifacts": [
                        {"relative_name": "manifest.json", "size_bytes": 10, "sha256": manifest_sha}
                    ],
                },
                "conformance": {
                    "state": "PASS",
                    "artifact_set_sha256": artifact_set,
                    "manifest_sha256": manifest_sha,
                },
            }
        ),
        encoding="utf-8",
    )
    stamp = now - age_seconds
    os.utime(receipt, (stamp, stamp))
    return receipt



def _durable_publication_proof(result_dir: Path, job_id: str) -> Path:
    manifest = json.loads((result_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["job_hash"] = "c" * 64
    (result_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    proof = result_dir / "DURABLE_PUBLICATION_PROOF.json"
    proof.write_text(
        json.dumps(
            {
                "schema": "universal-video-durable-publication-proof-v1",
                "status": "PUBLISHED_VERIFIED",
                "job_id": job_id,
                "job_hash": "c" * 64,
                "drive_folder_id": "drive-folder-id",
                "artifact_set_sha256": "a" * 64,
                "publication_marker_sha256": "b" * 64,
                "remote_verification": "SIZE_MD5_SHA256_PROPERTY_MATCH",
            }
        ),
        encoding="utf-8",
    )
    return proof


def test_retention_never_deletes_pending_or_running_artifacts(tmp_path: Path):
    now = 2_000_000_000.0
    base = tmp_path / "uv"
    for name in ("inbox", "running", "done", "failed", "results"):
        (base / "spool" / name).mkdir(parents=True, exist_ok=True)
    (base / "media").mkdir(parents=True)

    active_media = base / "media" / "active.mp4"
    stale_media = base / "media" / "stale.mp4"
    active_media.write_bytes(b"active")
    stale_media.write_bytes(b"stale")
    old = now - 40 * DAY
    os.utime(active_media, (old, old))
    os.utime(stale_media, (old, old))

    (base / "spool" / "running" / "active.json").write_text(
        json.dumps(
            {
                "job_id": "active-job",
                "profile": "transcript_only",
                "source": {"kind": "local_path", "path": str(active_media)},
            }
        ),
        encoding="utf-8",
    )
    active_result = _terminal_result(base, "active-job", age_seconds=40 * DAY, now=now)
    stale_result = _terminal_result(base, "stale-job", age_seconds=40 * DAY, now=now)
    _durable_publication_receipt(base, "stale-job", age_seconds=40 * DAY, now=now)

    policy = RetentionPolicy(max_deletes_per_run=100)
    plan = build_cleanup_plan(base, policy=policy, now=now)
    paths = {item.path for item in plan}
    assert active_media not in paths
    assert active_result not in paths
    assert stale_media in paths
    assert stale_result in paths


def test_retention_blocks_completed_cleanup_without_durable_publication_proof(tmp_path: Path):
    now = 2_000_000_000.0
    base = tmp_path / "uv"
    for name in ("inbox", "running", "done", "failed", "results"):
        (base / "spool" / name).mkdir(parents=True, exist_ok=True)
    (base / "media").mkdir(parents=True)
    unproven = _terminal_result(base, "unproven-job", age_seconds=40 * DAY, now=now)
    (base / "spool" / "done" / "unproven-job.json").write_text(
        json.dumps({"status": "COMPLETED", "job_id": "unproven-job"}),
        encoding="utf-8",
    )
    proven = _terminal_result(base, "proven-job", age_seconds=40 * DAY, now=now)
    proven_receipt = _durable_publication_receipt(base, "proven-job", age_seconds=40 * DAY, now=now)

    plan = build_cleanup_plan(base, policy=RetentionPolicy(), now=now)
    paths = {item.path for item in plan}

    assert unproven not in paths
    assert base / "spool" / "done" / "unproven-job.json" not in paths
    assert proven in paths
    assert proven_receipt in paths


def test_retention_accepts_explicit_external_publication_proof_dir(monkeypatch, tmp_path: Path):
    now = 2_000_000_000.0
    base = tmp_path / "uv"
    for name in ("inbox", "running", "done", "failed", "results"):
        (base / "spool" / name).mkdir(parents=True, exist_ok=True)
    (base / "media").mkdir(parents=True)
    result = _terminal_result(base, "external-proof-job", age_seconds=40 * DAY, now=now)
    local_done = base / "spool" / "done" / "external-proof-job.json"
    local_done.write_text(
        json.dumps({"status": "COMPLETED", "job_id": "external-proof-job"}),
        encoding="utf-8",
    )
    stamp = now - 40 * DAY
    os.utime(local_done, (stamp, stamp))
    proof_dir = tmp_path / "published"
    _durable_publication_receipt(proof_dir, "external-proof-job", age_seconds=40 * DAY, now=now)
    monkeypatch.setenv("UNIVERSAL_VIDEO_PUBLISHED_RECEIPT_DIRS", str(proof_dir / "spool" / "done"))

    plan = build_cleanup_plan(base, policy=RetentionPolicy(), now=now)
    paths = {item.path for item in plan}

    assert result in paths
    assert local_done in paths



def test_retention_accepts_local_publication_proof_sidecar(tmp_path: Path):
    now = 2_000_000_000.0
    base = tmp_path / "uv"
    for name in ("inbox", "running", "done", "failed", "results"):
        (base / "spool" / name).mkdir(parents=True, exist_ok=True)
    (base / "media").mkdir(parents=True)

    result = _terminal_result(base, "sidecar-proof-job", age_seconds=40 * DAY, now=now)
    _durable_publication_proof(result, "sidecar-proof-job")

    plan = build_cleanup_plan(base, policy=RetentionPolicy(), now=now)
    assert result in {item.path for item in plan}


def test_retention_rejects_incomplete_local_publication_proof_sidecar(tmp_path: Path):
    now = 2_000_000_000.0
    base = tmp_path / "uv"
    for name in ("inbox", "running", "done", "failed", "results"):
        (base / "spool" / name).mkdir(parents=True, exist_ok=True)
    (base / "media").mkdir(parents=True)

    result = _terminal_result(base, "bad-sidecar-proof-job", age_seconds=40 * DAY, now=now)
    proof = _durable_publication_proof(result, "bad-sidecar-proof-job")
    payload = json.loads(proof.read_text(encoding="utf-8"))
    payload["publication_marker_sha256"] = "not-a-sha"
    proof.write_text(json.dumps(payload), encoding="utf-8")

    plan = build_cleanup_plan(base, policy=RetentionPolicy(), now=now)
    assert result not in {item.path for item in plan}


def test_abandoned_result_directory_is_bounded_after_grace(tmp_path: Path):
    now = 2_000_000_000.0
    base = tmp_path / "uv"
    for name in ("inbox", "running", "done", "failed", "results"):
        (base / "spool" / name).mkdir(parents=True, exist_ok=True)
    (base / "media").mkdir(parents=True)
    abandoned = base / "spool" / "results" / "abandoned-job"
    abandoned.mkdir()
    (abandoned / "partial.tmp").write_bytes(b"partial")
    old = now - 8 * DAY
    os.utime(abandoned / "partial.tmp", (old, old))
    os.utime(abandoned, (old, old))

    plan = build_cleanup_plan(base, policy=RetentionPolicy(), now=now)
    item = next(candidate for candidate in plan if candidate.path == abandoned)
    assert item.reason == "abandoned_ttl"


def test_retention_apply_removes_only_planned_managed_paths(tmp_path: Path):
    now = 2_000_000_000.0
    base = tmp_path / "uv"
    for name in ("inbox", "running", "done", "failed", "results"):
        (base / "spool" / name).mkdir(parents=True, exist_ok=True)
    (base / "media").mkdir(parents=True)
    receipt = base / "spool" / "failed" / "old.json"
    receipt.write_text("{}", encoding="utf-8")
    old = now - 40 * DAY
    os.utime(receipt, (old, old))
    plan = build_cleanup_plan(base, policy=RetentionPolicy(), now=now)
    assert receipt in {item.path for item in plan}
    report = apply_cleanup_plan(base, plan, dry_run=False)
    assert report["status"] == "APPLIED"
    assert not receipt.exists()


def test_drive_file_boundary_reports_only_configured_state(monkeypatch, tmp_path: Path):
    secret = tmp_path / "google-drive-oauth.json"
    secret.write_text(
        json.dumps({"client_id": "a", "client_secret": "b", "refresh_token": "c"}),
        encoding="utf-8",
    )
    secret.chmod(0o640)
    monkeypatch.setenv("GOOGLE_DRIVE_OAUTH_JSON_FILE", str(secret))
    monkeypatch.delenv("GOOGLE_DRIVE_OAUTH_JSON", raising=False)
    assert credential_boundary_status() == "CONFIGURED"
    secret.chmod(0o644)
    assert credential_boundary_status() == "NOT_CONFIGURED"


def test_drive_file_boundary_rejects_symlink(monkeypatch, tmp_path: Path):
    target = tmp_path / "secret.json"
    target.write_text(json.dumps({"client_id": "a", "client_secret": "b", "refresh_token": "c"}), encoding="utf-8")
    target.chmod(0o640)
    link = tmp_path / "oauth.json"
    link.symlink_to(target)
    monkeypatch.setenv("GOOGLE_DRIVE_OAUTH_JSON_FILE", str(link))
    assert credential_boundary_status() == "NOT_CONFIGURED"


def test_drive_source_probe_requires_verifiable_provider_checksum(monkeypatch):
    monkeypatch.setattr(drive_preflight, "credential_boundary_status", lambda: "CONFIGURED")
    monkeypatch.setattr(drive_preflight, "access_token", lambda: "token")
    monkeypatch.setattr(
        drive_preflight,
        "file_metadata",
        lambda file_id, token: {"id": file_id, "mimeType": "video/mp4", "size": "1048576"},
    )
    with pytest.raises(RuntimeError, match="verifiable content checksum"):
        drive_preflight.probe_drive_source(
            "1AbCdEfGhIjKlMnOpQrStUvWxYz",
            max_source_bytes=1024**2,
            max_duration_seconds=3600,
        )


def test_compact_result_router_excludes_raw_media_and_caps_keyframes(tmp_path: Path):
    job = tmp_path / "lesson-job"
    frames = job / "frames"
    frames.mkdir(parents=True)
    (job / "manifest.json").write_text(
        json.dumps(
            {
                "status": "COMPLETED",
                "job_id": "lesson-job",
                "job_hash": "a" * 64,
                "profile": "bridge_lesson",
            }
        ),
        encoding="utf-8",
    )
    (job / "transcript.jsonl").write_text('{"text":"ok"}\n', encoding="utf-8")
    (job / "transcript.txt").write_text("ok", encoding="utf-8")
    (job / "transcript_qc.json").write_text("[]", encoding="utf-8")
    (job / "source.mp4").write_bytes(b"raw video must never publish")
    (frames / "frame-001.jpg").write_bytes(b"jpeg")

    artifacts = collect_compact_artifacts(job, max_frames=10)
    names = {item.relative_name for item in artifacts}
    assert "manifest.json" in names
    assert "transcript.txt" in names
    assert "frames/frame-001.jpg" in names
    assert "source.mp4" not in names
    assert all(not name.endswith(".mp4") for name in names)

    (frames / "frame-002.jpg").write_bytes(b"jpeg")
    with pytest.raises(RuntimeError, match="keyframe count"):
        collect_compact_artifacts(job, max_frames=1)


def test_compact_result_router_rejects_review_and_incomplete_identity(tmp_path: Path):
    job = tmp_path / "review-job"
    job.mkdir()
    for name in ("transcript.jsonl", "transcript.txt", "transcript_qc.json"):
        (job / name).write_text("[]" if name.endswith(".json") else "ok", encoding="utf-8")
    (job / "manifest.json").write_text(json.dumps({"status": "REVIEW"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="not technical COMPLETED"):
        collect_compact_artifacts(job)

    (job / "manifest.json").write_text(json.dumps({"status": "COMPLETED"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="identity is incomplete"):
        collect_compact_artifacts(job)


def test_remote_artifact_verification_rejects_same_name_wrong_content(tmp_path: Path):
    path = tmp_path / "artifact.txt"
    path.write_text("expected", encoding="utf-8")
    artifact = PublishArtifact(
        path,
        "artifact.txt",
        path.stat().st_size,
        drive_results._sha256(path),
        drive_results._md5(path),
    )
    remote = {
        "id": "remote-id",
        "size": str(path.stat().st_size),
        "md5Checksum": "0" * 32,
        "appProperties": {"sha256": artifact.sha256},
    }
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        drive_results._verify_remote_artifact(remote, artifact)

    public_remote = {
        "id": "remote-id",
        "size": str(path.stat().st_size),
        "md5Checksum": artifact.md5,
        "appProperties": {"sha256": artifact.sha256},
        "permissions": [{"type": "anyone", "role": "reader"}],
    }
    with pytest.raises(RuntimeError, match="broad ACL"):
        drive_results._verify_remote_artifact(public_remote, artifact)


def test_drive_upload_uses_multipart_related_metadata_first(tmp_path: Path, monkeypatch):
    path = tmp_path / "artifact.txt"
    path.write_text("expected media", encoding="utf-8")
    artifact = PublishArtifact(
        path,
        "artifact.txt",
        path.stat().st_size,
        drive_results._sha256(path),
        drive_results._md5(path),
    )
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "created-id"}

    def post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr(drive_results, "_find_existing_file", lambda parent, name, token: None)
    monkeypatch.setattr(
        drive_results,
        "_get_file_metadata",
        lambda file_id, token: {
            "id": file_id,
            "size": str(artifact.size_bytes),
            "md5Checksum": artifact.md5,
            "appProperties": {"sha256": artifact.sha256},
            "permissions": [{"type": "user", "role": "owner"}],
        },
    )
    monkeypatch.setattr(drive_results.requests, "post", post)

    receipt = drive_results._upload_or_verify_file("parent", artifact, "token")
    content_type = captured["headers"]["Content-Type"]
    assert content_type.startswith("multipart/related; boundary=")
    assert "files" not in captured
    assert captured["params"]["uploadType"] == "multipart"
    assert captured["params"]["supportsAllDrives"] is True
    body = captured["data"]
    assert body.index(b"Content-Type: application/json; charset=UTF-8") < body.index(b"Content-Type: text/plain")
    assert body.endswith(b"--\r\n")
    assert receipt["file_id"] == "created-id"


def test_drive_folder_with_broad_acl_fails_closed(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "id": "child",
                "mimeType": "application/vnd.google-apps.folder",
                "parents": ["parent"],
                "capabilities": {"canAddChildren": True},
                "permissions": [
                    {"type": "user", "role": "owner"},
                    {"type": "anyone", "role": "reader"},
                ],
            }

    monkeypatch.setattr(drive_results.requests, "get", lambda *args, **kwargs: Response())
    with pytest.raises(RuntimeError, match="broad ACL"):
        drive_results._verify_folder("child", "token", expected_parent_id="parent", require_writable=True)


def test_publish_writes_marker_last_and_is_deterministic_on_retry(tmp_path: Path, monkeypatch):
    job = tmp_path / "publish-job"
    job.mkdir()
    manifest = {
        "status": "COMPLETED",
        "job_id": "publish-job",
        "job_hash": "a" * 64,
        "profile": "transcript_only",
        "source_fingerprint": "b" * 64,
        "processing_fingerprint": "c" * 64,
    }
    (job / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (job / "transcript.jsonl").write_text('{"text":"ok"}\n', encoding="utf-8")
    (job / "transcript.txt").write_text("ok", encoding="utf-8")
    (job / "transcript_qc.json").write_text("[]", encoding="utf-8")

    monkeypatch.setattr(drive_results, "access_token", lambda: "token")
    monkeypatch.setattr(drive_results, "probe_destination", lambda folder, token: {"status": "PASS"})
    monkeypatch.setattr(drive_results, "_find_child_folder", lambda parent, name, token: "child-id")
    monkeypatch.setattr(drive_results, "_verify_folder", lambda *args, **kwargs: {"status": "PASS"})
    expected_bundle = drive_results.artifact_set_sha256(collect_compact_artifacts(job))
    monkeypatch.setattr(
        drive_results,
        "verify_result",
        lambda *args, **kwargs: {
            "state": "PASS",
            "artifact_set_sha256": expected_bundle,
            "domain_analysis_status": "NOT_APPLICABLE",
        },
    )
    calls: list[tuple[str, str]] = []

    def upload(parent, artifact, token):
        calls.append((artifact.relative_name, artifact.sha256))
        return {"relative_name": artifact.relative_name, "file_id": "id", "sha256": artifact.sha256}

    monkeypatch.setattr(drive_results, "_upload_or_verify_file", upload)
    inventories: list[list[str]] = []

    def verify_inventory(child, artifacts, token):
        inventories.append([item.relative_name for item in artifacts])
        return [{"relative_name": item.relative_name, "sha256": item.sha256} for item in artifacts]

    monkeypatch.setattr(drive_results, "_verify_remote_inventory", verify_inventory)

    exact = {
        "expected_job_id": "publish-job",
        "expected_profile": "transcript_only",
        "expected_job_hash": "a" * 64,
        "expected_source_file_id": None,
        "expected_artifact_set_sha256": expected_bundle,
    }
    first = publish_result(job, "parent", **exact)
    first_calls = list(calls)
    calls.clear()
    second = publish_result(job, "parent", **exact)
    assert first["status"] == "PUBLISHED_VERIFIED"
    assert first_calls[-1][0] == "PUBLICATION_COMPLETE.json"
    assert calls[-1][0] == "PUBLICATION_COMPLETE.json"
    assert "PUBLICATION_COMPLETE.json" not in inventories[0]
    assert "PUBLICATION_COMPLETE.json" in inventories[1]
    assert first["artifact_set_sha256"] == second["artifact_set_sha256"]
    assert first["publication_marker_sha256"] == second["publication_marker_sha256"]
    proof = json.loads((job / "DURABLE_PUBLICATION_PROOF.json").read_text(encoding="utf-8"))
    assert proof["status"] == "PUBLISHED_VERIFIED"
    assert proof["drive_folder_id"] == "child-id"
    assert proof["artifact_set_sha256"] == expected_bundle
    assert proof["publication_marker_sha256"] == first["publication_marker_sha256"]
    assert proof["remote_verification"] == "SIZE_MD5_SHA256_PROPERTY_MATCH"


def test_publish_fails_before_network_when_approved_bundle_changes(tmp_path: Path, monkeypatch):
    job = tmp_path / "exact-job"
    job.mkdir()
    manifest = {
        "status": "COMPLETED",
        "job_id": "exact-job",
        "job_hash": "a" * 64,
        "profile": "transcript_only",
    }
    (job / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (job / "transcript.jsonl").write_text('{"text":"ok"}\n', encoding="utf-8")
    (job / "transcript.txt").write_text("changed after approval", encoding="utf-8")
    (job / "transcript_qc.json").write_text("[]", encoding="utf-8")
    network_calls: list[str] = []
    monkeypatch.setattr(drive_results, "access_token", lambda: network_calls.append("token"))

    def reject_changed_bundle(*args, **kwargs):
        assert kwargs["expected_job_id"] == "exact-job"
        assert kwargs["expected_profile"] == "transcript_only"
        assert kwargs["expected_job_hash"] == "a" * 64
        assert kwargs["expected_artifact_set_sha256"] == "f" * 64
        raise drive_results.ResultConformanceError("artifact set hash mismatch")

    monkeypatch.setattr(drive_results, "verify_result", reject_changed_bundle)
    with pytest.raises(drive_results.ResultConformanceError, match="artifact set hash mismatch"):
        publish_result(
            job,
            "parent",
            expected_job_id="exact-job",
            expected_profile="transcript_only",
            expected_job_hash="a" * 64,
            expected_source_file_id=None,
            expected_artifact_set_sha256="f" * 64,
        )
    assert network_calls == []


def test_oracle_service_and_rollout_define_resource_and_no_asr_gates():
    root = Path(__file__).resolve().parents[1]
    unit = (root / "deploy/oracle-universal-video/universal-video.service").read_text(encoding="utf-8")
    assert "MemoryHigh=6G" in unit
    assert "MemoryMax=8G" in unit
    assert "CPUQuota=400%" in unit
    assert "CPUWeight=20" in unit
    assert "IOSchedulingClass=idle" in unit
    assert "IOWeight=20" in unit

    maintenance = (root / "deploy/oracle-universal-video/universal-video-maintenance.service").read_text(encoding="utf-8")
    timer = (root / "deploy/oracle-universal-video/universal-video-maintenance.timer").read_text(encoding="utf-8")
    assert "universal_video.maintenance" in maintenance
    assert "OnUnitActiveSec=1h" in timer
    assert "Persistent=true" in timer

    rollout = (root / "ops/oracle_universal_video_productionize.sh").read_text(encoding="utf-8")
    assert "UNIVERSAL_VIDEO_DRIVE_OAUTH=CONFIGURED" in rollout
    assert "drive_preflight source-probe" in rollout
    assert "drive_results probe-destination" in rollout
    assert "UNIVERSAL_VIDEO_DRIVE_SOURCE_NO_ASR_PASS" in rollout
    assert "UNIVERSAL_VIDEO_DDS3_NONREGRESSION_PASS" in rollout
    assert "assistant-lab-observer.service" in rollout
    assert "assistant-lab-control.service" in rollout
    assert "faster_whisper" not in rollout
    assert "run_job" not in rollout
