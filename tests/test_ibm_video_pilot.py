"""SYNTHETIC offline fixtures only: dummy media/JPEG/model bytes are NOT video.

Uses unittest so it runs without pytest, Docker, ffmpeg, model downloads or
optional ASR/ONNX packages. Native runner orchestration and result conformance
are real; media, ASR, speaker work and container execution are injected mocks.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import tempfile
import sys
import unittest
from dataclasses import replace
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import patch

from ops import ibm_video_pilot as pilot
from universal_video.contract import VideoJob
from universal_video.speaker_structure import _unavailable


def forbidden_drive(*_args, **_kwargs):
    raise AssertionError("Drive/network is forbidden in SYNTHETIC offline tests")


# The native runner imports optional requests via drive_adapter at import time.
# Mock that unused external boundary only when requests is absent. Do not fake
# a requests installation or a pytest runner; any stubbed Drive call fails.
if importlib.util.find_spec("requests") is None:
    with patch.dict(sys.modules, {"universal_video.drive_adapter": SimpleNamespace(
            access_token=forbidden_drive, download_file=forbidden_drive, file_metadata=forbidden_drive)}):
        from universal_video import runner
else:
    from universal_video import runner


REPO = Path(__file__).resolve().parents[1]
NOW = 1000.0


class SyntheticExecutor:
    synthetic = True

    def __init__(self, request):
        self.request = request
        self.calls = 0
        self.observations = 0
        self.removed = []
        self.before_execute = None
        self.after_execute = None
        self.observation_change = None
        self.execution_change = None

    def observe(self, request):
        self.observations += 1
        clip = request.clip
        observation = pilot.Observation(
            image_id=request.image_id, code_hashes=tuple(sorted(pilot.image_code_inventory(dict(request.code_hashes)).items())),
            entrypoint=pilot.ENTRYPOINT, base_env=(), clip_sha256=clip.sha256,
            clip_bytes=clip.size_bytes, duration_seconds=clip.duration_seconds,
            pts=(clip.source_start_pts, clip.source_end_pts, clip.clip_start_pts, clip.clip_end_pts),
            video_streams=1, audio_streams=1, synthetic=True)
        return self.observation_change(observation) if self.observation_change else observation

    def execute_one(self, plan, limits):
        self.calls += 1
        if self.before_execute:
            self.before_execute(plan)
        payload = json.loads(plan.job_json)
        media = {"duration_seconds": 70.0, "size_bytes": self.request.clip.size_bytes,
                 "sha256": self.request.clip.sha256, "format_name": "SYNTHETIC_NOT_REAL_MEDIA",
                 "streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}
        source_fp = pilot.fingerprint({"kind": "local_path", "size_bytes": media["size_bytes"],
                                       "sha256": media["sha256"]})
        inspection = {"kind": "local_path", "path": self.request.clip.path,
                      "fingerprint": source_fp, "fingerprint_basis": "sha256+size",
                      "reuse_safe": True, "known_sha256": media["sha256"]}
        provenance = {"kind": "local_path", "path": pilot.ROOT + "/media/clip.mp4",
                      "fingerprint": source_fp, "fingerprint_basis": "sha256+size"}
        rows = [{"start": 0.0, "end": 1.5, "text": "Opening bid", "chunk": 0, "unreliable": False}]
        qc = [{"chunk": 0, "start": 0.0, "end": 70.0, "ok": True,
               "no_speech": False, "critical": False, "nonspeech_hallucination": False,
               "retry_used": False, "primary_words": 2, "strict_words": 2,
               "similarity": 1.0, "repetition_ratio": 0.0, "strict_repetition_ratio": 0.0,
               "selected_attempt": "primary", "selected_consensus": 1.0, "failure_reasons": []}]
        speaker_rows, speaker = _unavailable(rows, revision="synthetic-v1",
                                             status="UNAVAILABLE_INSUFFICIENT_SEGMENTS",
                                             reason="INSUFFICIENT_SEGMENTS", min_label_coverage=0.8)

        def frames(_source, output, duration, **options):
            output.mkdir()
            packet = runner.plan_frame_evidence(duration, **options)
            timestamps = packet.pop("timestamps")
            names = {}
            packet["frames"] = []
            for index, timestamp in enumerate(timestamps):
                name = f"frame-{index:04d}.jpg"
                # Explicit dummy bytes: validates packaging, not image decode.
                content = b"SYNTHETIC JPEG FIXTURE - NOT REAL VIDEO"
                (output / name).write_bytes(content)
                names[timestamp] = name
                packet["frames"].append({"time": timestamp, "file": name,
                                         "sha256": hashlib.sha256(content).hexdigest()})
            for bundle in packet["bundles"]:
                for member in bundle["members"]:
                    member["file"] = names[member["time"]]
            packet["frame_count"] = len(packet["frames"])
            packet["bundle_count"] = len(packet["bundles"])
            return packet

        with (patch.dict(os.environ, dict(plan.env), clear=True),
              patch.object(runner, "validate_from_env", return_value=VideoJob(**payload)),
              patch.object(runner, "_inspect_source", return_value=inspection),
              patch.object(runner, "_materialize_source", return_value=(self.request.clip.path, provenance, media["sha256"])),
              patch.object(runner, "media_probe", return_value=media),
              patch.object(runner, "transcribe", return_value=(rows, qc, "en", 0)),
              patch.object(runner, "run_speaker_structure", return_value=(speaker_rows, speaker)),
              patch.object(runner, "extract_frame_evidence", side_effect=frames),
              patch.object(runner.os, "uname", return_value=SimpleNamespace(nodename="SYNTHETIC"), create=True)):
            manifest = runner.run_job(payload, plan.scope / "output")
        ready = {"schema": "universal-video-container-readiness-v1", "status": "READY",
                 "source_commit": pilot.BASELINE, "fallback_used": False,
                 "speaker_models": dict(zip(("segmentation_sha256", "embedding_sha256"), pilot.SPEAKER_HASHES.values())),
                 "mounts": sorted([pilot.ROOT + "/spool", pilot.ROOT + "/output", pilot.ROOT + "/media",
                                   pilot.ROOT + "/model-cache", pilot.SPEAKER])}
        execution = pilot.Execution("c" * 64, True, plan.name, True, 0,
                                    pilot.encoded(ready) + b"\n" + pilot.encoded(manifest) + b"\n",
                                    b"", 1.0, 1024**2)
        if self.after_execute:
            self.after_execute(plan)
        return self.execution_change(execution) if self.execution_change else execution

    def remove_owned(self, container_id, owner):
        self.removed.append((container_id, owner))


class IBMPilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real_code = pilot.code_inventory(REPO)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ibm-pilot-test-")
        if Path(self.temp.name).resolve().parent != Path(tempfile.gettempdir()).resolve():
            raise AssertionError("test cleanup scope escaped workspace")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        parent = root / "attempts"
        parent.mkdir()
        inputs = root / "inputs"
        inputs.mkdir()
        # Small SYNTHETIC image-source fixture keeps negative cases fast.
        # A separate test below inventories the full real baseline Dockerfile.
        source = root / "synthetic-source"
        (source / "deploy/oracle-universal-video").mkdir(parents=True)
        (source / "universal_video").mkdir()
        (source / "deploy/oracle-universal-video/Dockerfile").write_text(
            "COPY universal_video ./universal_video\nCOPY bridge_speaker_diarization_v3.py ./\n", encoding="utf-8")
        (source / "universal_video/runner.py").write_bytes(b"# SYNTHETIC CODE INVENTORY FIXTURE\n")
        (source / "bridge_speaker_diarization_v3.py").write_bytes(b"# SYNTHETIC CODE INVENTORY FIXTURE\n")
        code = tuple(sorted(pilot.code_inventory(source).items()))
        clip_path = inputs / "clip.mp4"
        clip_path.write_bytes(b"SYNTHETIC FIXTURE, NOT AN ACTUAL VIDEO")
        clip = pilot.Clip(clip_path, clip_path.stat().st_size,
                          hashlib.sha256(clip_path.read_bytes()).hexdigest(), 70.0,
                          1250.0, 1320.0, 0.0, 70.0, pilot.DRIVE_ID, pilot.SOURCE_BYTES,
                          pilot.SOURCE_SHA256, pilot.SOURCE_SECONDS, "SYNTHETIC_FIXTURE", "e" * 64)
        model = inputs / "asr"
        model.mkdir()
        for name in ("model.bin", "config.json", "tokenizer.json"):
            (model / name).write_bytes(b"SYNTHETIC MODEL " + name.encode())
        speaker = inputs / "speaker"
        speaker.mkdir()
        fake_hashes = {}
        for name in pilot.SPEAKER_HASHES:
            raw = b"SYNTHETIC MODEL FIXTURE" * 100
            (speaker / name).write_bytes(raw)
            fake_hashes[name] = hashlib.sha256(raw).hexdigest()
        # Production constants are never relaxed in adapter; test scope only.
        speaker_patch = patch.object(pilot, "SPEAKER_HASHES", fake_hashes)
        speaker_patch.start()
        self.addCleanup(speaker_patch.stop)
        limits = pilot.Limits(2000, 100, 100, 1024**2, 100, 512 * 1024, 128 * 1024,
                              None, "a" * 64, "b" * 64, "c" * 64)
        self.request = pilot.Request("1" * 32, parent, source, clip, model, speaker,
                                     "sha256:" + "f" * 64, code,
                                     tuple(sorted(pilot.tree_hashes(model, max_bytes=1024**2, max_files=128).items())), limits)
        self.request = replace(self.request, limits=replace(limits, admission_sha256=pilot.admission_fingerprint(self.request)))
        self.executor = SyntheticExecutor(self.request)

    def plan(self, request=None):
        request = request or self.request
        return pilot.build_plan(request, self.executor.observe(request), now=NOW)

    def run_pilot(self):
        return pilot.run_pilot(self.request, executor=self.executor, clock=lambda: NOW)

    def assert_blocked(self, code, *, request=None):
        with self.assertRaisesRegex((pilot.PilotBlocked, FileNotFoundError), code):
            self.plan(request)
        self.assertEqual(self.executor.calls, 0)

    def test_exact_allowlisted_plan_and_nested_readonly_mounts(self):
        plan = self.plan()
        self.assertEqual(plan.argv, pilot.RUNNER_ARGV)
        self.assertEqual(plan.client_env, ())
        args = plan.docker_create_argv()
        for arg in ("--network=none", "--pull=never", "--cpus=4", "--memory=8g",
                    "--memory-swap=8g", "--pids-limit=512", "--read-only", "--log-driver=none"):
            self.assertIn(arg, args)
        self.assertNotIn("--env-file", args)
        self.assertNotIn("--entrypoint", args)
        self.assertNotIn("--rm", args)
        self.assertEqual(args[-7:], (self.request.image_id,) + pilot.RUNNER_ARGV)
        env = dict(plan.env)
        self.assertEqual(env["UNIVERSAL_VIDEO_SPEAKER_MODEL_CACHE"], env["BRIDGE_SPEAKER_MODEL_CACHE"])
        self.assertEqual(env["UNIVERSAL_VIDEO_WHISPER_MODEL"], pilot.MODEL)
        self.assertEqual(env["UNIVERSAL_VIDEO_REQUIRE_STAGED_SOURCE"], "0")
        self.assertEqual(env["HF_HUB_OFFLINE"], "1")
        self.assertEqual(env["TRANSFORMERS_OFFLINE"], "1")
        self.assertEqual(env["BRIDGE_SPEAKER_ALLOW_NEMO_COMPAT"], "0")
        self.assertEqual(len(plan.mounts), 10)
        self.assertEqual(sum(m.readonly for m in plan.mounts), 5)
        self.assertEqual([m for m in plan.mounts if m.target == pilot.SPEAKER],
                         [pilot.Mount(str(plan.scope / "model-cache/speaker"), pilot.SPEAKER, False)])
        self.assertTrue(all(m.target.startswith(pilot.ROOT) for m in plan.mounts))
        self.assertEqual(json.loads(plan.job_json)["options"]["max_duration_seconds"], 70)
        self.assertEqual(json.loads(plan.job_json)["source"]["kind"], "local_path")
        self.assertFalse(plan.scope.exists())

    def test_real_baseline_dockerfile_inventory_and_unchanged_resident_defaults(self):
        for path in ("universal_video/runner.py", "universal_video/container_runtime.py",
                     "universal_video/readiness.py", "universal_video/result_conformance.py",
                     "universal_video/speaker_structure.py", "universal_video/speaker_role_verifier.py",
                     "bridge_speaker_diarization_v3.py", "requirements-worker.txt",
                     "deploy/oracle-universal-video/universal-video-container-entrypoint.sh"):
            self.assertIn(path, self.real_code)
        dockerfile = (REPO / "deploy/oracle-universal-video/Dockerfile").read_text(encoding="utf-8")
        self.assertIn("UNIVERSAL_VIDEO_REQUIRE_STAGED_SOURCE=1", dockerfile)
        self.assertIn('CMD ["python", "-m", "universal_video.spool_worker"]', dockerfile)
        self.assertNotIn("diana_longitudinal_postprocess_v4_2.py", self.real_code)
        image = pilot.image_code_inventory(self.real_code)
        self.assertNotIn(pilot.CODE_ROOT + "/" + pilot.DOCKERFILE, image)
        self.assertIn("/usr/local/bin/universal-video-container-entrypoint", image)
        self.assertEqual(image[pilot.CODE_ROOT + "/universal_video/runner.py"], self.real_code["universal_video/runner.py"])
        entry = (REPO / "deploy/oracle-universal-video/universal-video-container-entrypoint.sh").read_text()
        self.assertIn('exec python -m universal_video.container_runtime "$@"', entry)

    def test_real_native_runner_and_conformance_with_mock_media(self):
        receipt = self.run_pilot()
        self.assertEqual(receipt["state"], "SYNTHETIC_TECHNICAL_BUNDLE", receipt)
        self.assertEqual(receipt["native_conformance"]["state"], "PASS")
        self.assertFalse(receipt["live_admitted"])
        self.assertTrue(receipt["synthetic"])
        self.assertEqual(receipt["roles"], "UNKNOWN")
        self.assertEqual(receipt["person_identity"], "NOT_INFERRED")
        self.assertEqual(receipt["original_offset_seconds"], 1250)
        self.assertEqual(receipt["stages"]["transcribe"], "EXECUTED")
        for stage in pilot.DOWNSTREAM:
            self.assertEqual(receipt["stages"][stage], "DEFERRED")
        scope = self.request.parent / ("pilot-" + self.request.run_id)
        self.assertTrue((scope / "adapter-receipt.json").is_file())
        self.assertTrue((scope / "pilot.job.json").is_file())
        for leaf in pilot.SPOOL_LEAVES:
            self.assertEqual(list((scope / "spool" / leaf).iterdir()), [])
        self.assertEqual(self.executor.removed, [("c" * 64, "ibm-video-pilot-" + self.request.run_id)])
        self.assertEqual(receipt["delivery"], "UNVERIFIED_RETAIN_ALL")
        self.assertTrue((scope / "output").is_dir())

    def test_no_live_executor_or_claimed_provenance(self):
        self.executor.synthetic = False
        with self.assertRaisesRegex(pilot.PilotBlocked, "LIVE_EXECUTION_BLOCKED"):
            self.run_pilot()
        self.assertEqual(self.executor.observations, 0)
        for value in (None, "CLAIMED", "UNKNOWN"):
            with self.subTest(value=value):
                self.assert_blocked("CLAIMED_OR_NULL", request=replace(self.request, clip=replace(self.request.clip, provenance=value)))

    def test_missing_image_and_code_identity(self):
        self.executor.observation_change = lambda o: replace(o, image_id="")
        self.assert_blocked("IMAGE_MISSING")
        self.executor.observation_change = lambda o: replace(o, code_hashes=())
        self.assert_blocked("IMAGE_CODE_BYTES")
        self.executor.observation_change = None
        self.assert_blocked("CODE_BYTES", request=replace(self.request, code_hashes=(("universal_video/runner.py", "e" * 64),)))
        self.assert_blocked("BASELINE", request=replace(self.request, source_commit="a" * 40))
        self.assert_blocked("IMMUTABLE_IMAGE", request=replace(self.request, image_id="latest"))

    def test_missing_asr_and_speaker_caches(self):
        (self.request.asr_model / "model.bin").unlink()
        self.assert_blocked("ASR_CACHE_INCOMPLETE")

    def test_missing_onnx(self):
        (self.request.speaker_cache / next(iter(pilot.SPEAKER_HASHES))).unlink()
        self.assert_blocked("")

    def test_onnx_hash_and_asr_substitution(self):
        path = self.request.speaker_cache / next(iter(pilot.SPEAKER_HASHES))
        old = path.read_bytes()
        path.write_bytes(b"x" * len(old))
        self.assert_blocked("SPEAKER_CACHE_HASH")
        path.write_bytes(old)
        (self.request.asr_model / "config.json").write_bytes(b"substitution")
        self.assert_blocked("ASR_HASH")

    def test_source_hash_bytes_pts_duration_and_stream_mismatches(self):
        for changes, code in (({"sha256": "0" * 64}, "CLIP_BYTES_HASH"),
                              ({"size_bytes": 3}, "CLIP_BYTES_HASH"),
                              ({"source_bytes": 1}, "ORIGINAL_SOURCE"),
                              ({"source_sha256": "0" * 64}, "ORIGINAL_SOURCE"),
                              ({"drive_id": "wrong"}, "ORIGINAL_SOURCE"),
                              ({"source_start_pts": 1249}, "CLIP_PTS"),
                              ({"clip_start_pts": 1}, "CLIP_PTS"),
                              ({"duration_seconds": 71}, "CLIP_DURATION"),
                              ({"duration_seconds": float("nan")}, "INVALID_PTS")):
            with self.subTest(changes=changes):
                self.assert_blocked(code, request=replace(self.request, clip=replace(self.request.clip, **changes)))
        self.executor.observation_change = lambda o: replace(o, video_streams=0)
        self.assert_blocked("VIDEO_AND_AUDIO")
        self.executor.observation_change = lambda o: replace(o, pts=(0, 70, 0, 70))
        self.assert_blocked("CLIP_PTS")

    def test_forbidden_env_cache_alias_and_command_mutations(self):
        for key in ("DOCKER_HOST", "DOCKER_CONTEXT", "BRIDGE_VIDEO_QUEUE_DATABASE_URL", "GOOGLE_APPLICATION_CREDENTIALS", "PYTHONPATH"):
            with self.subTest(key=key):
                self.executor.observation_change = lambda o: replace(o, base_env=((key, "forbidden"),))
                self.assert_blocked("FORBIDDEN_IMAGE_ENV")
        self.executor.observation_change = lambda o: replace(o, base_env=(("BRIDGE_SPEAKER_MODEL_CACHE", "/tmp/wrong"),))
        self.assert_blocked("ALIAS_OR_VALUE")
        self.executor.observation_change = None
        observation = self.executor.observe(self.request)
        plan = self.plan()
        for changed in (replace(plan, argv=("sh", "-c", "bad")),
                        replace(plan, env=plan.env + (("NEON_PASSWORD", "secret"),)),
                        replace(plan, client_env=(("DOCKER_HOST", "tcp://host"),)),
                        replace(plan, mounts=plan.mounts + (pilot.Mount("/", "/host", False),)),
                        replace(plan, mounts=(replace(plan.mounts[4], readonly=False),) + plan.mounts[1:])):
            with self.subTest(changed=changed.argv):
                with self.assertRaisesRegex(pilot.PilotBlocked, "PLAN_ALLOWLIST"):
                    pilot.validate_plan(changed, self.request, observation, now=NOW)

    def test_host_secrets_not_inherited(self):
        with patch.dict(os.environ, {"DOCKER_HOST": "secret", "DOCKER_CONTEXT": "secret", "NEON_PASSWORD": "secret"}):
            plan = self.plan()
        self.assertEqual(plan.client_env, ())
        self.assertFalse(any("secret" in arg for arg in plan.docker_create_argv()))

    def test_deadline_budget_and_enforcement_gates(self):
        for changes, code in (({"deadline_epoch": 1100}, "NO_COMPUTE_WINDOW"),
                              ({"export_reserve_seconds": 0}, "RESERVE_REQUIRED"),
                              ({"stop_reserve_seconds": 0}, "RESERVE_REQUIRED"),
                              ({"budget_usd": float("nan")}, "INVALID_BUDGET"),
                              ({"budget_usd": 0}, "INVALID_BUDGET"),
                              ({"output_quota_evidence_sha256": ""}, "ENFORCEMENT_EVIDENCE"),
                              ({"stop_contract_sha256": ""}, "ENFORCEMENT_EVIDENCE"),
                              ({"max_log_bytes": 0}, "OUTPUT_CAP")):
            with self.subTest(changes=changes):
                self.assert_blocked(code, request=replace(self.request, limits=replace(self.request.limits, **changes)))

    def test_output_scope_overlap_and_duplicate(self):
        self.assert_blocked("OUTPUT_INPUT_SCOPE_OVERLAP", request=replace(self.request, parent=self.request.clip.path.parent))
        (self.request.parent / ("pilot-" + self.request.run_id)).mkdir()
        self.assert_blocked("OUTPUT_SCOPE_ALREADY_EXISTS")

    def test_symlink_and_hardlink(self):
        link = self.request.clip.path.parent / "link.mp4"
        try:
            link.symlink_to(self.request.clip.path)
        except OSError as exc:
            self.skipTest(f"OS does not permit symlink creation: {type(exc).__name__}")
        self.assert_blocked("SYMLINK", request=replace(self.request, clip=replace(self.request.clip, path=link)))

    def test_hardlink_rejected(self):
        link = self.request.clip.path.parent / "hard.mp4"
        os.link(self.request.clip.path, link)
        self.assert_blocked("HARDLINK")

    def test_input_race_and_job_substitution(self):
        self.executor.after_execute = lambda _: self.request.clip.path.write_bytes(b"changed")
        receipt = self.run_pilot()
        self.assertEqual(receipt["state"], "UNKNOWN")
        self.assertEqual(receipt["error_code"], "CLIP_BYTES_HASH_MISMATCH")
        self.assertEqual(self.executor.calls, 1)

    def test_job_race(self):
        self.executor.after_execute = lambda plan: (plan.scope / "pilot.job.json").write_bytes(b"{}")
        self.assertEqual(self.run_pilot()["error_code"], "JOB_RACE")

    def test_code_bytes_changed_after_start(self):
        self.executor.after_execute = lambda _: (self.request.repo / "universal_video/runner.py").write_bytes(b"tampered")
        self.assertEqual(self.run_pilot()["error_code"], "CODE_BYTES_MISMATCH")

    def test_admission_bound_to_exact_caps_and_run(self):
        self.assert_blocked("ADMISSION_BINDING", request=replace(self.request,
                            limits=replace(self.request.limits, budget_usd=19.0)))

    def test_owner_removed_usd20_ceiling_without_lifting_live_gates(self):
        for ceiling in (None, 20.01, 200.0):
            with self.subTest(ceiling=ceiling):
                request = replace(self.request, limits=replace(self.request.limits, budget_usd=ceiling))
                request = replace(request, limits=replace(request.limits,
                                  admission_sha256=pilot.admission_fingerprint(request)))
                self.assertEqual(self.plan(request).compute_deadline_epoch, 1800)
        receipt = self.run_pilot()
        self.assertFalse(receipt["live_admitted"])
        self.assertEqual(receipt["costs"], {"actual_usd": None, "status": "NOT_OBSERVED_SYNTHETIC",
                                           "budget_ceiling_usd": None})
        self.assertTrue(receipt["live_gaps"])
        self.assertFalse(any("USD20" in gap for gap in receipt["live_gaps"]))

    def test_reparse_ancestor_gate_without_symlink_privilege(self):
        original = Path.lstat
        parent = self.request.clip.path.parent
        def observe(path, *args, **kwargs):
            if path == parent:
                return SimpleNamespace(st_mode=0o40755, st_file_attributes=0x400)
            return original(path, *args, **kwargs)
        with patch.object(Path, "lstat", observe):
            with self.assertRaisesRegex(pilot.PilotBlocked, "REPARSE"):
                pilot.file_identity(self.request.clip.path, cap=1024)

    def test_no_job_in_gate_spool(self):
        self.executor.after_execute = lambda plan: (plan.scope / "spool/inbox/job.json").write_bytes(b"{}")
        self.assertEqual(self.run_pilot()["error_code"], "UNEXPECTED_SCRATCH_FILE")

    def test_every_writable_scratch_root_is_checked(self):
        for relative in ("spool/extra.bin", "media/extra.bin", "model-cache/extra.bin", "model-cache/speaker/extra.bin"):
            with self.subTest(relative=relative):
                # Separate owned fixture per attempt, never erase a marker.
                case = IBMPilotTests()
                case.setUp()
                try:
                    case.executor.after_execute = lambda plan: (plan.scope / relative).write_bytes(b"unexpected")
                    receipt = case.run_pilot()
                    self.assertEqual(receipt["state"], "UNKNOWN")
                    self.assertEqual(receipt["error_code"], "UNEXPECTED_SCRATCH_FILE")
                finally:
                    case.doCleanups()

    def test_writable_scratch_overflow_is_not_hidden_by_output_cap(self):
        self.executor.after_execute = lambda plan: (plan.scope / "media/extra.bin").write_bytes(b"x" * (1024**2 + 1))
        self.assertEqual(self.run_pilot()["error_code"], "FILE_BYTES_LIMIT")

    def test_speaker_parent_replacement_rejected(self):
        def change(plan):
            path = plan.scope / "model-cache/speaker"
            path.rename(plan.scope / "model-cache/old-speaker")
            path.mkdir()
            for name in pilot.SPEAKER_HASHES:
                (path / name).write_bytes(b"")
        self.executor.after_execute = change
        self.assertEqual(self.run_pilot()["error_code"], "SCRATCH_PATH_REPLACED")

    def test_output_overflow_and_unknown_extra_file(self):
        self.executor.after_execute = lambda plan: (plan.scope / "output/extra.txt").write_bytes(b"x" * (1024**2 + 1))
        self.assertEqual(self.run_pilot()["error_code"], "FILE_BYTES_LIMIT")

    def test_output_filecount(self):
        def overflow(plan):
            for index in range(101):
                (plan.scope / f"output/extra-{index}.txt").write_bytes(b"x")
        self.executor.after_execute = overflow
        self.assertEqual(self.run_pilot()["error_code"], "FILE_COUNT_LIMIT")

    def test_unknown_output_file(self):
        self.executor.after_execute = lambda plan: (plan.scope / "output/extra.txt").write_bytes(b"x")
        receipt = self.run_pilot()
        self.assertEqual(receipt["state"], "UNKNOWN")

    def test_unknown_output_directory(self):
        self.executor.after_execute = lambda plan: (plan.scope / "output/unexpected").mkdir()
        self.assertEqual(self.run_pilot()["error_code"], "UNDECLARED_OUTPUT_DIRECTORY")

    def test_completed_then_lost_adapter_receipt_cannot_repeat(self):
        receipt = self.run_pilot()
        self.assertEqual(receipt["state"], "SYNTHETIC_TECHNICAL_BUNDLE")
        (self.request.parent / ("pilot-" + self.request.run_id) / "adapter-receipt.json").unlink()
        self.request = replace(self.request, run_id="2" * 32)
        self.request = replace(self.request, limits=replace(self.request.limits,
                               admission_sha256=pilot.admission_fingerprint(self.request)))
        with self.assertRaisesRegex(pilot.PilotBlocked, "DUPLICATE_OR_UNKNOWN"):
            self.run_pilot()
        self.assertEqual(self.executor.calls, 1)

    def test_compute_deadline_exceeded_is_unknown_and_retained(self):
        times = iter([NOW, NOW, NOW, 1801, 1801])
        receipt = pilot.run_pilot(self.request, executor=self.executor, clock=lambda: next(times))
        self.assertEqual(receipt["state"], "UNKNOWN")
        self.assertEqual(receipt["error_code"], "COMPUTE_DEADLINE_EXCEEDED")
        self.assertEqual(receipt["delivery"], "UNVERIFIED_RETAIN_ALL")

    def test_finalization_preserves_stop_reserve(self):
        times = iter([NOW, NOW, NOW, NOW, 1901])
        receipt = pilot.run_pilot(self.request, executor=self.executor, clock=lambda: next(times))
        self.assertEqual(receipt["state"], "UNKNOWN")
        self.assertEqual(receipt["error_code"], "FINALIZATION_DEADLINE_EXCEEDED")
        self.assertEqual(receipt["timings"]["finalization_deadline_epoch"], 1900)

    def test_invalid_executor_metrics_still_persist_unknown_receipt(self):
        for field, value in (("elapsed_seconds", float("nan")), ("elapsed_seconds", float("inf")),
                             ("elapsed_seconds", object()), ("peak_memory_bytes", object())):
            with self.subTest(field=field, value=repr(value)):
                case = IBMPilotTests()
                case.setUp()
                try:
                    case.executor.execution_change = lambda execution: replace(execution, **{field: value})
                    receipt = case.run_pilot()
                    self.assertEqual(receipt["state"], "UNKNOWN")
                    saved = case.request.parent / ("pilot-" + case.request.run_id) / "adapter-receipt.json"
                    self.assertEqual(json.loads(saved.read_text())["state"], "UNKNOWN")
                    if field == "elapsed_seconds":
                        self.assertIsNone(receipt["timings"]["observed_elapsed_seconds"])
                    else:
                        self.assertIsNone(receipt["peak_memory_bytes"])
                finally:
                    case.doCleanups()

    def test_log_overflow(self):
        self.executor.execution_change = lambda e: replace(e, stderr=b"x" * (self.request.limits.max_log_bytes + 1))
        self.assertEqual(self.run_pilot()["error_code"], "LOG_BYTES_LIMIT")

    def test_timeout_reserves_and_no_duplicate_even_new_run_id(self):
        self.executor.before_execute = lambda _: (_ for _ in ()).throw(TimeoutError("DO NOT PERSIST PRIVATE LOG"))
        receipt = self.run_pilot()
        self.assertEqual(receipt["state"], "UNKNOWN")
        self.assertEqual(receipt["error_code"], "TimeoutError")
        self.assertEqual(self.executor.removed, [])
        self.request = replace(self.request, run_id="2" * 32)
        self.request = replace(self.request, limits=replace(self.request.limits,
                               admission_sha256=pilot.admission_fingerprint(self.request)))
        with self.assertRaisesRegex(pilot.PilotBlocked, "DUPLICATE_OR_UNKNOWN"):
            self.run_pilot()
        self.assertEqual(self.executor.calls, 1)

    def test_missing_execution_receipt(self):
        self.executor.execution_change = lambda _: None
        receipt = self.run_pilot()
        self.assertEqual(receipt["state"], "UNKNOWN")
        self.assertEqual(receipt["error_code"], "LOST_EXECUTION_RECEIPT")
        self.assertEqual(self.executor.removed, [])

    def test_cleanup_only_proved_new_owned_stopped_id(self):
        self.executor.execution_change = lambda e: replace(e, created_new=False, container_id="existing")
        receipt = self.run_pilot()
        self.assertEqual(receipt["state"], "UNKNOWN")
        self.assertEqual(self.executor.removed, [])

    def test_unstopped_container_no_cleanup(self):
        self.executor.execution_change = lambda e: replace(e, stopped=False)
        self.assertEqual(self.run_pilot()["error_code"], "CONTAINER_STOP_UNPROVED")
        self.assertEqual(self.executor.removed, [])

    def test_review_is_not_success(self):
        def review(e):
            ready, manifest = pilot.parse_stdout(e.stdout, max_bytes=1024**2)
            manifest["status"] = "REVIEW"
            return replace(e, stdout=pilot.encoded(ready) + b"\n" + pilot.encoded(manifest))
        self.executor.execution_change = review
        receipt = self.run_pilot()
        self.assertEqual(receipt["state"], "UNKNOWN")
        self.assertEqual(receipt["error_code"], "NATIVE_REVIEW_NOT_SUCCESS")
        self.assertEqual(receipt["stages"]["transcribe"], "BLOCKED")

    def test_stdout_disk_substitution(self):
        def change(e):
            ready, manifest = pilot.parse_stdout(e.stdout, max_bytes=1024**2)
            manifest["processing_revision"] = "0" * 40
            return replace(e, stdout=pilot.encoded(ready) + b"\n" + pilot.encoded(manifest))
        self.executor.execution_change = change
        self.assertEqual(self.run_pilot()["error_code"], "STDOUT_DISK_MANIFEST_MISMATCH")

    def test_exact_two_schema_records_strict_json(self):
        ready = {"schema": "universal-video-container-readiness-v1", "status": "READY", "fallback_used": False}
        manifest = {"contract": "universal-video-v1", "status": "COMPLETED"}
        good = pilot.encoded(ready) + b"\n" + pilot.encoded(manifest)
        self.assertEqual(pilot.parse_stdout(good, max_bytes=4096), (ready, manifest))
        for bad in (good + b"\n{}", pilot.encoded(manifest), pilot.encoded(manifest) + b"\n" + pilot.encoded(ready),
                    b"log line\n" + good, b'{"schema":"x","schema":"y"}\n' + pilot.encoded(manifest),
                    b'{"x":NaN}\n' + pilot.encoded(manifest)):
            with self.subTest(bad=bad[:32]):
                with self.assertRaises((pilot.PilotBlocked, ValueError)):
                    pilot.parse_stdout(bad, max_bytes=4096)

    def test_profile_downstream_and_duration_reject_not_trim(self):
        from universal_video.profiles import resolve_profile
        from universal_video.readiness import deferred_stages
        self.assertEqual(deferred_stages(resolve_profile("bridge_lesson").stages), list(pilot.DOWNSTREAM[:4]))
        payload = json.loads(self.plan().job_json)
        with self.assertRaisesRegex(RuntimeError, "duration"):
            runner._enforce_media_bounds({"duration_seconds": 70.02, "size_bytes": 10}, VideoJob(**payload), 1024**2)


class LinuxEnforcementTests(unittest.TestCase):
    """Linux syscall/mount mocks ONLY. These are not host enforcement evidence."""

    def setUp(self):
        self.fixture = IBMPilotTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        request = self.fixture.request
        request = replace(request, limits=replace(request.limits, max_output_bytes=512 * 1024**2,
                          max_output_files=128, max_file_bytes=4 * 1024**2))
        self.request = replace(request, limits=replace(request.limits,
                               admission_sha256=pilot.admission_fingerprint(request)))
        self.observation = self.fixture.executor.observe(self.request)
        self.policy = self.request.clip.path.parent / "default-seccomp.json"
        self.policy.write_bytes(pilot.encoded({"defaultAction": "SCMP_ACT_ERRNO", "defaultErrnoRet": 1,
            "architectures": ["SCMP_ARCH_X86_64"], "syscalls": [
                {"names": ["read", "write", "openat", *sorted(pilot.BLOCKED_LINK_SYSCALLS)], "action": "SCMP_ACT_ALLOW"},
                {"names": ["mount"], "action": "SCMP_ACT_ERRNO", "errnoRet": 1}]}))
        self.policy_sha = hashlib.sha256(self.policy.read_bytes()).hexdigest()
        self.commands = []
        self.capacity = (self.request.parent.stat().st_dev, 128, 512 * 1024**2)
        self.seals = {}
        self.root = self.request.parent
        self.native_verity = pilot._verity
        self.native_mounted_capacity = pilot._mounted_capacity
        native_safe = pilot.safe_path

        def linux_safe(path, **kwargs):
            info = native_safe(path, **kwargs)
            # The preparation API requires root, but CI runs unprivileged.
            # Model root ownership together with the mocked chown/root gate;
            # this is synthetic stat evidence, never a host ownership proof.
            values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
            values["st_uid"] = 0
            if path.name == "output.ext4":
                # Windows chmod has no Linux group/other bits. Explicitly mock
                # the Linux-owned 0600 stat result; not a Windows ACL proof.
                values["st_mode"] = (info.st_mode & ~0o777) | 0o600
            return SimpleNamespace(**values)

        def allocate(path, size):
            # A sparse fixture file, never formatted or mounted by these tests.
            with path.open("xb") as stream:
                stream.truncate(size)
            os.chmod(path, 0o600)

        def verity(fd, *, enable):
            info = os.fstat(fd)
            key = (info.st_dev, info.st_ino)
            if enable:
                self.seals[key] = hashlib.sha256(b"SYNTHETIC-VERITY" + pilot._fd_sha256(fd).encode()).hexdigest()
            if key not in self.seals:
                raise OSError("SYNTHETIC missing fs-verity")
            return self.seals[key]

        for name, value in (("_linux_root_only", lambda: None), ("_private_root_ancestry", lambda _: None),
                            ("_allocate_image", allocate), ("_mounted_capacity", lambda *_: self.capacity),
                            ("_verity", verity), ("safe_path", linux_safe)):
            patcher = patch.object(pilot, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        ownership = patch.object(pilot.os, "chown", create=True)
        ownership.start()
        self.addCleanup(ownership.stop)

    def run_host(self, argv, **kwargs):
        self.commands.append((argv, kwargs))
        return b""  # Never run a process in these tests.

    def prepare(self, **kwargs):
        return pilot.prepare_linux_enforcement(self.request, self.observation,
            default_seccomp=self.policy, default_seccomp_sha256=self.policy_sha,
            run_host=kwargs.get("run_host", self.run_host), clock=kwargs.get("clock", lambda: NOW))

    def test_prepare_enforces_fixed_commands_geometry_snapshots_and_argv(self):
        prepared = self.prepare()
        self.addCleanup(prepared.close_pins)
        self.assertEqual(len(self.commands), 2)
        self.assertEqual(self.commands[0][0][0], "/usr/sbin/mkfs.ext4")
        self.assertEqual(self.commands[1][0][0], "/usr/bin/mount")
        for argv, kwargs in self.commands:
            self.assertIn(str(prepared.image), argv)
            self.assertEqual(kwargs, {"env": pilot.HOST_ENV, "deadline_epoch": 1800,
                                      "max_output_bytes": 16384})
            self.assertNotIn("docker", argv)
        self.assertEqual(prepared.file_bytes, 4 * 1024**2)
        self.assertLessEqual(prepared.file_bytes * prepared.inode_capacity, self.request.limits.max_output_bytes)
        argv = prepared.docker_create_argv(clock=lambda: NOW)
        self.assertIn("--ulimit=fsize=4194304:4194304", argv)
        self.assertIn("--ulimit=core=0:0", argv)
        self.assertIn("--ipc=none", argv)
        self.assertTrue(any("nr_inodes=512" in arg for arg in argv))
        self.assertEqual(argv[-len(pilot.RUNNER_ARGV):], pilot.RUNNER_ARGV)
        self.assertIn("--network=none", argv)
        self.assertIn("--pull=never", argv)
        self.assertEqual(prepared.evidence()["host_qualification"], "NOT_TESTED")
        self.assertEqual(prepared.evidence()["docker_mount_handoff"], "NOT_TESTED")
        self.assertFalse(prepared.evidence()["live_admitted"])
        self.assertEqual(len(prepared.pins), 8)
        for mount in prepared.plan.mounts:
            if mount.readonly:
                self.assertTrue(Path(mount.source).is_relative_to(prepared.control_scope / "inputs"))
            else:
                self.assertTrue(Path(mount.source).is_relative_to(prepared.plan.scope))
        for leaf in pilot.SPOOL_LEAVES:
            self.assertEqual(list((prepared.plan.scope / "spool" / leaf).iterdir()), [])
        # Original writable inputs can no longer substitute the sealed copy.
        self.request.clip.path.write_bytes(b"changed original")
        prepared.check()
        self.assertEqual(hashlib.sha256((prepared.control_scope / "inputs/clip.mp4").read_bytes()).hexdigest(),
                         self.request.clip.sha256)

    def test_actual_inode_rounding_over_cap_blocks_and_keeps_attempt(self):
        self.capacity = (self.capacity[0], 144, 512 * 1024**2)
        with self.assertRaisesRegex(pilot.PilotBlocked, "ACTUAL_FILESYSTEM_CAP"):
            self.prepare()
        self.assertEqual(len(self.commands), 2)
        with self.assertRaisesRegex(pilot.PilotBlocked, "DUPLICATE_OR_UNKNOWN"):
            self.prepare()
        self.assertEqual(len(self.commands), 2)
        self.assertTrue((self.root / ("pilot-" + self.request.run_id) / "output.ext4").exists())

    def test_actual_block_capacity_over_cap_blocks(self):
        self.capacity = (self.capacity[0], 128, 513 * 1024**2)
        with self.assertRaisesRegex(pilot.PilotBlocked, "ACTUAL_FILESYSTEM_CAP"):
            self.prepare()

    def test_native_wav_requires_sufficient_fsize(self):
        request = replace(self.request, limits=replace(self.request.limits, max_file_bytes=2 * 1024**2))
        self.request = replace(request, limits=replace(request.limits,
                               admission_sha256=pilot.admission_fingerprint(request)))
        with self.assertRaisesRegex(pilot.PilotBlocked, "FILE_CAP_TOO_SMALL_FOR_NATIVE_WAV"):
            self.prepare()

    def test_mount_failure_never_retries_or_cleans_existing_resources(self):
        def failing(argv, **kwargs):
            self.run_host(argv, **kwargs)
            if argv[0] == "/usr/bin/mount":
                raise OSError("SYNTHETIC unsupported mount")
            return b""
        with self.assertRaises(OSError):
            self.prepare(run_host=failing)
        self.assertEqual(len(self.commands), 2)
        self.assertFalse(any("umount" in argv[0] or "stop" in argv for argv, _ in self.commands))

    def test_unsupported_verity_fails_closed_and_retains_storage(self):
        with patch.object(pilot, "_verity", side_effect=OSError("SYNTHETIC EOPNOTSUPP")):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertTrue((self.root / ("pilot-" + self.request.run_id) / "output.ext4").exists())

    def test_sealed_path_replacement_blocks_before_create_argv(self):
        prepared = self.prepare()
        self.addCleanup(prepared.close_pins)
        original = prepared.control_scope / "inputs/clip.mp4"
        # Linux permits rename with an open fd; Windows denies it. Model the
        # post-rename lstat identity explicitly instead of changing privileges.
        native_safe = pilot.safe_path
        def replaced(path, **kwargs):
            info = native_safe(path, **kwargs)
            return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino + 1, st_size=info.st_size) if path == original else info
        with patch.object(pilot, "safe_path", replaced):
            with self.assertRaisesRegex(pilot.PilotBlocked, "SEALED_INPUT_PATH_REPLACED"):
                prepared.docker_create_argv(clock=lambda: NOW)

    def test_extra_snapshot_file_is_not_accepted(self):
        prepared = self.prepare()
        self.addCleanup(prepared.close_pins)
        # Deliberately model a trusted-root tree mutation even on non-root CI.
        # Root ancestry is mocked; this does not relax production permissions.
        os.chmod(prepared.control_scope / "inputs/asr", 0o755)
        (prepared.control_scope / "inputs/asr/unexpected.json").write_bytes(b"{}")
        with self.assertRaisesRegex(pilot.PilotBlocked, "SNAPSHOT_TREE_CHANGED"):
            prepared.check()

    def test_writable_bind_source_replacement_blocks_before_create(self):
        prepared = self.prepare()
        self.addCleanup(prepared.close_pins)
        speaker = prepared.plan.scope / "model-cache/speaker"
        speaker.rename(speaker.with_name("previous-speaker"))
        speaker.mkdir()
        for name in pilot.SPEAKER_HASHES:
            (speaker / name).write_bytes(b"")
        (speaker / "unapproved.bin").write_bytes(b"not admitted")
        with self.assertRaisesRegex(pilot.PilotBlocked, "SCRATCH_PATH_REPLACED"):
            prepared.docker_create_argv(clock=lambda: NOW)

    def test_foreign_device_writable_leaf_fails_preparation(self):
        self.capacity = (self.capacity[0] + 1, *self.capacity[1:])
        with self.assertRaisesRegex(pilot.PilotBlocked, "SCRATCH_OUTSIDE_BOUNDED_FILESYSTEM"):
            self.prepare()

    def test_unexpected_initial_writable_content_blocks_before_create(self):
        prepared = self.prepare()
        self.addCleanup(prepared.close_pins)
        unexpected = prepared.plan.scope / "output/unapproved.bin"
        unexpected.write_bytes(b"not admitted")
        with self.assertRaisesRegex(pilot.PilotBlocked, "UNEXPECTED_INITIAL_SCRATCH_FILE"):
            prepared.docker_create_argv(clock=lambda: NOW)
        unexpected.unlink()
        (prepared.plan.scope / "output/extra-directory").mkdir()
        with self.assertRaisesRegex(pilot.PilotBlocked, "UNEXPECTED_INITIAL_SCRATCH_DIRECTORY"):
            prepared.docker_create_argv(clock=lambda: NOW)

    def test_capacity_change_and_deadline_block_create_argv(self):
        prepared = self.prepare()
        self.addCleanup(prepared.close_pins)
        with self.assertRaisesRegex(pilot.PilotBlocked, "DEADLINE_BEFORE_START"):
            prepared.docker_create_argv(clock=lambda: 1800)
        self.capacity = (self.capacity[0] + 1, 128, 512 * 1024**2)
        with self.assertRaisesRegex(pilot.PilotBlocked, "OUTPUT_CAPACITY_CHANGED"):
            prepared.docker_create_argv(clock=lambda: NOW)

    def test_preparation_deadline_keeps_unknown_no_host_command(self):
        values = iter([NOW, 1801])
        with self.assertRaisesRegex(pilot.PilotBlocked, "PREPARATION_DEADLINE"):
            self.prepare(clock=lambda: next(values))
        self.assertFalse(self.commands)
        with self.assertRaisesRegex(pilot.PilotBlocked, "DUPLICATE_OR_UNKNOWN"):
            self.prepare()

    def test_seccomp_only_removes_permission_grants(self):
        raw = self.policy.read_bytes()
        source = json.loads(raw)
        narrowed = json.loads(pilot.tightened_seccomp(raw, self.policy_sha))
        self.assertEqual({k: v for k, v in narrowed.items() if k != "syscalls"},
                         {k: v for k, v in source.items() if k != "syscalls"})
        allowed = {name for rule in narrowed["syscalls"] if rule["action"] == "SCMP_ACT_ALLOW" for name in rule["names"]}
        self.assertEqual(allowed, {"read", "write", "openat"})
        self.assertEqual(narrowed["syscalls"][1], source["syscalls"][1])
        for changes in ({"defaultAction": "SCMP_ACT_ALLOW"}, {"listenerPath": "/untrusted"},
                        {"syscalls": [{"names": ["*"], "action": "SCMP_ACT_ALLOW"}]},
                        {"syscalls": [{"names": ["linkat"], "action": "SCMP_ACT_NOTIFY"}]}):
            data = pilot.encoded({**source, **changes})
            with self.subTest(changes=changes):
                with self.assertRaises(pilot.PilotBlocked):
                    pilot.tightened_seccomp(data, hashlib.sha256(data).hexdigest())
        with self.assertRaisesRegex(pilot.PilotBlocked, "SECCOMP_BASELINE_HASH"):
            pilot.tightened_seccomp(raw, "0" * 64)

    def test_fsverity_ioctl_encoding_and_measurement_are_schema_checked(self):
        calls = []
        def ioctl(fd, command, value, *_):
            calls.append(command)
            self.assertEqual(fd, 123)
            if command == 0x40806685:
                self.assertEqual(len(value), 128)
                self.assertEqual(pilot.struct.unpack_from("=IIII", value), (1, 1, 4096, 0))
                self.assertFalse(any(value[16:]))
            else:
                self.assertEqual(command, 0xC0046686)
                self.assertEqual(pilot.struct.unpack_from("=HH", value), (0, 32))
                value[:] = pilot.struct.pack("=HH", 1, 32) + b"\x22" * 32
        with patch.dict(sys.modules, {"fcntl": SimpleNamespace(ioctl=ioctl)}):
            self.assertEqual(self.native_verity(123, enable=True), "22" * 32)
        self.assertEqual(calls, [0x40806685, 0xC0046686])

    def test_kernel_mountinfo_and_loop_backing_are_checked(self):
        case = self
        class LinuxPath(PurePosixPath):
            def read_text(path):
                if path == LinuxPath("/proc/self/mountinfo"):
                    return row
                case.assertEqual(path, LinuxPath("/sys/dev/block/7:7/loop/backing_file"))
                return "synthetic/private/output.ext4\n"
            def stat(path):
                return SimpleNamespace(st_dev=777)
        mountpoint, image = LinuxPath("/synthetic/private/bounded"), LinuxPath("/synthetic/private/output.ext4")
        row = f"40 20 7:7 / {mountpoint} rw,nosuid,nodev,noexec - ext4 /dev/loop7 rw\n"
        with patch.object(pilot, "Path", LinuxPath), \
             patch.object(pilot.os, "statvfs", return_value=SimpleNamespace(f_files=128, f_blocks=65536, f_frsize=4096), create=True), \
             patch.object(pilot.os, "major", return_value=7, create=True), patch.object(pilot.os, "minor", return_value=7, create=True):
            self.assertEqual(self.native_mounted_capacity(mountpoint, image), (777, 128, 256 * 1024**2))
            row = row.replace("rw,nosuid,nodev,noexec", "rw,nosuid,nodev")
            with self.assertRaisesRegex(pilot.PilotBlocked, "OUTPUT_MOUNT_INVALID"):
                self.native_mounted_capacity(mountpoint, image)

    def capture(self, chunks, *, cap=12, clock=lambda: NOW):
        class Selector:
            def __init__(inner):
                inner.mapping = {}
            def __enter__(inner):
                return inner
            def __exit__(inner, *_):
                return False
            def register(inner, fd, event, data):
                inner.mapping[fd] = SimpleNamespace(fd=fd, data=data)
            def unregister(inner, fd):
                del inner.mapping[fd]
            def get_map(inner):
                return inner.mapping
            def select(inner, timeout):
                return [(key, 1) for key in list(inner.mapping.values())]
        buffers = {fd: bytearray(value) for fd, value in chunks.items()}
        def read(fd, maximum):
            self.assertLessEqual(maximum, min(64 * 1024, cap + 1))
            result = bytes(buffers[fd][:maximum])
            del buffers[fd][:maximum]
            return result
        destination = self.root / "logs"
        destination.mkdir()
        with patch.object(pilot.selectors, "DefaultSelector", Selector), patch.object(pilot.os, "read", read):
            return pilot.capture_bounded_logs(10, 11, destination, max_bytes=cap, deadline_epoch=1800, clock=clock)

    def test_concurrent_log_capture_exact_combined_boundary(self):
        self.assertEqual(self.capture({10: b"123456", 11: b"abcdef"}), (b"123456", b"abcdef"))
        self.assertEqual((self.root / "logs/stdout.log").read_bytes(), b"123456")
        self.assertEqual((self.root / "logs/stderr.log").read_bytes(), b"abcdef")

    def test_log_overflow_persists_only_bounded_prefix(self):
        with self.assertRaisesRegex(pilot.PilotBlocked, "LOG_BYTES_LIMIT"):
            self.capture({10: b"1234567", 11: b"abcdef"})
        self.assertEqual(sum(p.stat().st_size for p in (self.root / "logs").iterdir()), 12)

    def test_log_deadline_retains_empty_logs_and_does_not_stop_provider(self):
        with self.assertRaisesRegex(pilot.PilotBlocked, "LOG_CAPTURE_DEADLINE"):
            self.capture({10: b"private", 11: b"error"}, clock=lambda: 1800)
        self.assertEqual(sum(p.stat().st_size for p in (self.root / "logs").iterdir()), 0)


if __name__ == "__main__":
    unittest.main()
