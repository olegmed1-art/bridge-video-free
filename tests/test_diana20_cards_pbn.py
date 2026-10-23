"""Synthetic contract tests. Real saved rows are exercised by the local protocol."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.diana20_cards_pbn_adapter import adapt_diana20_row, export_diana20_jsonl, installed_runtime_pins


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def row_fixture():
    base = {"status": "PARTIAL_MACHINE_OBSERVATIONS_DECLARED_LAYOUT_ONLY",
            "recognizer_version": "bridgit-gambler-partial-pixels-v3",
            "source_video_sha256": "e5a600d8d532a4f10adef191a9bfdfbb962854429849cd55821266ad71d6868f",
            "frame_sha256": "a" * 64, "source_timestamp_ms": 1000,
            "canonical_allowed": False, "complete_deal": False,
            "cards": [{"card": "AS", "seat": "N", "screen_position": "top", "status": "MACHINE_HAND_CANDIDATE"}],
            "unknown": [{"card": None, "status": "UNKNOWN_CARD", "reason": "SYNTHETIC_TIE_5H_6H"}],
            "seat_mapping": {"accepted_as_observation": False, "human_verified": False},
            "hand_inventory_completeness": "UNKNOWN_NOT_INFERRED_FROM_CARD_COUNTS"}
    payload = copy.deepcopy(base)
    payload["recognizer_update"] = {"schema": "diana20-installed-recognizer-observation/v1",
        "version": "diana20-installed-margin-r1-20261020", "mode": "margin",
        "canonical_allowed": False, "complete_deal": False, "human_verified": False,
        "geometry_enabled": False, "factor_identity_fix_enabled": False,
        "original_payload_sha256": hashlib.sha256(encoded(base)).hexdigest(),
        "original_card_count": 1, "original_unknown_count": 1, "additional_machine_candidate_count": 0,
        "effective_cards": copy.deepcopy(base["cards"]), "effective_unknown": copy.deepcopy(base["unknown"]),
        "decisions": [], "runtime": installed_runtime_pins()}
    return {"sequence": 0, "payload": payload, "receipt": {"frame_id": "SYNTHETIC-FRAME",
        "frame_sha256": "a" * 64, "source_sha256": base["source_video_sha256"], "timestamp_ms": 1000}}


class Diana20PBNTests(unittest.TestCase):
    def test_actual_shape_preserves_machine_candidates_unknown_and_no_deal(self):
        row = row_fixture(); before = copy.deepcopy(row)
        record, provenance = adapt_diana20_row(row)
        self.assertIsNone(record["deal"])
        self.assertEqual(provenance["effective_cards"], row["payload"]["recognizer_update"]["effective_cards"])
        self.assertEqual(provenance["effective_unknown"], row["payload"]["recognizer_update"]["effective_unknown"])
        self.assertEqual(row, before)

    def test_empty_partial_null_and_tie_remain_review(self):
        for card in (None, "TIE", "5H"):
            row = row_fixture(); base = row["payload"]; base["cards"][0]["card"] = card
            update = base["recognizer_update"];update["effective_cards"] = copy.deepcopy(base["cards"])
            update["original_payload_sha256"] = hashlib.sha256(encoded({k:v for k,v in base.items() if k != "recognizer_update"})).hexdigest()
            record, provenance = adapt_diana20_row(row)
            self.assertIsNone(record["deal"])
            self.assertEqual(provenance["effective_cards"][0]["card"], card)

    def test_real_no_hand_corners_variant_has_no_base_version(self):
        row=row_fixture();p=row["payload"];p.pop("recognizer_version");p.update(status="NO_HAND_CORNERS",cards=[])
        u=p["recognizer_update"];u.update(original_card_count=0,effective_cards=[])
        u["original_payload_sha256"]=hashlib.sha256(encoded({k:v for k,v in p.items() if k!="recognizer_update"})).hexdigest()
        record,provenance=adapt_diana20_row(row)
        self.assertIsNone(record["deal"]);self.assertEqual(provenance["effective_cards"],[])
        p["status"]="FUTURE_UNKNOWN_STATUS"
        with self.assertRaisesRegex(ValueError,"BASE_VERSION"):adapt_diana20_row(row)

    def test_bad_pins_authority_or_binding_rejected(self):
        variants = []
        for mutate in (
            lambda r:r["payload"]["recognizer_update"]["runtime"].update(worker_sha256="f"*64),
            lambda r:r["payload"]["recognizer_update"].update(geometry_enabled=True),
            lambda r:r["payload"]["recognizer_update"].update(human_verified=True),
            lambda r:r["payload"]["recognizer_update"].update(complete_deal=True),
            lambda r:r["receipt"].update(frame_sha256="b"*64),
            lambda r:r["payload"]["recognizer_update"].update(original_payload_sha256="f"*64),
            lambda r:r["payload"]["recognizer_update"].update(effective_cards=[]),
            lambda r:r["payload"]["recognizer_update"].update(effective_unknown=[]),
        ):
            row=row_fixture(); mutate(row);variants.append(row)
        for row in variants:
            with self.assertRaises(ValueError):adapt_diana20_row(row)

    def test_exclusive_export_retains_exact_jsonl_and_video_title(self):
        data = encoded(row_fixture()) + b"\n"
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root=Path(tmp);source=root/"card-observations.jsonl";source.write_bytes(data)
            out=export_diana20_jsonl(source,root,expected_sha256=hashlib.sha256(data).hexdigest(),video_title="Видео Диана — синтетика")
            self.assertEqual((root/out["jsonl_copy"]).read_bytes(),data)
            p=root/out["pbn_outputs"][0]["path"];self.assertIn("Видео Диана",p.name)
            self.assertIn(b'[Deal "?"]',p.read_bytes())
            self.assertIn(b'"effective_cards"',p.read_bytes())
            self.assertIn(b'SYNTHETIC_TIE_5H_6H',p.read_bytes())
            self.assertEqual(out["machine_candidates"],1)
            again=export_diana20_jsonl(source,root,expected_sha256=hashlib.sha256(data).hexdigest(),video_title="Видео Диана — синтетика")
            self.assertEqual(again["pbn_outputs"][0]["write_status"],"ALREADY_PRESENT_IDENTICAL")
            p.write_bytes(b"foreign")
            with self.assertRaises(FileExistsError):export_diana20_jsonl(source,root,expected_sha256=hashlib.sha256(data).hexdigest(),video_title="Видео Диана — синтетика")
            self.assertEqual(p.read_bytes(),b"foreign")

    def test_conditional_runtime_semantic_measurements_remain_exact_comment_claim(self):
        row=row_fixture();data=encoded(row)+b"\n";key=hashlib.sha256(encoded(row)).hexdigest()
        trace={"schema":"bridge-diana20-current-ui-evidence-v1",
               "hard_evidence":False,"canonical_promotion_allowed":False,"votes":0,
               "conditional_compass_match":True,"board_epoch":None,"settled":"UNKNOWN",
               "same_board_proven":False,"settled_proven":False,"source_ref":"SYNTHETIC_ONLY"}
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root=Path(tmp);source=root/"input.jsonl";source.write_bytes(data)
            result=export_diana20_jsonl(source,root,expected_sha256=hashlib.sha256(data).hexdigest(),
                video_title="SYNTHETIC R3",reconstruction_by_row_sha256={key:{"semantic_measurements":trace}})
            pbn=(root/result["pbn_outputs"][0]["path"]).read_bytes()
            self.assertIn(b'[Deal "?"]',pbn)
            # The exporter wraps JSON at column255, but it retains all exact
            # values under the unvalidated claim namespace, with no authority.
            provenance=b"".join(line.split(b"=",1)[1] for line in pbn.split(b"\r\n")
                if line.startswith((b"; provenance=",b"; provenance+=")))
            claim=json.loads(provenance)["external_provenance_claim"]["reconstruction_unvalidated_claim"]
            self.assertEqual(claim,{"semantic_measurements":trace})
            self.assertEqual((root/result["jsonl_copy"]).read_bytes(),data)
            self.assertIs(result["pbn_outputs"][0]["canonical_promotion_allowed"],False)


    def test_malformed_truncated_duplicate_key_or_hash_is_preflight_failure(self):
        good=encoded(row_fixture())+b"\n"
        for data, digest in ((good[:-1],None),(b'{"payload":{},"payload":{}}\n',None),(good,"f"*64),(good+good,None)):
            with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
                root=Path(tmp);source=root/"in.jsonl";source.write_bytes(data)
                with self.assertRaises(ValueError):
                    export_diana20_jsonl(source,root,expected_sha256=digest or hashlib.sha256(data).hexdigest())
                self.assertEqual(list(root.rglob("*.pbn")),[])

    def test_qualified_input_rejects_raw_claim_and_other_source_before_writes(self):
        from bridge_vision.recovery_pbn import load_reviewed_recovery
        fixture = json.loads((Path(__file__).parent / "fixtures/recognized_cards_recovery_synthetic.json").read_text("utf-8"))
        row = row_fixture(); data = encoded(row) + b"\n"
        row_key = hashlib.sha256(encoded(row)).hexdigest()
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp); source = root / "in.jsonl"; source.write_bytes(data)
            sidecar = root / "synthetic-sidecar.json"; sidecar.write_bytes(encoded(fixture["envelope"]))
            review = root / "synthetic-review.json"; review.write_bytes(encoded(fixture["review_manifest"]))
            admitted = load_reviewed_recovery(sidecar, expected_sha256=hashlib.sha256(sidecar.read_bytes()).hexdigest(),
                expected_temporal_sha256="0101827157dae60bde91dd99e3a355591a048148857ffd7f801b18eb3e1467f2",
                review_manifest_path=review, expected_review_manifest_sha256=hashlib.sha256(review.read_bytes()).hexdigest(),
                expected_producer_pins=fixture["envelope"]["producer_pins"], target_binding=fixture["binding"])
            for value, reason in ((fixture["envelope"], "PINNED_QUALIFIED_OBJECT_REQUIRED"),
                                  (admitted, "QUALIFIED_TARGET_BINDING")):
                with self.assertRaisesRegex(ValueError, reason):
                    export_diana20_jsonl(source, root, expected_sha256=hashlib.sha256(data).hexdigest(),
                        qualified_reconstruction_by_row_sha256={row_key: value})
                self.assertEqual(list(root.rglob("*.pbn")), [])
                self.assertEqual(list(root.glob("diana20-observations-*.jsonl")), [])


if __name__ == "__main__":unittest.main()
