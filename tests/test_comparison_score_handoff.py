"""Synthetic source-only integration controls. Execution status: NOT_RUN."""
import copy
import hashlib
import importlib.util
import json
import marshal
import struct
from pathlib import Path
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

handoff = load("comparison_score_handoff", ROOT / "tools" / "comparison_score_handoff.py")
fixtures = load("reviewed_synthetic_fixture", SOURCE / "test_offline_score.py")
score = handoff.scorer_at(SOURCE / "offline_score.py")

class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "capture"
        self.root.mkdir()
        raw, self.gold, self.index, self.freeze = fixtures.fixture()
        self.index["frames"][0]["frame_sha256"] = hashlib.sha256(fixtures.png_bytes()).hexdigest()
        fixtures.refresh_freeze(self.gold, self.index, self.freeze)
        fixtures.write_comparison(self.root, raw, self.gold, self.index, self.freeze)
        self.paths, self.pins = {}, {}
        for name, value in (("gold", self.gold), ("index", self.index), ("freeze", self.freeze)):
            path = self.base / (name + ".json")
            # Same canonical bytes as the reviewed synthetic freeze fixture.
            path.write_bytes(json.dumps(value, sort_keys=True).encode())
            self.paths[name], self.pins[name] = path, score.sha(path)
        self.approval = {
            "schema": "bridge-independent-capture-review/v1",
            "independent_review": True, "outputs_finalized": True,
            "scoring_not_started": True, "reviewer": "synthetic-independent-reviewer",
            "reviewed_utc": "2026-01-04T00:00:00Z",
            "comparison_inventory_sha256": handoff.inventory_sha256(handoff.inventory(self.root)),
            "gold_sha256": self.pins["gold"], "pts_index_sha256": self.pins["index"],
            "gold_freeze_sha256": self.pins["freeze"],
            "clip_sha256": self.gold["clip_sha256"],
        }
        self.write_review()

    def write_review(self):
        path = self.base / "review.json"
        path.write_bytes(handoff.encoded(self.approval))
        self.paths["review"], self.pins["review"] = path, score.sha(path)

    def produce(self, **changes):
        args = {"enabled": True, "scorer": score, "comparison": self.root,
                "state_dir": self.base / "controls"}
        for name in ("gold", "index", "freeze", "review"):
            args[name], args[name + "_sha256"] = self.paths[name], self.pins[name]
        args.update(changes)
        return handoff.freeze_capture(**args)

    def test_default_off_has_no_filesystem_access(self):
        with self.assertRaisesRegex(ValueError, "disabled"):
            self.produce(enabled=False, comparison=self.base / "does-not-exist")
        self.assertFalse((self.base / "controls").exists())

    def test_frozen_handoff_loads_exact_r3_and_scores_without_promotion(self):
        before = handoff.inventory(self.root)
        result = self.produce()
        receipt = result["receipt"]
        self.assertEqual(score.sha(result["handoff"]), result["handoff_sha256"])
        self.assertEqual(handoff.inventory(self.root), before)
        capture = Path(receipt["capture"])
        self.assertEqual(score.sha(capture), receipt["capture_sha256"])
        self.assertEqual((capture.parent / "capture.sha256").read_text().strip(),
                         receipt["capture_sha256"])
        variants = score.load_comparison(
            self.root, self.pins["gold"], self.gold["clip_sha256"], self.freeze,
            capture_path=capture, capture_hash=receipt["capture_sha256"],
            index_hash=self.pins["index"])
        for variant in ("baseline", "candidate"):
            report = score.evaluate(
                variants[variant]["raw"], self.gold, self.index, self.freeze,
                gold_hash=self.pins["gold"], index_hash=self.pins["index"],
                boundary_events=variants[variant]["boundary_events"])
            self.assertEqual(report["cards"]["tp"], 2)
            self.assertEqual(report["auction"]["complete_sequence_correct"], 1)
            self.assertFalse(report["promotion_allowed"])
        self.assertFalse(receipt["accuracy_evaluated"])

    def test_missing_independent_review_is_not_auto_attested(self):
        for field in ("independent_review", "outputs_finalized", "scoring_not_started"):
            with self.subTest(field=field):
                previous = self.approval[field]
                self.approval[field] = False
                self.write_review()
                with self.assertRaisesRegex(ValueError, "independent capture review"):
                    self.produce()
                self.assertFalse((self.base / "controls").exists())
                self.approval[field] = previous

    def test_review_must_bind_full_inventory_and_independent_inputs(self):
        for field in ("comparison_inventory_sha256", "gold_sha256", "pts_index_sha256",
                      "gold_freeze_sha256", "clip_sha256"):
            with self.subTest(field=field):
                previous = self.approval[field]
                self.approval[field] = "f" * 64
                self.write_review()
                with self.assertRaisesRegex(ValueError, "binding mismatch"):
                    self.produce()
                self.assertFalse((self.base / "controls").exists())
                self.approval[field] = previous

    def test_output_edit_after_independent_review_blocks_freeze(self):
        path = next(self.root.glob("candidate/*/result.json"))
        path.write_text("{}")
        with self.assertRaisesRegex(ValueError, "binding mismatch"):
            self.produce()
        self.assertFalse((self.base / "controls").exists())

    def test_external_review_digest_is_required(self):
        self.paths["review"].write_text("{}")
        with self.assertRaisesRegex(score.ScoringError, "seal mismatch"):
            self.produce()
        self.assertFalse((self.base / "controls").exists())

    def test_predicate_claim_cannot_replace_pre_output_gold_freeze(self):
        self.freeze["outputs_seen_before_freeze"] = True
        self.paths["freeze"].write_bytes(json.dumps(self.freeze, sort_keys=True).encode())
        self.pins["freeze"] = score.sha(self.paths["freeze"])
        self.approval["gold_freeze_sha256"] = self.pins["freeze"]
        self.write_review()
        with self.assertRaisesRegex(ValueError, "pre-output gold"):
            self.produce()
        self.assertFalse((self.base / "controls").exists())

    def test_gold_index_source_identity_mismatch_blocks_freeze(self):
        self.index["source_sha256"] = "e" * 64
        self.paths["index"].write_bytes(json.dumps(self.index, sort_keys=True).encode())
        self.pins["index"] = score.sha(self.paths["index"])
        self.freeze["pts_index_sha256"] = self.pins["index"]
        self.paths["freeze"].write_bytes(json.dumps(self.freeze, sort_keys=True).encode())
        self.pins["freeze"] = score.sha(self.paths["freeze"])
        self.approval.update(pts_index_sha256=self.pins["index"],
                             gold_freeze_sha256=self.pins["freeze"])
        self.write_review()
        with self.assertRaisesRegex(ValueError, "source/clip/PTS"):
            self.produce()

    def test_control_directory_inside_capture_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "inside capture"):
            self.produce(state_dir=self.root / "controls")
        self.assertFalse((self.root / "controls").exists())

    def test_incomplete_capture_cannot_issue_handoff(self):
        path = self.root / "comparison.json"
        summary = json.loads(path.read_text())
        summary["status"] = "REPLAY_ERROR"
        path.write_bytes(handoff.encoded(summary))
        self.approval["comparison_inventory_sha256"] = handoff.inventory_sha256(handoff.inventory(self.root))
        self.write_review()
        with self.assertRaisesRegex(score.ScoringError, "incomplete replay"):
            self.produce()
        self.assertFalse((self.base / "controls" / "handoff.json").exists())
        self.assertFalse((self.base / "controls" / "capture.sha256").exists())

    def test_extra_file_after_freeze_cannot_be_scored(self):
        result = self.produce()
        receipt = result["receipt"]
        (self.root / "extra.json").write_text("{}")
        with self.assertRaisesRegex(score.ScoringError, "inventory"):
            score.load_comparison(
                self.root, self.pins["gold"], self.gold["clip_sha256"], self.freeze,
                capture_path=receipt["capture"], capture_hash=receipt["capture_sha256"],
                index_hash=self.pins["index"])

    def test_exact_cli_plan_is_separate_and_does_not_execute(self):
        result = self.produce()
        output = self.base / "report.json"
        argv = handoff.scoring_argv(
            handoff_path=result["handoff"], handoff_sha256=result["handoff_sha256"],
            scorer_path=SOURCE / "offline_score.py", api_checkout=SOURCE / "api",
            output=output, python="/synthetic/python")
        self.assertEqual(argv[:3], ["/synthetic/python", "-I", "-B"])
        self.assertEqual(argv[argv.index("--repo-sha") + 1], score.PINNED_API_SHA)
        self.assertEqual(argv[argv.index("--capture-sha256") + 1],
                         result["receipt"]["capture_sha256"])
        self.assertFalse(output.exists())
        # Fixture source PTS 10000 deliberately differs from requested time 0.
        self.assertEqual(self.index["frames"][0]["source_pts"], 10000)
        self.assertEqual(self.index["frames"][0]["requested_timestamp_ms"], 0)

    def test_handoff_tamper_prevents_cli_plan(self):
        result = self.produce()
        Path(result["handoff"]).write_text("{}")
        with self.assertRaisesRegex(ValueError, "seal mismatch"):
            handoff.scoring_argv(
                handoff_path=result["handoff"], handoff_sha256=result["handoff_sha256"],
                scorer_path=SOURCE / "offline_score.py", api_checkout=SOURCE / "api",
                output=self.base / "report.json")

    def test_report_cannot_mutate_frozen_capture_or_controls(self):
        result = self.produce()
        for output in (self.root / "report.json", self.base / "controls" / "report.json"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                handoff.scoring_argv(
                    handoff_path=result["handoff"], handoff_sha256=result["handoff_sha256"],
                    scorer_path=SOURCE / "offline_score.py", api_checkout=SOURCE / "api",
                    output=output)

class TrustedLoaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "offline_score.py"
        self.verified_bytes = (SOURCE / "offline_score.py").read_bytes()
        self.assertEqual(hashlib.sha256(self.verified_bytes).hexdigest(), handoff.SCORER_SHA256)
        self.source.write_bytes(self.verified_bytes)

    def test_path_replacement_after_digest_executes_verified_snapshot_once(self):
        actual_digest = handoff.digest
        actual_read = Path.read_bytes
        reads = []
        def replace_after_check(data):
            value = actual_digest(data)
            self.source.write_bytes(b'raise RuntimeError("UNVERIFIED_PATH_EXECUTED")\n')
            return value
        def read_once(path):
            if path == self.source:
                reads.append(path)
            return actual_read(path)
        with mock.patch.object(handoff, "digest", side_effect=replace_after_check), \
                mock.patch.object(Path, "read_bytes", new=read_once):
            module = handoff.scorer_at(self.source)
        self.assertEqual(len(reads), 1)
        self.assertEqual(module.SCORER_VERSION, "offline-score-capture-repairs-r2")
        self.assertIn(b"UNVERIFIED_PATH_EXECUTED", self.source.read_bytes())

    def test_valid_timestamp_pyc_injected_after_digest_is_never_consumed(self):
        actual_digest = handoff.digest
        cache = Path(importlib.util.cache_from_source(str(self.source)))
        malicious = compile('raise RuntimeError("UNVERIFIED_PYC_EXECUTED")',
                            str(self.source), "exec")
        def inject_after_check(data):
            value = actual_digest(data)
            state = self.source.stat()
            cache.parent.mkdir()
            header = importlib.util.MAGIC_NUMBER + struct.pack(
                "<III", 0, int(state.st_mtime) & 0xffffffff, state.st_size)
            cache.write_bytes(header + marshal.dumps(malicious))
            return value
        with mock.patch.object(handoff, "digest", side_effect=inject_after_check):
            module = handoff.scorer_at(self.source)
        self.assertTrue(cache.exists())
        self.assertEqual(module.SCORER_VERSION, "offline-score-capture-repairs-r2")
        self.assertEqual(self.source.read_bytes(), self.verified_bytes)

    def test_each_load_has_a_fresh_module_namespace(self):
        first = handoff.scorer_at(self.source)
        first.untrusted_shared_marker = True
        second = handoff.scorer_at(self.source)
        self.assertIsNot(first, second)
        self.assertFalse(hasattr(second, "untrusted_shared_marker"))

    def test_wrong_digest_never_reaches_compilation(self):
        self.source.write_bytes(b'raise RuntimeError("MUST_NOT_EXECUTE")\n')
        with mock.patch("builtins.compile") as compiler:
            with self.assertRaisesRegex(ValueError, "scorer pin mismatch"):
                handoff.scorer_at(self.source)
            compiler.assert_not_called()

if __name__ == "__main__":
    unittest.main()
