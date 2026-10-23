"""Synthetic offline fixtures only: no recognizer, transport, database or DDS run."""
import copy
import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

from bridge_contracts.video_deal import canonicalize_video_deal
from bridge_vision.pbn import export_frame_pbns, render_frame_pbn, safe_video_stem, observation_record_sha256
from tools.bridge_video_deals import reconstruct_job
from tools.bridge_video_positions import process_job_frames
from bridge_vision import BridgeVisionEngine

SEATS = "NESW"
RANKS = "AKQJT98765432"


def record(hands=None, **extra):
    return {"status": "PARTIAL_BOARD_OBSERVATION", "engine_version": "synthetic-v1",
            "deal": canonicalize_video_deal({"hands": hands or {}}).to_dict(),
            "frame_file": "synthetic.png", "frame_sha256": "a" * 64, "time": 1.25, **extra}


def full_record(**extra):
    return record({s: [r + suit for r in RANKS] for s, suit in zip(SEATS, "SHDC")}, **extra)


def tags(data):
    return dict(re.findall(r'^\[(\w+) "([^"\r\n]*)"\]$', data.decode("ascii").replace("\r\n", "\n"), re.M))


class PBNTests(unittest.TestCase):
    def test_full_order_ten_mandatory_tags_and_no_fabricated_metadata(self):
        data, info = render_frame_pbn(full_record(dealer="W"), provenance={"synthetic": True})
        t = tags(data)
        self.assertEqual(t["Deal"], "W:...AKQJT98765432 AKQJT98765432... .AKQJT98765432.. ..AKQJT98765432.")
        self.assertEqual(t["Dealer"], "W")
        for name in ("Vulnerable", "Board", "Contract", "Declarer", "Result"):
            self.assertEqual(t[name], "?")
        self.assertEqual(len(t), 15)
        self.assertIn(b"% EXPORT\r\n", data)
        self.assertNotIn(b"[Auction", data)
        self.assertNotIn(b"[Play", data)
        self.assertEqual(info["representation"], "COMPLETE_MACHINE_OBSERVATION")
        self.assertIs(info["canonical_promotion_allowed"], False)
        self.assertIn(b"MACHINE_CANDIDATE_NOT_CONFIRMED", data)
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        self.assertTrue(all(len(line) + 2 <= 255 for line in data.split(b"\r\n")))

    def test_all_dealer_rotations_roundtrip_seats(self):
        expected = {s: [r + suit for r in RANKS] for s, suit in zip(SEATS, "SHDC")}
        for dealer in SEATS:
            t = tags(render_frame_pbn(full_record(dealer=dealer))[0])
            first, hands = t["Deal"].split(":")
            recovered = {}
            for offset, hand in enumerate(hands.split()):
                seat = SEATS[(SEATS.index(first) + offset) % 4]
                recovered[seat] = [rank + suit for suit, ranks in zip("SHDC", hand.split(".")) for rank in ranks]
            self.assertEqual(recovered, expected)

    def test_unknown_dealer_never_becomes_north(self):
        data, info = render_frame_pbn(full_record())
        self.assertEqual(tags(data)["Dealer"], "?")
        self.assertTrue(tags(data)["Deal"].startswith("N:"))
        self.assertNotIn(b"% EXPORT", data)
        self.assertEqual(info["pbn_format"], "IMPORT")

    def test_partial_hand_is_not_encoded_as_voids_or_end_position(self):
        data, info = render_frame_pbn(record({"N": ["AS", "10H"]}))
        self.assertEqual(tags(data)["Deal"], "?")
        self.assertIn(b'"N":["AS","TH"]', data)
        self.assertIn(b"PARTIAL_HANDS_IN_COMMENTS", data)
        self.assertEqual(info["observed_card_count"], 2)

    def test_whole_unknown_hands_supported_no_fourth_complement(self):
        r = full_record()
        r["deal"] = canonicalize_video_deal({"hands": {s: r["deal"]["hands"][s]["cards"] for s in "NES"}}).to_dict()
        data, info = render_frame_pbn(r)
        self.assertEqual(tags(data)["Deal"].split()[-1], "-")
        self.assertEqual(info["observed_card_count"], 39)
        self.assertEqual(info["representation"], "PARTIAL_MACHINE_OBSERVATION")
        self.assertNotIn("2C", tags(data)["Deal"])

    def test_unknown_vulnerability_and_explicit_none_are_distinct(self):
        self.assertEqual(tags(render_frame_pbn(full_record())[0])["Vulnerable"], "?")
        self.assertEqual(tags(render_frame_pbn(full_record(vulnerability="None"))[0])["Vulnerable"], "None")
        self.assertEqual(tags(render_frame_pbn(full_record(vulnerability="unknown"))[0])["Vulnerable"], "?")

    def test_conflict_duplicate_tie_null_derived_bad_counts_fail_closed(self):
        variants = []
        r = full_record(status="CONFLICT"); variants.append(r)
        for value in ("TIE", None, "AS"):
            r = full_record(); r["deal"]["hands"]["E"]["cards"][0] = value; variants.append(r)
        r = full_record(); r["deal"]["derivations"] = [{"computed_cards": ["AS"]}]; variants.append(r)
        r = full_record(); r["deal"]["hands"]["N"]["unknown_count"] = 1; variants.append(r)
        r = full_record(); r["deal"]["card_provenance"]["N"]["derived_cards"] = ["AS"]; variants.append(r)
        r = full_record(); r["deal"]["contract_version"] = "future-unsupported"; variants.append(r)
        for r in variants:
            data, info = render_frame_pbn(r)
            self.assertEqual(tags(data)["Deal"], "?")
            self.assertEqual(info["representation"], "REVIEW_INVALID_OBSERVATION")

    def test_raw_candidate_never_becomes_observed_card(self):
        r = record(); r["deal"] = None
        r["candidates"] = [{"hands": {"N": ["AS"]}}]
        self.assertEqual(tags(render_frame_pbn(r)[0])["Deal"], "?")

    def test_no_input_mutation_or_cross_frame_accumulation(self):
        rows = [record({"N": ["AS"]}), record({"N": ["KS"]})]
        before = copy.deepcopy(rows)
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            result = export_frame_pbns(Path(tmp), rows, source_metadata={"title": "Синтетика", "source_fingerprint": "b" * 64})
            self.assertEqual(len(result), 2)
            for entry in result:
                self.assertEqual(tags((Path(tmp) / entry["path"]).read_bytes())["Deal"], "?")
                self.assertEqual(entry["observed_card_count"], 1)
        self.assertEqual(rows, before)

    def test_unicode_title_safety_reserved_names_and_fallback(self):
        for title in ("Школа бриджа — урок 20", "../../CON", "NUL.txt", "CON .txt", "CONIN$", "CONOUT$.txt", "a\\b:c/d", "x\x00\n\u202e?", "．．/COM¹", ".", "LPT1", "é" * 500):
            stem = safe_video_stem(title, source_id="source-42")
            self.assertNotRegex(stem, r'[<>:"/\\|?*\x00-\x1f]')
            self.assertFalse(stem.endswith((" ", ".")))
            self.assertNotIn("..", stem)
        self.assertIn("Школа бриджа", safe_video_stem("Школа бриджа — урок 20", source_id="42"))
        self.assertIn("source-42", safe_video_stem(None, source_id="source-42"))
        for title in ("CON .txt", "CONIN$", "CONOUT$.txt"):
            self.assertTrue(safe_video_stem(title, source_id="42").startswith("_"))

    def test_repeat_delivery_foreign_collision_and_distinct_frames(self):
        rows = [record({"N": ["AS"]}), record({"N": ["AS"]}, time=2.0)]
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp); meta = {"title": "Видео / тест", "source_fingerprint": "c" * 64}
            one = export_frame_pbns(root, rows, source_metadata=meta)
            self.assertEqual(len({v["path"] for v in one}), 2)
            for entry in one:
                self.assertIn("Видео", entry["path"])
                self.assertLessEqual(len(Path(entry["path"]).name.encode("utf-8")), 240)
            two = export_frame_pbns(root, rows, source_metadata=meta)
            self.assertTrue(all(v["write_status"] == "ALREADY_PRESENT_IDENTICAL" for v in two))
            target = root / one[0]["path"]; target.write_bytes(b"foreign")
            with self.assertRaises(FileExistsError):
                export_frame_pbns(root, rows, source_metadata=meta)
            self.assertEqual(target.read_bytes(), b"foreign")

    def test_metadata_cannot_inject_tags_or_comments(self):
        data, _ = render_frame_pbn(record({"N": ["AS"]}), provenance={"title": 'x\n[Result "13"]\n} % EXPORT {я'})
        self.assertEqual(len(tags(data)), 15)
        self.assertEqual(tags(data)["Result"], "?")
        self.assertTrue(all(line.startswith((b"%", b";", b"[")) or not line for line in data.split(b"\r\n")))

    def test_optional_solver_provenance_is_record_bound_comment_only(self):
        rows = [record({"N": ["AS"]}), record({"N": ["KS"]}, time=2)]
        external = {observation_record_sha256(rows[0]): {
            "producer_version": "SYNTHETIC-SOLVER-NOT-QUALIFIED",
            "observed": [{"seat": "N", "card": "AS"}],
            "inferred": [{"seat": "E", "card": "KH", "status": "UNCONFIRMED"}],
            "unresolved_alternatives": ["KH", "QH"],
            "canonical_promotion_allowed": True, "source_original_bytes_verified": True}}
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            outputs = export_frame_pbns(root, rows, provenance_by_record_sha256=external)
            data = [(root / value["path"]).read_bytes() for value in outputs]
            self.assertIn(b"external_provenance_claim", data[0])
            self.assertNotIn(b"external_provenance_claim", data[1])
            self.assertIn(b'"inferred"', data[0])
            self.assertTrue(all(tags(value)["Deal"] == "?" for value in data))
            self.assertTrue(all(value["canonical_promotion_allowed"] is False for value in outputs))

    def test_unbound_solver_provenance_rejected_without_pbn(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "observation SHA256"):
                export_frame_pbns(root, [record()], provenance_by_record_sha256={"f" * 64: {"inferred": ["AS"]}})
            self.assertEqual(list(root.rglob("*.pbn")), [])

    def test_extreme_time_and_long_title_filename_is_bounded(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            out = export_frame_pbns(Path(tmp), [record(time=1e300)], source_metadata={"title": "Урок" * 200})
            self.assertLessEqual(len(Path(out[0]["path"]).name.encode("utf-8")), 240)
            self.assertIn("tUNKNOWN", out[0]["path"])

    def test_oversize_board_metadata_cannot_break_line_limit(self):
        data, info = render_frame_pbn(full_record(board_number=10**300, dealer="E"))
        self.assertEqual(tags(data)["Board"], "?")
        self.assertIn("BOARD_NUMBER_EXCEEDS_TAG_LIMIT", info["review_reasons"])
        self.assertTrue(all(len(line) + 2 <= 255 for line in data.split(b"\r\n")))

    def test_sidecar_folder_file_collision_preserves_foreign_entry(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            foreign = Path(tmp) / "pbn"; foreign.write_bytes(b"foreign")
            with self.assertRaises((FileExistsError, ValueError)):
                export_frame_pbns(Path(tmp), [record()])
            self.assertEqual(foreign.read_bytes(), b"foreign")

    def test_actual_positions_writer_keeps_json_diagnostics_and_title(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp); (root / "frames").mkdir()
            (root / "frames/synthetic.png").write_bytes(b"synthetic injection, not image inference")
            manifest = {"job_id": "synthetic", "source_fingerprint": "e" * 64,
                        "frames": [{"time": 1.25, "file": "synthetic.png", "sha256": "a" * 64}]}
            (root / "manifest.json").write_text(json.dumps(manifest), "utf-8")
            engine = BridgeVisionEngine({"synthetic-injection": lambda _: {
                "hands": {"N": ["AS", "KS"]}, "confidence": 0.95, "evidence": {"synthetic": True}}})
            summary = process_job_frames(root, engine=engine, video_title="Синтетическое видео")
            rows = [json.loads(line) for line in (root / "bridge_positions.jsonl").read_text("utf-8").splitlines()]
            self.assertEqual(rows[0]["candidates"][0]["evidence"], {"synthetic": True})
            self.assertEqual(rows[0]["deal"]["hands"]["N"]["cards"], ["AS", "KS"])
            self.assertEqual(len(summary["pbn_outputs"]), 1)
            self.assertIn("Синтетическое видео", summary["pbn_outputs"][0]["path"])
            data = (root / summary["pbn_outputs"][0]["path"]).read_bytes()
            self.assertEqual(tags(data)["Deal"], "?")
            saved_summary = json.loads((root / "bridge_positions_summary.json").read_text("utf-8"))
            self.assertEqual(saved_summary["pbn_outputs"], summary["pbn_outputs"])

    def test_reconstruct_real_output_retains_json_and_exports_each_frame(self):
        rows = [record({"N": ["AS"]}), record({"N": ["KS"]}, time=3)]
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            (root / "manifest.json").write_text(json.dumps({"title": "Синтетическое видео", "source_fingerprint": "d" * 64}), "utf-8")
            (root / "bridge_positions.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows), "utf-8")
            summary = reconstruct_job(root)
            self.assertTrue((root / "bridge_deals.json").is_file())
            self.assertEqual(len(summary["pbn_outputs"]), 2)
            self.assertTrue(all("Синтетическое видео" in x["path"] for x in summary["pbn_outputs"]))

    def test_existing_independent_corpus_parser_full_roundtrip(self):
        # Existing parser/validator, loaded as saved source: no solver or engine execution.
        root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(root / "dds_training"))
        try:
            spec = importlib.util.spec_from_file_location("pbn_independent_corpus", root / "dds_training/corpus.py")
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            data, _ = render_frame_pbn(full_record(board_number=1))
            # This corpus API requires its own DealID tag; prepend only in test copy.
            with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
                path = Path(tmp) / "parser-copy.pbn"
                path.write_bytes(b'[DealID "synthetic-parser-copy"]\r\n' + data)
                self.assertEqual(module.validate_pbn_corpus(path, 1)["count"], 1)
                parsed = list(module.iter_pbn_records(path))
                self.assertEqual(parsed[0]["deal"], tags(data)["Deal"])
        finally:
            sys.path.pop(0)


if __name__ == "__main__":
    unittest.main()
