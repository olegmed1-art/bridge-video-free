"""Synthetic protocol fixtures only; no real video, Drive IDs, auth or receipts."""
import hashlib
import json
import os
import struct
import zlib

import pytest

from universal_video import comparison_artifacts as comparison
from universal_video import drive_results as outputs
from universal_video import durable_drive as durable
from universal_video.server_review import build_server_review
from test_universal_video_result_conformance import _bundle, _verify
from test_universal_video_durable_drive import setup as drive_setup


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def png(extra=0):
    def chunk(kind, raw):
        return struct.pack(">I", len(raw)) + kind + raw + struct.pack(">I", zlib.crc32(kind + raw))
    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) +
            chunk(b"tEXt", b"synthetic\x00" + b"x" * extra) +
            chunk(b"IDAT", zlib.compress(b"\x00\x01\x02\x03")) + chunk(b"IEND", b""))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(comparison.encoded(value))


def prepared(tmp_path, *, parent=None, large=0):
    if parent is None:
        job, manifest = _bundle(tmp_path)
    else:
        job, manifest = parent
    manifest.setdefault("source", {}).setdefault("file_id", "synthetic_original_01")
    manifest["source"]["version"] = "1"
    manifest.setdefault("media", {})["sha256"] = manifest["media"].get("sha256", "d" * 64)
    raw = tmp_path / "raw-comparison"
    raw.mkdir()
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    case = "synthetic-first-clip"
    inputs = {}
    for name in ("video", "reference", "profile", "sprite", "gold"):
        path = input_dir / name
        path.write_bytes(("synthetic-" + name).encode())
        inputs[name] = {"path": str(path), "sha256": sha(path.read_bytes())}
    revisions = {"baseline": "1" * 40, "candidate": "2" * 40}
    runner_digest = sha(b"synthetic runner")
    sealed = {"schema": "recognizer-comparison-v1", "case_id": case, "source_offset_ms": 1500,
              "gold_frozen_before_outputs": True, "inputs": inputs,
              **{v: {"root": "/synthetic/" + v, "sha": s} for v, s in revisions.items()}}
    sealed_path = input_dir / "sealed-manifest.json"
    write(sealed_path, sealed)
    seal_hash = sha(sealed_path.read_bytes())
    write(raw / "seal.json", {"schema": "recognizer-comparison-v1", "case_id": case,
          "source_offset_ms": 1500, "inputs": inputs, "runtimes": {v: sealed[v] for v in revisions},
          "gold_sha256": inputs["gold"]["sha256"], "manifest_sha256": seal_hash,
          "runner_sha256": runner_digest,
          "scope": "PRIMARY_VISUAL_ONLY; NO_ASR_AUCTION_DDS_OR_PUBLISHER"})
    runs = {}
    for variant in revisions:
        directory = raw / variant / (case + "-" + variant)
        directory.mkdir(parents=True)
        (directory / "process.log").write_bytes(b"")
        evidence = directory / "evidence"
        pair = evidence / "attempts" / "00001"
        pair.mkdir(parents=True)
        (evidence / "decoded").mkdir()
        image = png(large if variant == "candidate" else 0)
        (evidence / "decoded" / "00000.png").write_bytes(image)
        (pair / "frame-0.png").write_bytes(image)
        (pair / "frame-1.png").write_bytes(png())
        write(pair / "backend-result.json", {"status": "REJECTED", "accepted": False})
        events = [
            {"event": "FRAME_DECODED", "evidence": {"path": "decoded/00000.png", "sha256": sha(image)}},
            {"event": "PAIR_CAPTURED", "attempt": 1, "observation_timestamps_ms": [0, 600],
             "frames": [{"path": "attempts/00001/frame-0.png", "sha256": sha(image)},
                        {"path": "attempts/00001/frame-1.png", "sha256": sha(png())}]},
            {"event": "BACKEND_RETURN", "attempt": 1, "status": "REJECTED"},
            {"event": "ACCEPTANCE_RESULT", "attempt": 1, "accepted": False},
        ]
        (evidence / "events.jsonl").write_bytes(b"".join(comparison.encoded(e) for e in events))
        version = ("bridgit-primary-video-gambler-v2" if variant == "baseline" else
                   "bridgit-primary-video-gambler-v2-observation-guards-candidate2")
        status = {"status": "RETURNED", "variant": variant, "job_id": case + "-" + variant,
                  "source_sha": revisions[variant], "attempts": 1, "version": version,
                  "retained_frames": 3, "evidence_bytes": 2 * len(image) + len(png()),
                  "result_status": "NO_FULL_LAYOUT_ACCEPTED",
                  "metadata": {"fps": 30.0, "frame_count": 1800.0, "duration_seconds": 60.0}}
        write(directory / "worker-status.json", status)
        write(directory / "result.json",
              {"version": version, "status": "NO_FULL_LAYOUT_ACCEPTED", "deals": [],
               "source_size": {"width": 1, "height": 1},
               "gambler_sprite_sha256": inputs["sprite"]["sha256"],
               "template_card_size": {"width": 109.0, "height": 147.0},
               "scan_ms": 1000, "attempt_gap_ms": 15000, "canonical_promotion_allowed": False})
        runs[variant] = {**status, "exit_code": 0}
        write(raw / (variant + "-config.json"),
              {"variant": variant, "job_id": case + "-" + variant, "sha": revisions[variant],
               "source_offset_ms": 1500, "manifest_sha256": seal_hash,
               "inputs": {n: value for n, value in inputs.items() if n != "gold"}})
    write(raw / "comparison.json", {"schema": "recognizer-comparison-v1", "runs": runs,
          "manifest_sha256": seal_hash, "gold_sha256": inputs["gold"]["sha256"],
          "status": "CAPTURED_UNSCORED", "accuracy_evaluated": False, "promotion_allowed": False})
    clip_path = input_dir / "clip-binding.json"
    write(clip_path, {"schema": "universal-video-source-clip-binding-v1", "status": "PASS",
          "source_file_id": manifest["source"]["file_id"], "source_version": "1",
          "source_sha256": manifest["media"]["sha256"], "clip_sha256": inputs["video"]["sha256"],
          "source_offset_ms": 1500, "duration_ms": 60000,
          "verification_receipt_sha256": sha(b"synthetic independent mapping fixture")})
    args = {"sealed_manifest_path": sealed_path, "sealed_manifest_sha256": seal_hash,
            "runner_commit": "3" * 40, "runner_sha256": runner_digest,
            "job_id": manifest["job_id"], "job_hash": manifest["job_hash"],
            "source_file_id": manifest["source"]["file_id"], "source_version": "1",
            "source_sha256": manifest["media"]["sha256"],
            "clip_binding_path": clip_path, "clip_binding_sha256": sha(clip_path.read_bytes())}
    return job, manifest, raw, args


