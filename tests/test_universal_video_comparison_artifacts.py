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
    manifest.setdefault("source", {})["file_id"] = manifest["source"].get("file_id", "synthetic_original_01")
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
        status = {"status": "RETURNED", "variant": variant, "job_id": case + "-" + variant,
                  "source_sha": revisions[variant], "attempts": 1}
        write(directory / "worker-status.json", status)
        write(directory / "result.json", {"status": "NO_ACCEPTED_DEALS", "deals": []})
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


def attach(prep):
    job, manifest, raw, args = prep
    declaration = comparison.build_comparison_package(raw, job / "comparison", **args)
    manifest["comparison_artifacts"] = declaration
    write(job / "manifest.json", manifest)
    return comparison.collect_comparison_paths(job, manifest)


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
    attach(prep)
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
