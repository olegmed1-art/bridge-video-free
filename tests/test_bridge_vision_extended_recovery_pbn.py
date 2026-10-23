"""Pinned outputs from the real producer on explicitly synthetic premises."""
import copy
import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path

from bridge_vision.pbn import export_frame_pbns, observation_record_sha256
from bridge_vision.recovery_pbn import load_reviewed_recovery
from bridge_vision.recovery_pbn_evidence import digest, extended_profile, TEACHER_LF_SHA256, EXTENDED_PRODUCER_R2_PINS, EXTENDED_PRODUCER_R3_PINS, RUNTIME_R3_LF_SHA256

FIXTURE_SHA = "b10162793e8de8c24fa39a090624fdc7d3c0067f4891934964e976390648f2a6"


def raw(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(value):
    return hashlib.sha256(value).hexdigest()


class ExtendedRecoveryPBNTests(unittest.TestCase):
    def setUp(self):
        data=(Path(__file__).parent/"fixtures/recognized_cards_extended_recovery_synthetic.json").read_bytes()
        self.assertEqual(sha(data),FIXTURE_SHA)
        fixture=json.loads(data);self.assertIs(fixture["synthetic_only"],True)
        self.cases=fixture["cases"]
        self.pins=self.cases["gap"]["envelope"]["producer_pins"]
        self.temp=tempfile.TemporaryDirectory(dir=Path.cwd());self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def admit(self, fixture, *, repin=False, pins=None):
        sidecar=self.root/"sidecar.json";review=self.root/"review.json"
        sidecar.write_bytes(raw(fixture["envelope"]));review.write_bytes(raw(fixture["manifest"]))
        return load_reviewed_recovery(sidecar,expected_sha256=sha(sidecar.read_bytes()),
            expected_temporal_sha256=digest(fixture["envelope"]["temporal_recovery"]) if repin else fixture["expected_result_sha256"],
            review_manifest_path=review,expected_review_manifest_sha256=sha(review.read_bytes()),
            expected_producer_pins=pins if pins is not None else self.pins,target_binding=fixture["binding"])

    def test_actual_producer_fixture_matrix_roundtrip_preserves_complete_provenance(self):
        expected={"gap":(2,1,3),"boundary":(1,1,2),"fixedpoint_full":(38,14,52),"global_order":(38,14,52),
                  "component_global":(38,14,52),"owner_unknown":(0,0,0),"played_rotated":(13,0,13),
                  "played_repeat":(13,0,13),"bounded_unknown":(0,0,0),"component_only":(0,1,1),"bounded_local_gap":(0,0,0)}
        for name,counts in expected.items():
            with self.subTest(case=name):
                fixture=self.cases[name];admitted=self.admit(fixture);record=fixture["record"]
                output=export_frame_pbns(self.root,[record],source_metadata={"title":"SYNTHETIC EXTENDED","source_id":record["source_video_sha256"]},
                    qualified_by_record_sha256={observation_record_sha256(record):admitted})[0]
                data=(self.root/output["path"]).read_bytes()
                self.assertEqual(tuple(output[k] for k in ("observed_card_count","inferred_card_count","ownership_card_count")),counts)
                self.assertEqual(admitted.payload()["temporal_recovery"],fixture["envelope"]["temporal_recovery"])
                self.assertEqual(admitted.payload()["evidence_usage"],"FROZEN_SYNTHETIC_VALIDATION")
                self.assertIs(admitted.payload()["human_identity_authenticated"],False)
                self.assertIs(admitted.payload()["runtime_authority_enrolled"],False)
                deal=re.search(rb'\[Deal "([^"]+)"\]',data).group(1).decode("ascii")
                if counts[2]==52:
                    first,hands=deal.split(":");self.assertEqual(first,"N")
                    cards=[{r+s for s,ranks in zip("SHDC",hand.split(".")) for r in ranks} for hand in hands.split()]
                    self.assertEqual([len(c) for c in cards],[13]*4);self.assertEqual(len(set.union(*cards)),52)
                elif name.startswith("played_"):
                    self.assertEqual(deal,"N:AKQJT98765432... - - -")
                else:
                    self.assertEqual(deal,"?")
                self.assertIs(output["canonical_promotion_allowed"],False)
                self.assertTrue(all(len(line)<=255 for line in data.split(b"\r\n")))
                if name=="bounded_local_gap":
                    self.assertEqual((output["upstream_observed_card_count"],output["upstream_inferred_card_count"]),(2,1))

    def test_sidecar_repin_cannot_replace_trusted_producer_result(self):
        fixture=copy.deepcopy(self.cases["gap"])
        fixture["envelope"]["temporal_recovery"]["scopes"][0]["facts"][-1]["source"]="VISUAL"
        with self.assertRaisesRegex(ValueError,"PINNED_PRODUCER_RESULT"):self.admit(fixture)
        with self.assertRaisesRegex(ValueError,"VISUAL_REVIEW_REFERENCE"):self.admit(fixture,repin=True)

    def test_snapshot_hash_anchor_and_component_bindings_cannot_be_forged(self):
        mutations=[("gap",lambda s:s["recovery"].update(rank_constraints_sha256="f"*64)),
            ("gap",lambda s:s["facts"][-1]["derivation"].update(event_id="cross-board-event")),
            ("gap",lambda s:s["facts"][-1]["derivation"].update(anchors=[])),
            ("component_global",lambda s:s["recovery"]["rank_constraints_snapshot"][0]["component_evidence"]["rank"].update(confidence=.99))]
        for name,mutate in mutations:
            fixture=copy.deepcopy(self.cases[name]);scope=fixture["envelope"]["temporal_recovery"]["scopes"][0];mutate(scope)
            if name=="component_global":scope["recovery"]["rank_constraints_sha256"]=digest(scope["recovery"]["rank_constraints_snapshot"])
            with self.subTest(case=name),self.assertRaises(ValueError):self.admit(fixture,repin=True)

    def test_cyclic_global_derivation_cannot_authorize_itself(self):
        fixture=copy.deepcopy(self.cases["global_order"]);scope=fixture["envelope"]["temporal_recovery"]["scopes"][0]
        facts=[f for f in scope["facts"] if f.get("kind")=="CONSTRAINT_INFERENCE"][:2]
        self.assertEqual(len(facts),2)
        for fact,other in ((facts[0],facts[1]),(facts[1],facts[0])):
            d=fact["derivation"];d["premise_evidence_ids"].append(other["evidence_id"])
            d["ownership_constraints"]["premise_evidence_ids"]=d["premise_evidence_ids"][:]
        with self.assertRaisesRegex(ValueError,"CYCLIC_DERIVATION"):self.admit(fixture,repin=True)

    def test_played_owner_trick_time_and_board_scope_are_bound(self):
        for field,value in (("initial_owner","W"),("trick_id","other-trick"),("played_at_ms",9000)):
            fixture=copy.deepcopy(self.cases["played_rotated"])
            fixture["envelope"]["temporal_recovery"]["scopes"][0]["facts"][-1][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):self.admit(fixture,repin=True)
        fixture=copy.deepcopy(self.cases["played_repeat"]);scope=fixture["envelope"]["temporal_recovery"]["scopes"][0]
        event=scope["event_ids"][1];scope["review_receipts"][event]["board_epoch"]=2
        fixture["manifest"]["events"][1]["board_epoch"]=2
        with self.assertRaises(ValueError):self.admit(fixture,repin=True)

    def test_target_binding_and_unqualified_runtime_authority_do_not_promote(self):
        for key in ("input_row_sha256","source_video_sha256","frame_sha256","timestamp_ms"):
            fixture=copy.deepcopy(self.cases["gap"]);fixture["binding"][key]=1 if key=="timestamp_ms" else "f"*64
            with self.subTest(key=key),self.assertRaises(ValueError):self.admit(fixture)
        fixture=copy.deepcopy(self.cases["gap"]);fixture["manifest"]["authority"]="RUNTIME_EVIDENCE"
        with self.assertRaisesRegex(ValueError,"REVIEW_MANIFEST"):self.admit(fixture)

    def test_unsupported_producer_pin_cannot_enable_extended_evidence(self):
        fixture=copy.deepcopy(self.cases["gap"]);pins=copy.deepcopy(self.pins)
        pins["bridge_vision/bridgit_rank_order_evidence.py"]="f"*64
        fixture["envelope"]["producer_pins"]=pins
        with self.assertRaisesRegex(ValueError,"UNSUPPORTED_DERIVED_PREMISE"):self.admit(fixture,pins=pins)

    def test_only_two_audited_dependency_eol_pins_are_compatible(self):
        pins=copy.deepcopy(self.pins)
        self.assertTrue(extended_profile(pins))
        pins["bridge_vision/teacher_card_reconstruction_r265.py"]=TEACHER_LF_SHA256
        self.assertTrue(extended_profile(pins))
        pins["bridge_vision/teacher_card_reconstruction_r265.py"]="f"*64
        self.assertFalse(extended_profile(pins))

    def test_r3_profile_accepts_only_audited_dependency_bytes(self):
        pins=copy.deepcopy(EXTENDED_PRODUCER_R3_PINS)
        self.assertTrue(extended_profile(pins))
        runtime="bridge_vision/runtime_recovery_evidence.py"
        pins[runtime]=RUNTIME_R3_LF_SHA256
        pins["bridge_vision/teacher_card_reconstruction_r265.py"]=TEACHER_LF_SHA256
        self.assertTrue(extended_profile(pins))
        for key in EXTENDED_PRODUCER_R3_PINS:
            bad=copy.deepcopy(pins);bad[key]="f"*64
            with self.subTest(key=key):self.assertFalse(extended_profile(bad))
        del pins[runtime]
        self.assertFalse(extended_profile(pins))


    def test_r2_profile_does_not_qualify_closed_runtime_candidates(self):
        pins=copy.deepcopy(EXTENDED_PRODUCER_R2_PINS)
        self.assertTrue(extended_profile(pins))
        for key in ("bridge_vision/runtime_recovery_evidence.py",):
            bad=copy.deepcopy(pins);bad[key]="f"*64
            self.assertFalse(extended_profile(bad))
        fixture=copy.deepcopy(self.cases["gap"]);fixture["envelope"]["producer_pins"]=pins
        temporal=fixture["envelope"]["temporal_recovery"]
        temporal.update(status="CLOSED_UNKNOWN",scopes=[{"status":"CLOSED_UNKNOWN","recovery":None}])
        with self.assertRaisesRegex(ValueError,"SCOPED_OUTPUT"):
            self.admit(fixture,repin=True,pins=pins)
        record=fixture["record"]
        with self.assertRaises(ValueError):
            export_frame_pbns(self.root,[record],qualified_by_record_sha256={observation_record_sha256(record):{
                "hard_fact_count":0,"status":"CLOSED_UNKNOWN"}})
        self.assertFalse(list(self.root.rglob("*.pbn")))


if __name__=="__main__":
    unittest.main()
