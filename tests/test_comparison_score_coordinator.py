"""Coordinator regressions, synthetic fixtures only; all methods NOT_RUN."""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "qualification" / "offline-scorer-r2"
sys.path.insert(0, str(SOURCE / "api"))

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

adapter = load("synthetic_score_coordinator", ROOT / "tools" / "comparison_score_coordinator.py")
helper = load("reviewed_score_handoff", ROOT / "tools" / "comparison_score_handoff.py")
fixtures = load("synthetic_score_fixtures", SOURCE / "test_offline_score.py")

class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.workspace = self.base / "capture-workspace"
        self.workspace.mkdir()
        self.config = {"job_id": "synthetic-job", "job_hash": "a" * 64,
                       "source_file_id": "synthetic-source", "source_version": "1",
                       "source_sha256": "1" * 64, "sealed_manifest_sha256": "7" * 64,
                       "clip_binding_sha256": "8" * 64}
        self.raw, self.gold, self.index, self.freeze = fixtures.fixture()
        self.index["frames"][0]["frame_sha256"] = hashlib.sha256(fixtures.png_bytes()).hexdigest()
        fixtures.refresh_freeze(self.gold, self.index, self.freeze)
        self.inputs = {}
        for name, value in (("gold", self.gold), ("pts_index", self.index), ("gold_freeze", self.freeze)):
            path = self.base / (name + ".json")
            path.write_bytes(json.dumps(value, sort_keys=True).encode())
            self.inputs[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        self.sealed = {"case_id": "synthetic-case",
                       "inputs": {"gold": self.inputs["gold"],
                                  **{name: {"path": str(self.base / ("recognition-" + name)),
                                           "sha256": "2" * 64}
                                     for name in ("video", "reference", "profile", "sprite")}}}
        self.q = {"workspace_root": str(self.workspace), "ledger_root": str(self.base),
                  "runtime_trees": [{"root": str(self.base / "runtime")}],
                  "source_trees": {n: {"root": str(self.base / ("recognition-source-" + n))}
                                   for n in ("runner", "baseline", "candidate")}}
        self.qualification = self.base / "qualification.json"
        self.qualification.write_bytes(helper.encoded(self.q))
        self.capture_calls = 0
        def protected_json(path, with_bytes=False):
            raw = Path(path).read_bytes()
            value = json.loads(raw)
            return (value, raw) if with_bytes else value
        def capture(config, qualification_file=None):
            self.capture_calls += 1
            root = self.workspace / ("comparison-" + config["job_hash"]) / "raw"
            root.mkdir(parents=True)
            fixtures.write_comparison(root, self.raw, self.gold, self.index, self.freeze)
            return root
        self.video = SimpleNamespace(
            authorized_entry=lambda c: dict(c),
            qualify=lambda c, q: (self.q, {}),
            qualification_pin=lambda q, o: {"qualification_sha256": helper.digest(self.qualification.read_bytes())},
            protected_json=protected_json, digest=lambda v: helper.digest(helper.encoded(v)),
            _preflight=lambda c: self.sealed, absolute=lambda p: Path(p),
            run_qualified_comparison=capture)
        self.b = {"schema": "comparison-score-coordinator-bindings/v1",
                  "config_sha256": self.video.digest(self.config), **self.config,
                  "integration_bundle_sha256": adapter.INTEGRATION_BUNDLE,
                  "video_bundle_sha256": adapter.VIDEO_BUNDLE,
                  "scorer_sha256": adapter.SCORER_PIN, "api_checkout_sha": adapter.API_PIN,
                  "scorer_path": str(SOURCE / "offline_score.py"),
                  "api_checkout": str(SOURCE / "api"), **self.inputs,
                  "capture_review_path": str(self.base / "review.json"),
                  "control_root": str(self.base / "controls"),
                  "evaluation_workspace": str(self.base / "score-workspace")}
        self.sidecar_path = self.base / "sidecar.json"
        self.write_sidecar()
        self.patch = mock.patch.object(adapter, "components", return_value=(self.video, helper))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def write_sidecar(self):
        self.sidecar_path.write_bytes(helper.encoded(self.b))
        self.sidecar_pin = helper.digest(self.sidecar_path.read_bytes())

    def capture(self, **changes):
        args = dict(enabled=True, config=self.config, sidecar_path=self.sidecar_path,
                    sidecar_sha256=self.sidecar_pin, qualification_file=self.qualification)
        args.update(changes)
        return adapter.capture_once(**args)

    def reviewed(self, **review_changes):
        stage = self.capture()
        review = {"schema": "bridge-independent-capture-review/v1",
                  "independent_review": True, "outputs_finalized": True,
                  "scoring_not_started": True, "reviewer": "synthetic-independent-reviewer",
                  "reviewed_utc": "2026-01-04T00:00:00Z",
                  "comparison_inventory_sha256": stage["inventory_sha256"],
                  "gold_sha256": self.inputs["gold"]["sha256"],
                  "pts_index_sha256": self.inputs["pts_index"]["sha256"],
                  "gold_freeze_sha256": self.inputs["gold_freeze"]["sha256"],
                  "clip_sha256": self.gold["clip_sha256"]}
        review.update(review_changes)
        path = Path(self.b["capture_review_path"])
        path.write_bytes(helper.encoded(review))
        pin = helper.digest(path.read_bytes())
        frozen = adapter.freeze_after_review(
            enabled=True, config=self.config, sidecar_path=self.sidecar_path,
            sidecar_sha256=self.sidecar_pin, qualification_file=self.qualification,
            stage_path=stage["stage"], stage_sha256=stage["stage_sha256"], review_sha256=pin)
        return stage, frozen

    def request(self, frozen):
        return adapter.evaluator_request(
            enabled=True, config=self.config, sidecar_path=self.sidecar_path,
            sidecar_sha256=self.sidecar_pin, qualification_file=self.qualification,
            handoff_path=frozen["handoff"], handoff_sha256=frozen["handoff_sha256"],
            stage_sha256=frozen["stage_sha256"])

    def test_default_off_precedes_imports_and_filesystem(self):
        with mock.patch.object(adapter, "components", side_effect=AssertionError("must not import")):
            for function in (adapter.capture_once, adapter.freeze_after_review, adapter.evaluator_request):
                with self.subTest(function=function.__name__), self.assertRaisesRegex(adapter.CoordinatorBlocked, "disabled"):
                    if function is adapter.capture_once:
                        function(config=None, sidecar_path=None, sidecar_sha256=None)
                    elif function is adapter.freeze_after_review:
                        function(config=None, sidecar_path=None, sidecar_sha256=None,
                                 qualification_file=None, stage_path=None, stage_sha256=None,
                                 review_sha256=None)
                    else:
                        function(config=None, sidecar_path=None, sidecar_sha256=None,
                                 qualification_file=None, handoff_path=None, handoff_sha256=None, stage_sha256=None)
        self.assertEqual(self.capture_calls, 0)

    def test_three_phase_handoff_is_request_only_and_never_promotes(self):
        stage, frozen = self.reviewed()
        request = self.request(frozen)
        value = json.loads(Path(request["request"]).read_bytes())
        self.assertEqual(value["state"], "REQUEST_ONLY_NOT_RUN")
        self.assertEqual(value["limits"], adapter.LIMITS)
        self.assertFalse(value["promotion_allowed"])
        self.assertFalse(value["accuracy_evaluated"])
        self.assertEqual(self.capture_calls, 1)
        self.assertFalse(Path(value["output"]).exists())
        self.assertIn(str(self.workspace / ("comparison-" + self.config["job_hash"]) / "raw"),
                      value["read_only"])
        self.assertEqual(helper.digest(Path(request["request"]).read_bytes()), request["request_sha256"])

    def test_unfrozen_gold_is_rejected_before_capture(self):
        freeze = dict(self.freeze, outputs_seen_before_freeze=True)
        path = Path(self.inputs["gold_freeze"]["path"])
        path.write_bytes(json.dumps(freeze, sort_keys=True).encode())
        self.b["gold_freeze"]["sha256"] = helper.digest(path.read_bytes())
        self.write_sidecar()
        with self.assertRaisesRegex(adapter.CoordinatorBlocked, "pre-output"):
            self.capture()
        self.assertEqual(self.capture_calls, 0)

    def test_independent_pts_flag_is_required_before_capture(self):
        index = dict(self.index, independently_verified=False)
        path = Path(self.inputs["pts_index"]["path"])
        path.write_bytes(json.dumps(index, sort_keys=True).encode())
        self.b["pts_index"]["sha256"] = helper.digest(path.read_bytes())
        freeze = dict(self.freeze, pts_index_sha256=self.b["pts_index"]["sha256"])
        path = Path(self.inputs["gold_freeze"]["path"])
        path.write_bytes(json.dumps(freeze, sort_keys=True).encode())
        self.b["gold_freeze"]["sha256"] = helper.digest(path.read_bytes())
        self.write_sidecar()
        with self.assertRaisesRegex(ValueError, "independent PTS"):
            self.capture()
        self.assertEqual(self.capture_calls, 0)

    def test_gold_pts_and_freeze_are_denied_inside_recognition_runtime(self):
        for name in ("gold", "pts_index", "gold_freeze"):
            old = self.b[name]["path"]
            sealed_gold = dict(self.sealed["inputs"]["gold"])
            path = self.base / "runtime" / (name + ".json")
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(Path(old).read_bytes())
            self.b[name]["path"] = str(path)
            if name == "gold":
                self.sealed["inputs"]["gold"] = dict(sealed_gold, path=str(path))
            self.write_sidecar()
            with self.subTest(name=name), self.assertRaisesRegex(adapter.CoordinatorBlocked, "recognition role"):
                self.capture()
            self.b[name]["path"] = old
            self.sealed["inputs"]["gold"] = sealed_gold
        self.assertEqual(self.capture_calls, 0)

    def test_original_source_mismatch_rejected_before_capture(self):
        self.config["source_sha256"] = "f" * 64
        self.b.update(source_sha256="f" * 64, config_sha256=self.video.digest(self.config))
        self.write_sidecar()
        with self.assertRaisesRegex(adapter.CoordinatorBlocked, "source/clip/PTS"):
            self.capture()
        self.assertEqual(self.capture_calls, 0)

    def test_sealed_gold_mismatch_rejected_before_capture(self):
        self.sealed["inputs"]["gold"] = dict(self.inputs["gold"], sha256="f" * 64)
        with self.assertRaisesRegex(adapter.CoordinatorBlocked, "sealed gold"):
            self.capture()
        self.assertEqual(self.capture_calls, 0)

    def test_each_new_control_role_is_denied_inside_worker_mounts(self):
        for field in ("capture_review_path", "control_root", "evaluation_workspace"):
            old = self.b[field]
            self.b[field] = str(self.base / "recognition-source-baseline" / field)
            self.write_sidecar()
            with self.subTest(field=field), self.assertRaisesRegex(adapter.CoordinatorBlocked, "recognition role"):
                self.capture()
            self.b[field] = old
        self.assertEqual(self.capture_calls, 0)

    def test_capture_reservation_prevents_duplicate_replay(self):
        self.capture()
        with self.assertRaisesRegex(adapter.CoordinatorBlocked, "reservation"):
            self.capture()
        self.assertEqual(self.capture_calls, 1)

    def test_failed_capture_retains_reservation_and_blocks_replay(self):
        self.video.run_qualified_comparison = mock.Mock(side_effect=RuntimeError("synthetic capture failed"))
        with self.assertRaisesRegex(RuntimeError, "synthetic capture failed"):
            self.capture()
        self.assertTrue((Path(self.b["control_root"]) / "started.json").exists())
        self.assertFalse((Path(self.b["control_root"]) / "captured.json").exists())
        with self.assertRaisesRegex(adapter.CoordinatorBlocked, "reservation"):
            self.capture()
        self.assertEqual(self.video.run_qualified_comparison.call_count, 1)

    def test_out_of_band_sidecar_digest_required(self):
        self.sidecar_path.write_text("{}")
        with self.assertRaisesRegex(adapter.CoordinatorBlocked, "digest changed"):
            self.capture()
        self.assertEqual(self.capture_calls, 0)

    def test_handoff_tamper_blocks_evaluator_request(self):
        _, frozen = self.reviewed()
        Path(frozen["handoff"]).write_text("{}")
        with self.assertRaisesRegex(ValueError, "seal mismatch"):
            self.request(frozen)

    def test_modified_capture_blocks_evaluator_request(self):
        _, frozen = self.reviewed()
        root = self.workspace / ("comparison-" + self.config["job_hash"]) / "raw"
        (root / "extra.json").write_text("{}")
        with self.assertRaisesRegex(adapter.CoordinatorBlocked, "inventory differs from captured stage"):
            self.request(frozen)

    def test_new_valid_handoff_cannot_replace_capture_bound_to_old_stage(self):
        stage, frozen = self.reviewed()
        stage_bytes = Path(stage["stage"]).read_bytes()
        root = self.workspace / ("comparison-" + self.config["job_hash"]) / "raw"
        summary = json.loads((root / "comparison.json").read_bytes())
        summary["synthetic_revision"] = 2
        (root / "comparison.json").write_bytes(helper.encoded(summary))
        rows = helper.inventory(root)
        self.assertNotEqual(helper.inventory_sha256(rows), stage["inventory_sha256"])
        # Independently reviewed replacement fixture; never a real-output auto-review.
        review_path = Path(self.b["capture_review_path"])
        review = json.loads(review_path.read_bytes())
        review["comparison_inventory_sha256"] = helper.inventory_sha256(rows)
        review_path.write_bytes(helper.encoded(review))
        replacement = helper.freeze_capture(
            enabled=True, scorer=fixtures.score, comparison=root,
            state_dir=Path(self.b["control_root"]) / "synthetic-replacement-freeze",
            gold=self.inputs["gold"]["path"], gold_sha256=self.inputs["gold"]["sha256"],
            index=self.inputs["pts_index"]["path"], index_sha256=self.inputs["pts_index"]["sha256"],
            freeze=self.inputs["gold_freeze"]["path"], freeze_sha256=self.inputs["gold_freeze"]["sha256"],
            review=review_path, review_sha256=helper.digest(review_path.read_bytes()))
        capture_path = Path(frozen["receipt"]["capture"])
        capture_path.write_bytes(Path(replacement["receipt"]["capture"]).read_bytes())
        receipt = dict(replacement["receipt"], capture=str(capture_path))
        Path(frozen["handoff"]).write_bytes(helper.encoded(receipt))
        changed = dict(frozen, receipt=receipt,
                       handoff_sha256=helper.digest(Path(frozen["handoff"]).read_bytes()))
        self.assertNotEqual(changed["handoff_sha256"], frozen["handoff_sha256"])
        # Prove the new complete capture and handoff are individually valid.
        runs = fixtures.score.load_comparison(
            root, self.inputs["gold"]["sha256"], self.gold["clip_sha256"], self.freeze,
            capture_path=capture_path, capture_hash=receipt["capture_sha256"],
            index_hash=self.inputs["pts_index"]["sha256"])
        self.assertEqual(set(runs), {"baseline", "candidate"})
        self.assertTrue(helper.scoring_argv(
            handoff_path=changed["handoff"], handoff_sha256=changed["handoff_sha256"],
            scorer_path=self.b["scorer_path"], api_checkout=self.b["api_checkout"],
            output=Path(self.b["evaluation_workspace"]) / "report.json"))
        self.assertEqual(Path(stage["stage"]).read_bytes(), stage_bytes)
        with self.assertRaisesRegex(adapter.CoordinatorBlocked, "inventory differs from captured stage"):
            self.request(changed)
        self.assertFalse((Path(self.b["control_root"]) / "evaluator-request.json").exists())
        self.assertEqual(self.capture_calls, 1)

    def test_evaluator_has_no_unqualified_or_arbitrary_command_fallback(self):
        with self.assertRaisesRegex(adapter.CoordinatorBlocked, "disabled"):
            adapter.execute_evaluator()
        with self.assertRaisesRegex(adapter.CoordinatorBlocked, "unavailable"):
            adapter.execute_evaluator(enabled=True, request_path="/synthetic/request", request_sha256="a" * 64)

    def test_independent_inventory_review_cannot_be_auto_attested(self):
        with self.assertRaisesRegex(ValueError, "independent capture review"):
            self.reviewed(independent_review=False)
        self.assertEqual(self.capture_calls, 1)
        self.assertFalse((Path(self.b["control_root"]) / "freeze" / "handoff.json").exists())

    def test_inventory_quota_failure_retains_capture_without_continuation(self):
        for limit, value in (("MAX_FILES", 1), ("MAX_BYTES", 1)):
            with self.subTest(limit=limit):
                with mock.patch.object(helper, limit, value):
                    root = self.base / ("quota-" + limit)
                    root.mkdir()
                    (root / "one").write_bytes(b"synthetic")
                    (root / "two").write_bytes(b"synthetic")
                    with self.assertRaisesRegex(ValueError, "quota"):
                        helper.inventory(root)
                self.assertTrue(root.exists())

    def test_valid_qualification_replacement_cannot_renew_captured_stage(self):
        _, frozen = self.reviewed()
        self.qualification.write_bytes(helper.encoded({**self.q, "synthetic-new-proof": True}))
        with self.assertRaisesRegex(adapter.CoordinatorBlocked, "sealed captured stage"):
            self.request(frozen)

    def test_existing_handoff_file_and_byte_limits_are_kept(self):
        self.assertEqual(helper.MAX_FILES, 4096)
        self.assertEqual(helper.MAX_BYTES, 256 * 1024**2)
        self.assertFalse(hasattr(adapter, "subprocess"))

if __name__ == "__main__":
    unittest.main()