def attach(prep, *, with_review=True):
    job, manifest, raw, args = prep
    declaration = comparison.build_comparison_package(raw, job / "comparison", **args)
    manifest["comparison_artifacts"] = declaration
    write(job / "manifest.json", manifest)
    paths = comparison.collect_comparison_paths(job, manifest)
    if with_review and manifest.get("contract") == "universal-video-v1":
        report = _verify(job, evidence_phase="GENERATION_FINALIZATION")
        write(job / "server_review.json", build_server_review(job, report))
    return paths


def test_rejected_pairs_are_lossless_and_large_png_uses_existing_file_caps(tmp_path):
    prep = prepared(tmp_path, large=6 * 1024**2)
    paths = attach(prep)
    assert len(paths) > 3
    artifacts = outputs.collect_compact_artifacts(prep[0])
    assert all(a.size_bytes <= outputs.MULTIPART_UPLOAD_MAX_BYTES for a in artifacts)
    assert sum(a.size_bytes for a in artifacts) <= comparison.MAX_TOTAL_BYTES
    index = comparison.decode(paths[0].read_bytes())
    names = {row["path"] for row in index["files"]}
    assert sum("/attempts/" in n and n.endswith(".png") for n in names) == 4
    assert all(any(n.startswith(v + "/") and n.endswith("backend-result.json") for n in names)
               for v in ("baseline", "candidate"))
    assert index["accuracy_evaluated"] is False


def test_conformance_and_review_include_same_typed_inventory(tmp_path):
    prep = prepared(tmp_path)
    attach(prep, with_review=False)
    job = prep[0]
    before = _verify(job, evidence_phase="GENERATION_FINALIZATION")
    assert any(a["relative_name"] == "comparison/index.json" for a in before["artifacts"])
    write(job / "server_review.json", build_server_review(job, before))
    after = _verify(job, require_server_review=True)
    artifacts = outputs.collect_compact_artifacts(job)
    assert after["artifact_set_sha256"] == outputs.artifact_set_sha256(artifacts)
    assert after["domain_analysis_status"] == "DEFERRED"


@pytest.mark.parametrize("mode", ["part-hash", "missing", "extra", "index-hash", "symlink", "hardlink"])
def test_tampered_package_rejected(tmp_path, mode):
    prep = prepared(tmp_path)
    attach(prep)
    root = prep[0] / "comparison"
    part = root / "part-00000.bin"
    if mode == "part-hash":
        part.write_bytes(part.read_bytes() + b"x")
    elif mode == "missing":
        part.unlink()
    elif mode == "extra":
        (root / "unexpected.bin").write_bytes(b"extra")
    elif mode == "index-hash":
        (root / "index.json").write_bytes(b"{}")
    elif mode == "symlink":
        original = tmp_path / "original-part"
        part.rename(original)
        part.symlink_to(original)
    else:
        os.link(part, tmp_path / "part-link")
    with pytest.raises((RuntimeError, OSError)):
        outputs.collect_compact_artifacts(prep[0])
    with pytest.raises(Exception):
        _verify(prep[0])


