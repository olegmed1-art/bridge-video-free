import json
import os
from pathlib import Path

import pytest

import universal_video.drive_preflight as drive_preflight
from universal_video.drive_preflight import credential_boundary_status
from universal_video.drive_results import collect_compact_artifacts
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

    policy = RetentionPolicy(max_deletes_per_run=100)
    plan = build_cleanup_plan(base, policy=policy, now=now)
    paths = {item.path for item in plan}
    assert active_media not in paths
    assert active_result not in paths
    assert stale_media in paths
    assert stale_result in paths


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
    receipt = base / "spool" / "done" / "old.json"
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
    (job / "manifest.json").write_text(json.dumps({"status": "COMPLETED"}), encoding="utf-8")
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


def test_oracle_service_and_rollout_define_resource_and_no_asr_gates():
    root = Path(__file__).resolve().parents[1]
    unit = (root / "deploy/oracle-universal-video/universal-video.service").read_text(encoding="utf-8")
    assert "MemoryHigh=12G" in unit
    assert "MemoryMax=16G" in unit
    assert "CPUQuota=600%" in unit
    assert "IOSchedulingClass=idle" in unit

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
