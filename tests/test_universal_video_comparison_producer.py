"""Synthetic orchestration controls; no real gold/media/Drive/IBM/DB jobs.

Only the runner process and external Drive transport are substituted. Typed
packaging, result conformance, review and worker completion gates stay real.
"""
import hashlib
from pathlib import Path
import shutil
import subprocess

import pytest

from universal_video import comparison_producer as producer, durable_drive as durable
from universal_video.comparison_requirement import require_comparison_package
from universal_video.contract import canonical_job_hash, validate_job
from universal_video.result_conformance import verify_result
from universal_video.server_review import build_server_review
from test_universal_video_comparison_artifacts import prepared, write
from test_universal_video_result_conformance import _bundle, _verify, _fingerprint
from test_universal_video_durable_drive import setup as drive_setup


def configure(tmp_path, monkeypatch, prep, source):
    job, manifest, raw, args = prep
    manifest["metadata"] = {"comparison_required": True}
    write(job / "manifest.json", manifest)
    pin = {"schema": "universal-video-source-integrity-v1",
           "job_id": manifest["job_id"], "job_hash": manifest["job_hash"],
           "comparison_required": True, "sha256": args["source_sha256"],
           "original": {"id": args["source_file_id"], "version": args["source_version"]}}
    if not (source / durable.SOURCE_RECEIPT).exists():
        write(source / durable.SOURCE_RECEIPT, pin)
    config = {**args, "runner_root": str(tmp_path / "runner"),
              "replay_authorized": True, "timeout_seconds": 3}
    for key in ("sealed_manifest_path", "clip_binding_path"):
        config[key] = str(config[key])
    registry = tmp_path / "comparison-producers.json"
    write(registry, {"schema": producer.SCHEMA, "jobs": [config]})
    registry.chmod(0o600)
    monkeypatch.setenv(producer.CONFIG_ENV, str(registry))
    calls = []
    monkeypatch.setattr(producer, "_runner_file", lambda config: tmp_path / "synthetic-runner.py")
    def launch(config, runner, destination, log):
        calls.append(destination)
        log.write_bytes(b"synthetic transport; no actual media processed\n")
        shutil.copytree(raw, destination)
    monkeypatch.setattr(producer, "_launch", launch)
    kwargs = {"job_id": args["job_id"], "job_hash": args["job_hash"],
              "source_file_id": args["source_file_id"], "source_dir": source}
    return config, registry, calls, kwargs


@pytest.fixture
def configured(tmp_path, monkeypatch):
    prep = prepared(tmp_path)
    return prep, configure(tmp_path, monkeypatch, prep, tmp_path / "source")


def test_attachment_uses_real_typed_validator_review_and_no_second_replay(configured):
    prep, (_, _, calls, kwargs) = configured
    job = prep[0]
    result = producer.ensure_comparison(job, **kwargs)
    require_comparison_package(job, expected_required=True)
    assert result["comparison_artifacts"]["schema"] == "universal-video-comparison-artifacts-v1"
    report = _verify(job, evidence_phase="GENERATION_FINALIZATION")
    write(job / "server_review.json", build_server_review(job, report))
    assert _verify(job, require_server_review=True)["state"] == "PASS"
    snapshot = {p.relative_to(job).as_posix(): p.read_bytes()
                for p in job.rglob("*") if p.is_file()}
    producer.ensure_comparison(job, **kwargs)
    assert len(calls) == 1
    assert snapshot == {p.relative_to(job).as_posix(): p.read_bytes()
                        for p in job.rglob("*") if p.is_file()}
    assert calls[0].exists()  # raw rejected pairs retained outside compact result