@pytest.mark.parametrize("field", ["job_id", "job_hash", "source_id", "source_hash", "version"])
def test_wrong_parent_binding_rejected(tmp_path, field):
    prep = prepared(tmp_path)
    attach(prep)
    manifest = prep[1]
    if field in {"job_id", "job_hash"}:
        manifest[field] = "other" if field == "job_id" else "f" * 64
    elif field == "source_id":
        manifest["source"]["file_id"] = "synthetic_other_01"
    elif field == "source_hash":
        manifest["media"]["sha256"] = "f" * 64
    else:
        manifest["source"]["version"] = "2"
    write(prep[0] / "manifest.json", manifest)
    with pytest.raises(RuntimeError):
        outputs.collect_compact_artifacts(prep[0])


@pytest.mark.parametrize("content", [b"\n", b"Bearer synthetic-private-token", b"ordinary diagnostic text"])
def test_raw_logs_never_published_or_modified(tmp_path, content):
    prep = prepared(tmp_path)
    log = next(prep[2].glob("baseline/*/process.log"))
    log.write_bytes(content)
    with pytest.raises(RuntimeError, match="nonempty raw process log"):
        attach(prep)
    assert log.read_bytes() == content
    assert not (prep[0] / "comparison").exists()


def test_missing_rejected_pair_blocks_build_and_retains_other_evidence(tmp_path):
    prep = prepared(tmp_path)
    frame = next(prep[2].glob("candidate/*/evidence/attempts/*/frame-1.png"))
    frame.unlink()
    with pytest.raises(RuntimeError, match="attempted or rejected"):
        attach(prep)
    assert next(prep[2].glob("candidate/*/evidence/attempts/*/frame-0.png")).exists()


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}'])
def test_strict_json(raw):
    with pytest.raises(RuntimeError):
        comparison.decode(raw)


@pytest.mark.parametrize("path", ["../secret", "/secret", "a//b", "a/./b", "a\\b", "a:b", "é.png"])
def test_traversal_and_ambiguous_names(path):
    with pytest.raises(RuntimeError):
        comparison.safe_path(path)


def test_total_quota_fails_without_truncating_or_deleting_source(tmp_path, monkeypatch):
    prep = prepared(tmp_path)
    before = {p.relative_to(prep[2]).as_posix(): p.read_bytes() for p in prep[2].rglob("*") if p.is_file()}
    monkeypatch.setattr(comparison, "MAX_TOTAL_BYTES", 100)
    with pytest.raises(RuntimeError, match="quota"):
        attach(prep)
    assert before == {p.relative_to(prep[2]).as_posix(): p.read_bytes() for p in prep[2].rglob("*") if p.is_file()}


def test_combined_core_plus_comparison_quota(tmp_path):
    prep = prepared(tmp_path)
    attach(prep)
    core = outputs.collect_compact_artifacts(prep[0])
    total = sum(x.size_bytes for x in core)
    with pytest.raises(RuntimeError, match="total byte cap"):
        outputs.collect_compact_artifacts(prep[0], max_total_bytes=total - 1)


def test_publication_retry_readback_and_cleanup_cover_comparison(drive_setup, tmp_path):
    job, source, backend, binding = drive_setup
    manifest = json.loads((job / "manifest.json").read_text())
    prep = prepared(tmp_path, parent=(job, manifest))
    attach(prep)
    proof = durable.finalize_drive_job(job, source, binding, job_id="job",
                                      profile="transcript_only", job_hash="a" * 64)
    count = len(backend.posts)
    assert durable.cleanup_proof_matches(job, source, job_id="job")
    assert any("comparison__part-" in item["name"] and item["parents"] == [binding["folders"]["analysis"]]
               for _, item in backend.posts)
    again = durable.finalize_drive_job(job, source, binding, job_id="job",
                                      profile="transcript_only", job_hash="a" * 64)
    assert proof["artifact_set_sha256"] == again["artifact_set_sha256"]
    assert len(backend.posts) == count
    backend.fail_readback = True
    with pytest.raises(Exception):
        durable.finalize_drive_job(job, source, binding, job_id="job",
                                  profile="transcript_only", job_hash="a" * 64)
    assert not durable.cleanup_proof_matches(job, source, job_id="job")
    backend.fail_readback = False
    durable.finalize_drive_job(job, source, binding, job_id="job",
                             profile="transcript_only", job_hash="a" * 64)
    part = job / "comparison" / "part-00000.bin"
    part.write_bytes(b"changed")
    assert not durable.cleanup_proof_matches(job, source, job_id="job")


def test_legacy_inventory_unchanged_without_declaration(tmp_path):
    job, manifest = _bundle(tmp_path)
    assert comparison.collect_comparison_paths(job, manifest) == []
    before = _verify(job)
    assert all(not x["relative_name"].startswith("comparison/") for x in before["artifacts"])


def test_undeclared_package_and_duplicate_parent_key_rejected(tmp_path):
    prep = prepared(tmp_path)
    attach(prep)
    job, manifest = prep[:2]
    original = (job / "manifest.json").read_bytes()
    (job / "manifest.json").write_bytes(original.rstrip()[:-1] + b',"job_id":"other"}')
    with pytest.raises(RuntimeError, match="duplicate JSON key"):
        outputs.collect_compact_artifacts(job)
    manifest.pop("comparison_artifacts")
    write(job / "manifest.json", manifest)
    with pytest.raises(RuntimeError, match="undeclared"):
        outputs.collect_compact_artifacts(job)

