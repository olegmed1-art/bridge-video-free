"""Synthetic file-to-local-staging tests; no real source data or credentials."""
from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from bridge_contracts.video_replay_input import build_replay_input, sha256, ReplayInputError, read_json
from bridge_contracts.video_canon_replay import replay_result_bundle
from tests.test_video_source_draft import packet


def raw(value):
    return json.dumps(value, ensure_ascii=False).encode()


def full_fixture():
    master = {
        "schema": "synthetic-master-v1", "job_id": "synthetic-job",
        "source": {"driveId": "synthetic-video", "sha256": "a" * 64},
        "transcript": [{"segment_id": "s1", "start": 10, "end": 20,
            "text": "Develop hearts because they provide 5 tricks.",
            "speaker_cluster": "synthetic-B", "speaker_role_candidate": "teacher"}],
        "episodes": [{"episode_id": "e1", "start": 9, "end": 30, "segment_ids": ["s1"]}],
        "screenshots": [{"evidence_id": "f1", "time": 15, "sha256": "b" * 64}],
    }
    mapping = {"job_id": "synthetic-job", "sourceMasterPdfDriveId": "synthetic-report",
        "sourceMasterJsonSha256": sha256(json.dumps(master, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()),
        "intervals": [{"interval_ref": "s1", "start": 10, "end": 20,
            "cluster_id": "synthetic-B", "role": "teacher", "participant_status": "PERSON_CONFIRMED",
            "evidence_refs": ["synthetic-anchor"], "conflicts": []}]}
    selection = {"schema": "video-replay-selection-v1", "input_kind": "full_master",
        "source_raw_sha256": sha256(raw(master)), "speaker_map_raw_sha256": sha256(raw(mapping)),
        "source_document_drive_id": "synthetic-report", "segment_id": "s1", "episode_id": "e1", "frame_ids": ["f1"]}
    return master, mapping, selection


class ReplayInputTests(unittest.TestCase):
    def test_full_master_copies_only_selected_source_and_never_attests(self):
        master, mapping, selection = full_fixture()
        original = deepcopy((master, mapping, selection))
        result = build_replay_input(raw(master), selection, speaker_map_bytes=raw(mapping))
        self.assertEqual((master, mapping, selection), original)
        self.assertEqual(result["selected_transcript_segment"]["exact_text"], master["transcript"][0]["text"])
        self.assertEqual(result["selected_transcript_segment"]["field_path"], "$.transcript[0]")
        self.assertEqual(result["speaker_identity_claim"]["evidence_refs"], ["synthetic-anchor"])
        self.assertFalse(result["input_builder_lineage"]["trusted_evidence"])
        replay = replay_result_bundle(result)
        self.assertEqual(replay["draft_knowledge"]["knowledge"]["claimed_tricks"], 5)
        self.assertEqual(replay["status"], "BLOCKED")
        self.assertFalse(replay["video_canon_inputs"])
        self.assertFalse(replay["promotion_commands"])

    def test_pins_and_selections_fail_closed(self):
        master, mapping, selection = full_fixture()
        for mutate in (lambda s: s.update(source_raw_sha256="0" * 64),
                       lambda s: s.update(segment_id="missing"),
                       lambda s: s.update(episode_id="missing"),
                       lambda s: s.update(frame_ids=["missing"]),
                       lambda s: s.update(speaker_map_raw_sha256="0" * 64),
                       lambda s: s.update(source_document_drive_id="wrong")):
            selected = deepcopy(selection)
            mutate(selected)
            with self.assertRaises(ReplayInputError):
                build_replay_input(raw(master), selected, speaker_map_bytes=raw(mapping))
        master["transcript"].append(deepcopy(master["transcript"][0]))
        selection["source_raw_sha256"] = sha256(raw(master))
        with self.assertRaisesRegex(ReplayInputError, "SEGMENT_NOT_UNIQUE"):
            build_replay_input(raw(master), selection, speaker_map_bytes=raw(mapping))

    def test_map_parent_mismatch_even_with_matching_raw_pin(self):
        master, mapping, selection = full_fixture()
        mapping["sourceMasterJsonSha256"] = "d" * 64
        selection["speaker_map_raw_sha256"] = sha256(raw(mapping))
        with self.assertRaisesRegex(ReplayInputError, "SPEAKER_MAP_PARENT_MISMATCH"):
            build_replay_input(raw(master), selection, speaker_map_bytes=raw(mapping))

    def test_boolean_mapped_timestamp_is_invalid(self):
        master, mapping, selection = full_fixture()
        mapping["intervals"][0]["start"] = True
        selection["speaker_map_raw_sha256"] = sha256(raw(mapping))
        with self.assertRaisesRegex(ReplayInputError, "MAPPED_INTERVAL_TIME_MISMATCH"):
            build_replay_input(raw(master), selection, speaker_map_bytes=raw(mapping))

    def test_overlap_omission_and_frame_boundary_are_preserved_blockers(self):
        master, mapping, selection = full_fixture()
        master["transcript"].append({"segment_id": "overlap", "start": 9, "end": 11, "text": "Unknown"})
        master["screenshots"][0]["time"] = 20
        selection.pop("speaker_map_raw_sha256")
        selection["source_raw_sha256"] = sha256(raw(master))
        constraints = {"example_end": 20, "overlap": {"seconds": 1}, "frame_boundary_conflicts": ["f1"]}
        selection["constraints_raw_sha256"] = sha256(raw(constraints))
        bounded = build_replay_input(raw(master), selection, constraints_bytes=raw(constraints))
        self.assertEqual(bounded["source_constraints"], constraints)
        self.assertEqual(set(bounded["input_builder_conflicts"]), {
            "OVERLAPPING_TRANSCRIPT_INTERVAL", "EPISODE_SEGMENT_OMITTED", "FRAME_OUTSIDE_HALF_OPEN_SPEECH_INTERVAL"})
        replay = replay_result_bundle(bounded)
        self.assertEqual(replay["status"], "BLOCKED")
        self.assertIsNone(replay["draft_knowledge"]["knowledge"])

    def test_bounded_schema_conversion_preserves_source_and_constraints(self):
        bounded = packet()
        bounded["schema"] = "synthetic-private-inspection-v1"
        bounded["source_constraints"] = {"overlap": {"status": "RESOLVED"}, "identity_lineage": {"claim": "PASS"}}
        selection = {"schema": "video-replay-selection-v1", "input_kind": "bounded_packet",
            "source_schema": bounded["schema"], "source_raw_sha256": sha256(raw(bounded))}
        result = build_replay_input(raw(bounded), selection)
        self.assertEqual(result["source_constraints"], bounded["source_constraints"])
        self.assertEqual(result["selected_transcript_segment"], bounded["selected_transcript_segment"])
        self.assertEqual(result["input_builder_lineage"]["original_schema"], bounded["schema"])
        self.assertEqual(replay_result_bundle(result)["status"], "BLOCKED")
        selection["constraints_raw_sha256"] = sha256(raw({"overlap": False}))
        with self.assertRaisesRegex(ReplayInputError, "CONSTRAINTS_CONFLICT"):
            build_replay_input(raw(bounded), selection, constraints_bytes=raw({"overlap": False}))

    def test_constraint_type_changes_are_not_equal(self):
        bounded = packet()
        bounded["source_constraints"] = {"example_end": 1}
        sidecar = {"example_end": True}
        selection = {"schema": "video-replay-selection-v1", "input_kind": "bounded_packet",
            "source_schema": bounded["schema"], "source_raw_sha256": sha256(raw(bounded)),
            "constraints_raw_sha256": sha256(raw(sidecar))}
        with self.assertRaisesRegex(ReplayInputError, "CONSTRAINTS_CONFLICT"):
            build_replay_input(raw(bounded), selection, constraints_bytes=raw(sidecar))

    def test_duplicate_keys_nonfinite_and_missing_sidecar_rejected(self):
        for content in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}'):
            with self.assertRaises(ReplayInputError):
                read_json(content)
        master, _, selection = full_fixture()
        with self.assertRaisesRegex(ReplayInputError, "PIN_AND_FILE_REQUIRED"):
            build_replay_input(raw(master), selection)

    def test_map_only_overlap_remains_blocked_despite_teacher_claim(self):
        master, mapping, selection = full_fixture()
        mapping["intervals"].append({"interval_ref": "synthetic-mixed", "start": 9.5, "end": 10.5,
                                    "participant_status": "MIXED", "conflicts": ["OVERLAP_UNRESOLVED"]})
        selection["speaker_map_raw_sha256"] = sha256(raw(mapping))
        built = build_replay_input(raw(master), selection, speaker_map_bytes=raw(mapping))
        self.assertIn("OVERLAPPING_MAP_INTERVAL", built["input_builder_conflicts"])
        self.assertIsNone(replay_result_bundle(built)["draft_knowledge"]["knowledge"])

    def test_map_annotations_are_retained_and_never_discharged(self):
        for field, value in (("source_constraints", {"overlap": {"status": "RESOLVED"}}),
                             ("overlap_status", "RESOLVED")):
            master, mapping, selection = full_fixture()
            mapping[field] = value
            mapping["intervals"][0]["overlap_status"] = "OVERLAP_UNRESOLVED"
            selection["speaker_map_raw_sha256"] = sha256(raw(mapping))
            built = build_replay_input(raw(master), selection, speaker_map_bytes=raw(mapping))
            replay = replay_result_bundle(built)
            self.assertEqual(replay["draft_knowledge"]["speaker_map_context_claim"][field], value)
            self.assertIn("MAP_CONSTRAINT_REVIEW_REQUIRED", built["input_builder_conflicts"])
            self.assertIn("MAPPED_INTERVAL_CONSTRAINT_REVIEW_REQUIRED", built["input_builder_conflicts"])
            self.assertIsNone(replay["draft_knowledge"]["knowledge"])

    def test_dangling_episode_refs_and_intersecting_omissions_are_conflicts(self):
        master, _, selection = full_fixture()
        master["episodes"][0]["segment_ids"].append("missing")
        master["transcript"].append({"segment_id": "unlisted", "start": 8, "end": 9.5, "text": "Synthetic"})
        selection.pop("speaker_map_raw_sha256")
        selection["source_raw_sha256"] = sha256(raw(master))
        built = build_replay_input(raw(master), selection)
        self.assertIn("EPISODE_REFERENCE_UNRESOLVED", built["input_builder_conflicts"])
        self.assertIn("EPISODE_SEGMENT_OMITTED", built["input_builder_conflicts"])

    def test_frame_constraints_block_and_original_claims_survive(self):
        master, _, selection = full_fixture()
        frame = master["screenshots"][0]
        frame["source_constraints"] = {"frame_boundary_conflicts": ["synthetic-conflict"]}
        frame["frame_bytes_verified"] = True
        selection.pop("speaker_map_raw_sha256")
        selection["source_raw_sha256"] = sha256(raw(master))
        built = build_replay_input(raw(master), selection)
        self.assertIn("SELECTED_FRAME_CONSTRAINT_REVIEW_REQUIRED", built["input_builder_conflicts"])
        self.assertEqual(built["frame_inventory_candidates"][0]["original_record"], frame)
        self.assertFalse(built["frame_inventory_candidates"][0]["frame_bytes_verified"])
        self.assertIsNone(replay_result_bundle(built)["draft_knowledge"]["knowledge"])

    def test_file_to_staging_repeat_preserves_files_and_counts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            master, mapping, selection = full_fixture()
            constraints = {"example_end": 20, "overlap": {"seconds": 1.25}, "missing_episode_refs": ["synthetic-missing"]}
            selection["constraints_raw_sha256"] = sha256(raw(constraints))
            inputs = {"master.json": raw(master), "map.json": raw(mapping), "selection.json": raw(selection), "constraints.json": raw(constraints)}
            for name, content in inputs.items():
                (root / name).write_bytes(content)
            command = [sys.executable, "-m", "tools.replay_video_canon", str(root / "master.json"),
                "--selection", str(root / "selection.json"), "--speaker-map", str(root / "map.json"),
                "--constraints", str(root / "constraints.json"), "--local-staging-db", str(root / "local.sqlite")]
            for n in (1, 2):
                proc = subprocess.run(command + ["--prepared-output", str(root / f"input{n}.json"),
                    "--output", str(root / f"receipt{n}.json")], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual((root / "input1.json").read_bytes(), (root / "input2.json").read_bytes())
            self.assertEqual((root / "receipt1.json").read_bytes(), (root / "receipt2.json").read_bytes())
            receipt = read_json((root / "receipt1.json").read_bytes())
            self.assertEqual(receipt["status"], "BLOCKED")
            self.assertEqual(receipt["draft_knowledge"]["source_constraints"], constraints)
            with closing(sqlite3.connect(root / "local.sqlite")) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM replay_staging").fetchone()[0], 1)
            for name, content in inputs.items():
                self.assertEqual((root / name).read_bytes(), content)
            bad = subprocess.run(command + ["--prepared-output", str(root / "input1.json"),
                "--output", str(root / "receipt1.json")], capture_output=True)
            self.assertNotEqual(bad.returncode, 0)


if __name__ == "__main__":
    unittest.main()