@pytest.mark.parametrize("mode", [
    "disabled", "wrong-job", "wrong-source", "wrong-source-hash", "wrong-version",
    "not-authorized", "duplicate", "unprotected", "gold-changed", "clip-changed",
    "stage-pin-changed", "already-reviewed", "required-stripped",
])
def test_preflight_refuses_without_launch_or_review(configured, monkeypatch, mode):
    prep, (config, registry, calls, kwargs) = configured
    job, manifest, raw, args = prep
    if mode == "disabled":
        monkeypatch.delenv(producer.CONFIG_ENV)
    elif mode == "wrong-job":
        config["job_hash"] = "f" * 64
    elif mode == "wrong-source":
        config["source_file_id"] = "synthetic_other_01"
    elif mode == "wrong-source-hash":
        config["source_sha256"] = "f" * 64
    elif mode == "wrong-version":
        config["source_version"] = "2"
    elif mode == "not-authorized":
        config["replay_authorized"] = False
    elif mode == "unprotected":
        registry.chmod(0o666)
    elif mode == "gold-changed":
        from universal_video.comparison_artifacts import decode
        sealed = decode(args["sealed_manifest_path"].read_bytes())
        Path(sealed["inputs"]["gold"]["path"]).write_bytes(b"changed synthetic gold")
    elif mode == "clip-changed":
        args["clip_binding_path"].write_bytes(b"{}")
    elif mode == "stage-pin-changed":
        write(kwargs["source_dir"] / durable.SOURCE_RECEIPT, {})
    elif mode == "already-reviewed":
        write(job / "server_review.json", {"synthetic": True})
    elif mode == "required-stripped":
        manifest.pop("metadata")
        write(job / "manifest.json", manifest)
    write(registry, {"schema": producer.SCHEMA,
                     "jobs": [config, config] if mode == "duplicate" else [config]})
    with pytest.raises((RuntimeError, OSError, ValueError)):
        producer.ensure_comparison(job, **kwargs)
    assert not calls
    assert not (job / "comparison").exists()
    if mode != "already-reviewed":
        assert not (job / "server_review.json").exists()
    assert raw.exists() and kwargs["source_dir"].exists()


@pytest.mark.parametrize("version", [None, False, "", "0", "-1", "None"])
def test_invalid_stage_version_blocks_before_launch(configured, version):
    prep, (_, _, calls, kwargs) = configured
    from universal_video.comparison_artifacts import decode
    pin_path = kwargs["source_dir"] / durable.SOURCE_RECEIPT
    pin = decode(pin_path.read_bytes())
    pin["original"]["version"] = version
    write(pin_path, pin)
    with pytest.raises(RuntimeError, match="source version"):
        producer.ensure_comparison(prep[0], **kwargs)
    assert not calls and not (prep[0] / "comparison").exists()


@pytest.mark.parametrize("mode", ["runner-error", "bad-output", "changed-parent"])
def test_attempt_failure_preserves_evidence_and_refuses_automatic_retry(configured, monkeypatch, mode):
    prep, (_, _, calls, kwargs) = configured
    job, manifest, raw, _ = prep
    original_launch = producer._launch
    def launch(*args):
        original_launch(*args)
        if mode == "runner-error":
            raise subprocess.CalledProcessError(1, ["synthetic-runner"])
        if mode == "bad-output":
            (args[2] / "comparison.json").write_bytes(b"{}")
        if mode == "changed-parent":
            manifest["synthetic_conflict"] = True
            write(job / "manifest.json", manifest)
    monkeypatch.setattr(producer, "_launch", launch)
    with pytest.raises((RuntimeError, subprocess.CalledProcessError)):
        producer.ensure_comparison(job, **kwargs)
    assert len(calls) == 1 and calls[0].exists()
    assert (calls[0].parent / "attempt.json").exists()
    assert not (job / "server_review.json").exists()
    assert not (job / durable.FINAL_RECEIPT).exists()
    with pytest.raises(FileExistsError):
        producer.ensure_comparison(job, **kwargs)
    assert len(calls) == 1 and kwargs["source_dir"].exists()


def test_missing_manifest_version_is_filled_from_stage_pin(configured):
    prep, (_, _, calls, kwargs) = configured
    job, manifest = prep[:2]
    manifest["source"].pop("version")
    write(job / "manifest.json", manifest)
    result = producer.ensure_comparison(job, **kwargs)
    assert result["source"]["version"] == "1"
    require_comparison_package(job, expected_required=True)
    assert len(calls) == 1


@pytest.mark.parametrize("entry", ["runner", "launch"])
def test_unqualified_executor_blocks_before_subprocess_or_log(
        tmp_path, monkeypatch, entry):
    def forbidden(*args, **kwargs):
        pytest.fail("unqualified comparison reached a host subprocess")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "check_output", forbidden)
    with pytest.raises(producer.ComparisonProducerError, match="credential-isolated"):
        if entry == "runner":
            producer._runner_file({})
        else:
            producer._launch({}, tmp_path / "runner", tmp_path / "raw", tmp_path / "log")
    assert not (tmp_path / "log").exists()