def test_independent_roundtrip_reconstructs_every_byte(tmp_path):
    prep = prepared(tmp_path, large=3 * 1024**2)
    paths = attach(prep)
    index = json.loads(paths[0].read_bytes())
    stream = b"".join(path.read_bytes() for path in paths[1:])
    expected = {p.relative_to(prep[2]).as_posix(): p.read_bytes()
                for p in prep[2].rglob("*") if p.is_file()}
    expected["source-clip-binding.json"] = prep[3]["clip_binding_path"].read_bytes()
    actual = {row["path"]: stream[row["offset"]:row["offset"] + row["size_bytes"]]
              for row in index["files"]}
    assert actual == expected
    assert index["file_count"] == len(expected)
    assert index["total_bytes"] == sum(map(len, expected.values()))


@pytest.mark.parametrize("mode", ["unsafe-file", "symlink-dir", "hardlink-file", "input-changed",
                                   "gold-unsealed", "clip-mismatch", "missing-backend"])
def test_raw_input_and_mapping_gates(tmp_path, mode):
    prep = prepared(tmp_path)
    job, manifest, raw, args = prep
    if mode == "unsafe-file":
        (raw / "secret.env").write_bytes(b"synthetic")
    elif mode == "symlink-dir":
        path = next(raw.glob("candidate/*/evidence/decoded"))
        path.rename(tmp_path / "decoded-outside")
        path.symlink_to(tmp_path / "decoded-outside", target_is_directory=True)
    elif mode == "hardlink-file":
        os.link(next(raw.glob("baseline/*/evidence/attempts/*/frame-0.png")), tmp_path / "alias")
    elif mode == "input-changed":
        (tmp_path / "inputs" / "gold").write_bytes(b"changed")
    elif mode == "gold-unsealed":
        value = json.loads(args["sealed_manifest_path"].read_text())
        value["gold_frozen_before_outputs"] = False
        write(args["sealed_manifest_path"], value)
        args["sealed_manifest_sha256"] = sha(args["sealed_manifest_path"].read_bytes())
    elif mode == "clip-mismatch":
        value = json.loads(args["clip_binding_path"].read_text())
        value["clip_sha256"] = "f" * 64
        write(args["clip_binding_path"], value)
        args["clip_binding_sha256"] = sha(args["clip_binding_path"].read_bytes())
    else:
        next(raw.glob("baseline/*/evidence/attempts/*/backend-result.json")).unlink()
    with pytest.raises((RuntimeError, OSError)):
        attach(prep)
    assert not (job / "comparison").exists()


def test_credential_like_json_is_rejected(tmp_path):
    prep = prepared(tmp_path)
    path = next(prep[2].glob("candidate/*/result.json"))
    write(path, {"status": "UNAVAILABLE", "access_token": "synthetic-secret"})
    with pytest.raises(RuntimeError, match="credential-like"):
        attach(prep)


@pytest.mark.parametrize("raw", [
    b'{"\\u0061ccess_token":"synthetic-secret"}',
    b'{"detail":"\\u0042earer synthetic-secret"}',
])
def test_escaped_credentials_cannot_bypass_decoded_json_gate(raw):
    with pytest.raises(RuntimeError, match="credential-like"):
        comparison.decode(raw)


def test_package_version_must_match_actual_intake_pin_before_writes(drive_setup, tmp_path):
    job, source, backend, binding = drive_setup
    manifest = json.loads((job / "manifest.json").read_text())
    prep = prepared(tmp_path, parent=(job, manifest))
    attach(prep)
    pin = durable.read_receipt(source / durable.SOURCE_RECEIPT)
    pin["original"]["version"] = "2"
    durable.atomic_json(source / durable.SOURCE_RECEIPT, pin)
    backend.meta["version"] = "2"
    with pytest.raises(RuntimeError, match="intake pin mismatch"):
        durable.finalize_drive_job(job, source, binding, job_id="job",
                                  profile="transcript_only", job_hash="a" * 64)
    assert backend.posts == []
    assert not durable.cleanup_proof_matches(job, source, job_id="job")


@pytest.mark.parametrize("mode", ["remove-success-evidence", "wrong-summary", "array-result",
                                   "missing-screenshot", "changed-screenshot"])
def test_error_side_cannot_hide_success_evidence_or_bad_results(tmp_path, mode):
    prep = prepared(tmp_path)
    raw = prep[2]
    summary_path = raw / "comparison.json"
    summary = json.loads(summary_path.read_text())
    summary["status"] = "REPLAY_ERROR"
    summary["runs"]["candidate"] = {"status": "TIMEOUT", "exit_code": None}
    write(summary_path, summary)
    base = next(raw.glob("baseline/*"))
    if mode == "remove-success-evidence":
        for path in sorted(base.rglob("*"), reverse=True):
            if path.is_file() and path.name != "process.log":
                path.unlink()
    elif mode == "wrong-summary":
        summary["runs"]["baseline"]["attempts"] = 0
        write(summary_path, summary)
    elif mode == "array-result":
        write(base / "result.json", [])
    else:
        result = json.loads((base / "result.json").read_text())
        result["status"] = "PRIMARY_COMPLETE"
        result["deals"] = [{"timestamp_ms": 0, "canonical_promotion_allowed": False,
                           "screenshot": "/synthetic/recognizer/primary_0000000000.png",
                           "screenshot_sha256": sha(png())}]
        write(base / "result.json", result)
        status = json.loads((base / "worker-status.json").read_text())
        status["result_status"] = "PRIMARY_COMPLETE"
        write(base / "worker-status.json", status)
        summary["runs"]["baseline"] = {**status, "exit_code": 0}
        write(summary_path, summary)
        if mode == "changed-screenshot":
            screenshot = base / "recognizer" / "primary_0000000000.png"
            screenshot.parent.mkdir()
            screenshot.write_bytes(png(1))
    with pytest.raises(RuntimeError):
        attach(prep)
    assert not (prep[0] / "comparison").exists()


def test_failed_comparison_blocks_publication_and_retains_success_side(tmp_path):
    prep = prepared(tmp_path)
    path = prep[2] / "comparison.json"
    summary = json.loads(path.read_text())
    summary["status"] = "REPLAY_ERROR"
    summary["runs"]["candidate"] = {"status": "TIMEOUT", "exit_code": None}
    write(path, summary)
    before = {p.relative_to(prep[2]).as_posix(): p.read_bytes() for p in prep[2].rglob("*") if p.is_file()}
    with pytest.raises(RuntimeError, match="incomplete comparison"):
        attach(prep)
    assert before == {p.relative_to(prep[2]).as_posix(): p.read_bytes() for p in prep[2].rglob("*") if p.is_file()}
    assert not (prep[0] / "comparison").exists()


@pytest.mark.parametrize("raw", [
    b'{"detail":"\\u0042earer\\tsynthetic-secret"}',
    b'{"detail":"\\u0042earer\\nsynthetic-secret"}',
])
def test_escaped_bearer_whitespace_is_screened_as_decoded_string(raw):
    with pytest.raises(RuntimeError, match="credential-like"):
        comparison.decode(raw)


@pytest.mark.parametrize("mode", ["returned-timeout", "missing-decoded"])
def test_available_worker_receipt_always_binds_complete_evidence(tmp_path, mode):
    prep = prepared(tmp_path)
    raw = prep[2]
    path = raw / "comparison.json"
    summary = json.loads(path.read_text())
    directory = next(raw.glob("candidate/*"))
    if mode == "returned-timeout":
        summary["status"] = "REPLAY_ERROR"
        summary["runs"]["candidate"] = {"status": "TIMEOUT", "exit_code": None}
        write(path, summary)
        for file in (directory / "evidence").rglob("*"):
            if file.is_file():
                file.unlink()
    else:
        events = directory / "evidence" / "events.jsonl"
        rows = [json.loads(line) for line in events.read_bytes().splitlines()]
        events.write_bytes(b"".join(comparison.encoded(r) for r in rows if r["event"] != "FRAME_DECODED"))
        next((directory / "evidence" / "decoded").glob("*.png")).unlink()
    with pytest.raises(RuntimeError):
        attach(prep)


def test_declared_comparison_still_honors_smaller_per_file_quota(tmp_path):
    prep = prepared(tmp_path, large=1024 * 1024)
    attach(prep)
    with pytest.raises(RuntimeError, match="per-file cap"):
        outputs.collect_compact_artifacts(prep[0], max_file_bytes=1024 * 1024)


@pytest.mark.parametrize("key", ["dsn", "clientSecret", "database_url", "access token", "access\ttoken"])
def test_decoded_credential_key_normalization(key):
    with pytest.raises(RuntimeError, match="credential-like"):
        comparison.decode(json.dumps({key: "synthetic-private-value"}).encode())


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("null_declaration", [False, True])
def test_actual_legacy_publish_cli_rejects_comparison_before_drive_access(tmp_path, monkeypatch, dry_run, null_declaration):
    if null_declaration:
        job, manifest = _bundle(tmp_path)
        manifest["comparison_artifacts"] = None
        write(job / "manifest.json", manifest)
        report = _verify(job, evidence_phase="GENERATION_FINALIZATION")
        write(job / "server_review.json", build_server_review(job, report))
    else:
        prep = prepared(tmp_path)
        attach(prep)
        job, manifest = prep[:2]
    bundle = outputs.artifact_set_sha256(outputs.collect_compact_artifacts(job))
    def forbidden(*args, **kwargs):
        pytest.fail("legacy comparison reached Drive auth or mutation")
    for name in ("access_token", "probe_destination", "_create_folder", "_upload_or_verify_file"):
        monkeypatch.setattr(outputs, name, forbidden)
    argv = ["drive-results", "publish", "--folder-id", "synthetic_destination_01",
            "--job-dir", str(job), "--expected-job-id", manifest["job_id"],
            "--expected-profile", manifest["profile"], "--expected-job-hash", manifest["job_hash"],
            "--expected-source-file-id", manifest["source"]["file_id"],
            "--expected-artifact-set-sha256", bundle]
    if dry_run:
        argv.append("--dry-run")
    monkeypatch.setattr("sys.argv", argv)
    with pytest.raises(RuntimeError, match="requires protected durable finalization"):
        outputs.main()
    assert not (job / "DURABLE_PUBLICATION_PROOF.json").exists()