def worker_fixture(tmp_path, monkeypatch, drive_setup):
    from universal_video import spool_worker as worker, drive_stage as stage
    import test_universal_video_durable_drive as protocol
    _, _, backend, _ = drive_setup
    content = b"synthetic-v" * (128 * 1024)
    digest = hashlib.sha256(content).hexdigest()
    monkeypatch.setattr(protocol, "CONTENT", content)
    monkeypatch.setattr(protocol, "SHA", digest)
    backend.meta.update(size=str(len(content)), sha256Checksum=digest)
    spool = tmp_path / "spool"
    (spool / "inbox").mkdir(parents=True)
    (spool / "results").mkdir()
    seed, manifest = _bundle(tmp_path / "seed")
    result = spool / "results" / manifest["job_id"]
    seed.rename(result)
    request = {"job_id": manifest["job_id"], "profile": manifest["profile"],
               "source": {"kind": "google_drive", "file_id": protocol.SOURCE},
               "metadata": {"comparison_required": True}}
    job_hash = canonical_job_hash(validate_job(request))
    fingerprint = _fingerprint({"kind": "google_drive", "file_id": protocol.SOURCE,
                               "size_bytes": len(content), "checksum_kind": "sha256Checksum",
                               "checksum": digest})
    manifest.update(job_hash=job_hash, metadata=request["metadata"],
                    source_fingerprint=fingerprint,
                    source_fingerprint_basis="sha256Checksum+size+file_id")
    manifest["source"] = {"kind": "google_drive", "file_id": protocol.SOURCE,
                          "size": str(len(content)), "version": "1", "sha256Checksum": digest,
                          "fingerprint": fingerprint, "fingerprint_basis": "sha256Checksum+size+file_id",
                          "reuse_safe": True}
    manifest["media"].update(sha256=digest, size_bytes=len(content))
    write(result / "manifest.json", manifest)
    prep = prepared(tmp_path, parent=(result, manifest))
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setenv("UNIVERSAL_VIDEO_MEDIA_ROOT", str(media))
    binding = tmp_path / "drive-bindings.json"
    write(binding, {"schema": "universal-video-drive-bindings-v1", "jobs": [{
        "job_id": request["job_id"], "job_hash": job_hash, "source_file_id": protocol.SOURCE,
        "processing_upload_authorized": True, "folders": protocol.FOLDERS}]})
    binding.chmod(0o600)
    monkeypatch.setenv("UNIVERSAL_VIDEO_DRIVE_BINDINGS_FILE", str(binding))
    monkeypatch.setattr(stage, "access_token", lambda: "synthetic-token")
    monkeypatch.setattr(durable, "verify_result", verify_result)
    monkeypatch.setattr(worker, "run_job", lambda *a: pytest.fail("comparison recovery repeated main compute"))
    monkeypatch.setattr(worker, "validate_video_runtime", lambda: pytest.fail("entered main compute preflight"))
    # stage_drive_job creates its own real pin through the synthetic backend;
    # configure only registers the expected values, never overrides that pin.
    source = media / "drive-ready" / request["job_id"]
    config, registry, calls, kwargs = configure(tmp_path, monkeypatch, prep, tmp_path / "unused-pin")
    manifest["source"].pop("version")  # actual main compute provenance behavior
    write(result / "manifest.json", manifest)
    inbox = spool / "inbox" / (request["job_id"] + ".json")
    write(inbox, request)
    return worker, spool, result, source, backend, registry, calls, inbox


@pytest.mark.parametrize("mode", ["disabled", "failed", "success"])
def test_worker_missing_failed_comparison_never_publishes_or_cleans(
        tmp_path, monkeypatch, drive_setup, mode):
    worker, spool, result, source, backend, registry, calls, inbox = worker_fixture(
        tmp_path, monkeypatch, drive_setup)
    if mode == "disabled":
        monkeypatch.delenv(producer.CONFIG_ENV)
    elif mode == "failed":
        def fail(*args):
            args[2].mkdir()
            (args[2] / "retained.txt").write_bytes(b"synthetic failure evidence")
            raise subprocess.CalledProcessError(1, ["synthetic-runner"])
        monkeypatch.setattr(producer, "_launch", fail)
    assert worker.verify_result is verify_result
    assert worker.build_server_review is build_server_review
    assert worker.finalize_drive_job is durable.finalize_drive_job
    assert worker.process_one(spool)
    if mode == "success":
        assert len(calls) == 1
        assert (spool / "done" / inbox.name).exists()
        assert (result / "server_review.json").exists()
        assert _verify(result, require_server_review=True)["state"] == "PASS"
        assert durable.read_receipt(result / durable.FINAL_RECEIPT)["status"] == "PUBLISHED_VERIFIED"
        assert backend.posts and not source.exists()
    else:
        assert not backend.posts
        assert source.exists()
        assert (spool / "failed" / inbox.name).exists()
        assert not (spool / "done" / inbox.name).exists()
        assert not (result / "server_review.json").exists()
        assert not (result / durable.FINAL_RECEIPT).exists()
        if mode == "failed":
            assert (result.parent / (result.name + ".comparison-replay") / "raw/retained.txt").exists()