@pytest.mark.parametrize("state", ["ERROR", "TIMEOUT", "PROCESS_ERROR"])
def test_failed_worker_without_events_cannot_be_complete_package(tmp_path, state):
    prep = prepared(tmp_path)
    directory = next(prep[2].glob("candidate/*"))
    for path in (directory / "evidence").rglob("*"):
        if path.is_file():
            path.unlink()
    summary_path = prep[2] / "comparison.json"
    summary = json.loads(summary_path.read_text())
    summary["status"] = "REPLAY_ERROR"
    summary["runs"]["candidate"] = {"status": state, "exit_code": 1 if state == "ERROR" else None}
    write(summary_path, summary)
    if state == "ERROR":
        status = {"status": "ERROR", "variant": "candidate", "job_id": "synthetic-first-clip-candidate",
                  "source_sha": "2" * 40}
        write(directory / "worker-status.json", status)
        summary["runs"]["candidate"] = {**status, "exit_code": 1}
        write(summary_path, summary)
    else:
        (directory / "worker-status.json").unlink()
    with pytest.raises(RuntimeError, match="incomplete comparison"):
        attach(prep)
    assert not (prep[0] / "comparison").exists()
    assert not (prep[0] / durable.FINAL_RECEIPT).exists()


def r3_prepared(tmp_path, *, parent=None, candidate3=True, with_auction=True):
    prep = prepared(tmp_path, parent=parent)
    job, manifest, raw, args = prep
    sealed = json.loads(args["sealed_manifest_path"].read_text())
    profile_path = tmp_path / "inputs" / "profile"
    calibration = {"schema": "bridge-auction-cell-profile/v1",
                   "reference_pixel_sha256": "a" * 64, "synthetic_calibration": True}
    write(profile_path, {"auction": calibration} if with_auction else {})
    sealed["inputs"]["profile"]["sha256"] = sha(profile_path.read_bytes())
    write(args["sealed_manifest_path"], sealed)
    args["sealed_manifest_sha256"] = sha(args["sealed_manifest_path"].read_bytes())
    seal = json.loads((raw / "seal.json").read_text())
    seal.update(inputs=sealed["inputs"], manifest_sha256=args["sealed_manifest_sha256"],
                scope=comparison.AUCTION_SCOPE, runner_version=comparison.AUCTION_RUNNER)
    write(raw / "seal.json", seal)
    summary = json.loads((raw / "comparison.json").read_text())
    summary.update(manifest_sha256=args["sealed_manifest_sha256"],
                   scope=comparison.AUCTION_SCOPE, runner_version=comparison.AUCTION_RUNNER)
    for variant in ("baseline", "candidate"):
        directory = next(raw.glob(variant + "/*"))
        output = "/synthetic/" + variant + "/" + sealed["case_id"] + "-" + variant
        config = json.loads((raw / (variant + "-config.json")).read_text())
        config.update(manifest_sha256=args["sealed_manifest_sha256"], output=output,
                      inputs={n: v for n, v in sealed["inputs"].items() if n != "gold"})
        write(raw / (variant + "-config.json"), config)
        status = json.loads((directory / "worker-status.json").read_text())
        status.update(scope=comparison.AUCTION_SCOPE, runner_version=comparison.AUCTION_RUNNER,
                      accuracy_evaluated=False, auction_result_status="NOT_REPORTED")
        if variant == "candidate" and candidate3:
            result = json.loads((directory / "result.json").read_text())
            result["version"] = comparison.AUCTION_VERSION
            status["version"] = comparison.AUCTION_VERSION
            auction = {"status": "UNAVAILABLE", "reason": "NO_AUCTION_PROFILE", "auctions": []}
            if with_auction:
                timestamp = 1000
                pixels = "b" * 64
                filename = f"auction-{timestamp:010d}-{pixels[:12]}.png"
                evidence = directory / "recognizer" / "auction-evidence" / filename
                evidence.parent.mkdir(parents=True)
                evidence.write_bytes(png())
                path = output + "/recognizer/auction-evidence/" + filename
                reference = {"timestamp_ms": timestamp, "frame_sha256": sha(png()), "frame_path": path}
                observation = dict(reference, frame_pixel_sha256=pixels, cells=[],
                                   start_visible=False, orientation_known=False)
                occurrence = {"source_id": sealed["inputs"]["video"]["sha256"],
                              "board_occurrence_id": "c" * 64, "anchor_pixel_sha256": "d" * 64,
                              "start_ms": timestamp, "end_ms": timestamp,
                              "latest_observation_index": 0, "latest_observation_timestamp_ms": timestamp,
                              "canonical_promotion_allowed": False, "complete": False,
                              "status": "PARTIAL", "observations": [observation],
                              "visible_fragments": [dict(reference, calls=[])], "ordered_calls": []}
                auction = {"schema": "bridge-visual-auction/v3", "status": "OBSERVED",
                           "source_id": sealed["inputs"]["video"]["sha256"],
                           "calibration_profile": calibration,
                           "profile_sha256": sha(json.dumps(calibration, sort_keys=True).encode()),
                           "reference_pixel_sha256": calibration["reference_pixel_sha256"],
                           "coverage_issues": [], "canonical_promotion_allowed": False,
                           "confidence_kind": "UNCALIBRATED_TEMPLATE_SIMILARITY",
                           "supported_layout": "CALIBRATED_FIXED_FOUR_COLUMN_TABLE_ONLY",
                           "auctions": [occurrence]}
            result["auction_recognition"] = auction
            status["auction_result_status"] = auction["status"]
            write(directory / "result.json", result)
        write(directory / "worker-status.json", status)
        summary["runs"][variant] = {**status, "exit_code": 0}
    write(raw / "comparison.json", summary)
    return prep


@pytest.mark.parametrize("candidate3,with_auction", [(True, True), (True, False), (False, True)])
def test_r3_package_preserves_exact_profile_and_auction_bytes(tmp_path, candidate3, with_auction):
    prep = r3_prepared(tmp_path, candidate3=candidate3, with_auction=with_auction)
    paths = attach(prep)
    index = json.loads(paths[0].read_bytes())
    stream = b"".join(p.read_bytes() for p in paths[1:])
    actual = {r["path"]: stream[r["offset"]:r["offset"] + r["size_bytes"]] for r in index["files"]}
    assert actual["embedded-profile.json"] == (tmp_path / "inputs" / "profile").read_bytes()
    expected = {p.relative_to(prep[2]).as_posix(): p.read_bytes() for p in prep[2].rglob("*") if p.is_file()}
    assert all(actual[n] == value for n, value in expected.items())
    assert index["accuracy_evaluated"] is False
    assert index["promotion_allowed"] is False


@pytest.mark.parametrize("mode", ["missing", "tampered", "extra", "unsafe-path", "wrong-output",
    "wrong-pixels", "wrong-timestamp", "nested-reference", "duplicate", "profile", "profile-digest",
    "source", "worker-status", "scope", "runner-version", "truncated", "partial-coverage",
    "missing-auction", "old-scope", "orphan-baseline", "unavailable-with-calibration"])
def test_r3_binding_and_incomplete_evidence_fail_closed(tmp_path, mode):
    prep = r3_prepared(tmp_path)
    job, manifest, raw, args = prep
    directory = next(raw.glob("candidate/*"))
    result_path = directory / "result.json"
    result = json.loads(result_path.read_text())
    auction = result["auction_recognition"]
    occurrence = auction["auctions"][0]
    obs = occurrence["observations"][0]
    image = next(directory.glob("recognizer/auction-evidence/*.png"))
    if mode == "missing":
        image.unlink()
    elif mode == "tampered":
        image.write_bytes(png(1))
    elif mode == "extra":
        (image.parent / ("auction-0000002000-" + "e" * 12 + ".png")).write_bytes(png())
    elif mode == "unsafe-path":
        obs["frame_path"] = "/synthetic/../" + obs["frame_path"]
    elif mode == "wrong-output":
        config_path = raw / "candidate-config.json"
        config = json.loads(config_path.read_text())
        config["output"] = "/synthetic/other/candidate/synthetic-first-clip-candidate"
        write(config_path, config)
    elif mode == "wrong-pixels":
        obs["frame_pixel_sha256"] = "f" * 64
    elif mode == "wrong-timestamp":
        obs["timestamp_ms"] = 2000
    elif mode == "nested-reference":
        occurrence["visible_fragments"][0]["frame_sha256"] = "f" * 64
    elif mode == "duplicate":
        occurrence["observations"].append(dict(obs))
    elif mode == "profile":
        auction["calibration_profile"]["synthetic_calibration"] = False
    elif mode == "profile-digest":
        auction["profile_sha256"] = "f" * 64
    elif mode == "source":
        auction["source_id"] = "f" * 64
    elif mode in {"worker-status", "scope", "runner-version"}:
        path = directory / "worker-status.json" if mode == "worker-status" else raw / "seal.json"
        receipt = json.loads(path.read_text())
        receipt["auction_result_status" if mode == "worker-status" else "scope" if mode == "scope" else "runner_version"] = "UNSUPPORTED"
        write(path, receipt)
        if mode == "worker-status":
            summary = json.loads((raw / "comparison.json").read_text())
            summary["runs"]["candidate"] = {**receipt, "exit_code": 0}
            write(raw / "comparison.json", summary)
    elif mode in {"truncated", "partial-coverage"}:
        auction["status"] = "TRUNCATED" if mode == "truncated" else "PARTIAL_COVERAGE"
    elif mode == "missing-auction":
        result.pop("auction_recognition")
    elif mode == "old-scope":
        seal = json.loads((raw / "seal.json").read_text())
        seal["scope"] = comparison.LEGACY_SCOPE
        write(raw / "seal.json", seal)
    elif mode == "orphan-baseline":
        path = next(raw.glob("baseline/*")) / "recognizer" / "auction-evidence" / image.name
        path.parent.mkdir(parents=True)
        path.write_bytes(png())
    else:
        result["auction_recognition"] = {"status": "UNAVAILABLE", "reason": "NO_AUCTION_PROFILE", "auctions": []}
    write(result_path, result)
    before = {p.relative_to(raw).as_posix(): p.read_bytes() for p in raw.rglob("*") if p.is_file()}
    with pytest.raises(RuntimeError):
        attach(prep)
    assert before == {p.relative_to(raw).as_posix(): p.read_bytes() for p in raw.rglob("*") if p.is_file()}
    assert not (job / "comparison").exists()
    assert not (job / durable.FINAL_RECEIPT).exists()


@pytest.mark.parametrize("limit", ["bytes", "snapshots"])
def test_r3_auction_has_separate_quota_without_modifying_source(tmp_path, monkeypatch, limit):
    prep = r3_prepared(tmp_path)
    monkeypatch.setattr(comparison, "MAX_AUCTION_BYTES" if limit == "bytes" else "MAX_AUCTION_SNAPSHOTS", 1 if limit == "bytes" else 0)
    before = next(prep[2].glob("candidate/*/recognizer/auction-evidence/*.png")).read_bytes()
    with pytest.raises(RuntimeError, match="auction evidence quota"):
        attach(prep)
    assert next(prep[2].glob("candidate/*/recognizer/auction-evidence/*.png")).read_bytes() == before


def test_r3_unpack_rechecks_sealed_profile_membership(tmp_path):
    prep = r3_prepared(tmp_path)
    paths = attach(prep)
    index = json.loads(paths[0].read_bytes())
    stream = b"".join(p.read_bytes() for p in paths[1:])
    files = {r["path"]: stream[r["offset"]:r["offset"] + r["size_bytes"]] for r in index["files"]}
    files["embedded-profile.json"] = comparison.encoded({"auction": {}})
    with pytest.raises(RuntimeError, match="sealed embedded profile"):
        comparison.validate_files(files, index["binding"])


def test_r3_durable_retry_and_tamper_invalidate_cleanup(drive_setup, tmp_path):
    job, source, backend, binding = drive_setup
    manifest = json.loads((job / "manifest.json").read_text())
    prep = r3_prepared(tmp_path, parent=(job, manifest))
    attach(prep)
    first = durable.finalize_drive_job(job, source, binding, job_id="job",
                                      profile="transcript_only", job_hash="a" * 64)
    count = len(backend.posts)
    again = durable.finalize_drive_job(job, source, binding, job_id="job",
                                      profile="transcript_only", job_hash="a" * 64)
    assert first["artifact_set_sha256"] == again["artifact_set_sha256"]
    assert len(backend.posts) == count
    assert durable.cleanup_proof_matches(job, source, job_id="job")
    (job / "comparison" / "part-00000.bin").write_bytes(b"changed")
    assert not durable.cleanup_proof_matches(job, source, job_id="job")


@pytest.mark.parametrize("mode", ["empty-observations", "unknown-status", "complete-mismatch",
                                  "fragments-type", "calls-type"])
def test_r3_occurrence_cannot_claim_evidence_with_empty_or_invalid_inventory(tmp_path, mode):
    prep = r3_prepared(tmp_path)
    directory = next(prep[2].glob("candidate/*"))
    path = directory / "result.json"
    result = json.loads(path.read_text())
    occurrence = result["auction_recognition"]["auctions"][0]
    if mode == "empty-observations":
        occurrence.update(observations=[], visible_fragments=[], complete=True, status="COMPLETE_CONFIRMED")
        next(directory.glob("recognizer/auction-evidence/*.png")).unlink()
    elif mode == "unknown-status":
        occurrence["status"] = "PASS"
    elif mode == "complete-mismatch":
        occurrence["complete"] = True
    elif mode == "fragments-type":
        occurrence["visible_fragments"] = None
    else:
        occurrence["ordered_calls"] = {}
    write(path, result)
    with pytest.raises(RuntimeError):
        attach(prep)
    assert not (prep[0] / "comparison").exists()


@pytest.mark.parametrize("field,value", [("start_ms", 0), ("latest_observation_index", 1),
    ("latest_observation_timestamp_ms", 999)])
def test_r3_occurrence_interval_and_latest_observation_bind(tmp_path, field, value):
    prep = r3_prepared(tmp_path)
    directory = next(prep[2].glob("candidate/*"))
    path = directory / "result.json"
    result = json.loads(path.read_text())
    result["auction_recognition"]["auctions"][0][field] = value
    write(path, result)
    with pytest.raises(RuntimeError):
        attach(prep)
