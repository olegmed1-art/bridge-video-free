"""Synthetic temporal contracts plus saved REAL diagnostic abstention only."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bridge_contracts.video_deal import FULL_DECK, SEATS
from bridge_vision.bridgit_adjacent_candidate_profile import PROFILE
from bridge_vision.bridgit_partial_evidence import SamplingContract, accumulate_detected_frames, accumulate_partial_frames
from bridge_vision.native_cards import NativeFourSeatCardDetector, CARD_DETECTOR_VERSION
from bridge_vision.original_deal_solver import RecoveryInputError
from bridge_vision.temporal_deal_recovery import (
    BINDINGS, TEMPORAL_VERSION, ReviewedRecoveryEvidence, ReviewedRecoveryBatch, accumulate_recovery_frames, event_id,
)
from bridge_vision.teacher_card_reconstruction_r265 import extract_teacher_card_declarations, declaration_digest


class TemporalTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "synthetic-source.bin"
        self.source.write_bytes(b"synthetic source, not a video")
        self.source_sha = sha256(self.source.read_bytes()).hexdigest()

    def frame(self, index=0, cards=(("AS", "N"),)):
        return {"source_video_sha256": self.source_sha, "frame_sha256": sha256(str(index).encode()).hexdigest(),
                "episode_id": "test", "board_epoch": 1, "board_number": 1,
                "board_number_read": {"status": "READ", "number": 1},
                "board_number_confirming_read": {"status": "READ", "number": 1},
                "timestamp_ms": index * 3000, "observation_index": index,
                "raw_frame_size": {"width": 100, "height": 100}, "recognizer_version": CARD_DETECTOR_VERSION,
                "reference_profile_id": "test-reference", "pixel_observer_version": "test-pixels",
                "data_class": "SYNTHETIC_REGRESSION",
                "observations": [{"status": "ACCEPTED_VISUAL", "source": "VISUAL", "card": c,
                                  "seat": s, "confidence": .99} for c, s in cards]}

    def proof(self, frame, previous=None):
        result = {k: deepcopy(frame[k]) for k in BINDINGS}
        result.update(scene_id="scene-1", orientation_id="orientation-1", settled=True,
                      source_frame_pts_ref="synthetic-independent-pts", deal_identity_ref="synthetic-deal",
                      seat_assignment_ref="synthetic-seat", settled_frame_ref="synthetic-settled",
                      screen_to_seat={s: s for s in SEATS},
                      cards=[{"observation_index": i, "card": c["card"], "screen_seat": c["seat"],
                              "kind": "IN_HAND", "evidence_ref": f"synthetic-card-{i}"}
                             for i, c in enumerate(frame["observations"])])
        if previous:
            result.update(continuous_from_event_id=event_id(previous), continuity_ref="synthetic-continuity")
        return result

    def evidence(self, proofs):
        return ReviewedRecoveryEvidence(self.source, {"version": TEMPORAL_VERSION,
            "authority": "INDEPENDENT_REVIEW", "reviewer_ref": "synthetic-test-oracle",
            "source_video_sha256": self.source_sha, "events": proofs})

    def test_two_frames_unique_and_real_existing_accumulator_callsite(self):
        cards = [(c, SEATS[i // 13]) for i, c in enumerate(sorted(FULL_DECK)[:39])]
        frames = [self.frame(0, cards[:20]), self.frame(1, cards[20:])]
        evidence = self.evidence([self.proof(frames[0]), self.proof(frames[1], frames[0])])
        contract = SamplingContract(PROFILE.version, 1000, 3000, PROFILE.max_gap_ms, .8)
        result = accumulate_partial_frames(frames, contract=contract, recovery_evidence=evidence)
        recovery = result["recovery"]["scopes"][0]["recovery"]
        self.assertEqual(recovery["status"], "VERIFIED_FULL")
        self.assertEqual(len(recovery["inferred"]), 13)
        self.assertEqual(result["tracks"][0]["observed_card_count"], 39)
        self.assertEqual(result["tracks"][0]["deal"]["hands"]["W"]["cards"], [])

    def test_score_never_creates_hard_evidence(self):
        frame = self.frame()
        self.assertEqual(accumulate_recovery_frames([frame])["status"], "CLOSED_UNKNOWN")
        proof = self.proof(frame)
        proof["cards"] = []
        result = accumulate_recovery_frames([frame], evidence=self.evidence([proof]))
        scope = result["scopes"][0]
        self.assertEqual(scope["facts"], [])
        self.assertEqual(scope["recovery"]["inferred"], {})
        self.assertEqual(scope["review_observations"][0]["reason"], "CARD_NOT_INDEPENDENTLY_REVIEWED")

    def test_reset_and_unknown_boundary_orientation_and_model(self):
        for change in ("board_epoch", "scene_id", "orientation_id", "screen_to_seat", "recognizer_version",
                       "pixel_observer_version", "episode_id", "continuous_from_event_id", "settled", "source_frame_pts_ref"):
            frames = [self.frame(0), self.frame(1, (("KS", "N"),))]
            if change in ("board_epoch", "recognizer_version", "pixel_observer_version", "episode_id"):
                frames[1][change] = 2 if change == "board_epoch" else "changed"
            proofs = [self.proof(frames[0]), self.proof(frames[1], frames[0])]
            if change in ("scene_id", "orientation_id", "continuous_from_event_id"):
                proofs[1][change] = "changed"
            if change == "screen_to_seat":
                proofs[1][change] = dict(zip(SEATS, ("E", "S", "W", "N")))
            if change == "settled":
                proofs[1][change] = False
            if change == "source_frame_pts_ref":
                proofs[1].pop(change)
            result = accumulate_recovery_frames(frames, evidence=self.evidence(proofs))
            self.assertEqual(len(result["scopes"]), 2, change)
            self.assertEqual(len(result["scopes"][0]["facts"]), 1, change)

    def test_repeated_pixels_redelivery_reordering_and_payload_conflict(self):
        frames = [self.frame(), self.frame(1)]
        frames[1]["frame_sha256"] = frames[0]["frame_sha256"]
        evidence = self.evidence([self.proof(frames[0]), self.proof(frames[1], frames[0])])
        result = accumulate_recovery_frames(frames, evidence=evidence)
        self.assertEqual(result, accumulate_recovery_frames([frames[1], frames[0], frames[0]], evidence=evidence))
        self.assertEqual(len(result["history"]), 2)
        self.assertEqual(result["scopes"][0]["visual_unit_count"], 1)
        self.assertEqual(result["scopes"][0]["independent_vote_count"], 0)
        self.assertEqual(len(result["scopes"][0]["recovery"]["observed"]), 1)
        changed = deepcopy(frames[0])
        changed["observations"][0]["card"] = "KS"
        bad = accumulate_recovery_frames([frames[0], changed], evidence=evidence)
        self.assertEqual(bad["status"], "CONFLICT")
        self.assertTrue(all(s["recovery"] is None for s in bad["scopes"]))

    def test_same_card_different_seats_and_wrong_orientation(self):
        frames = [self.frame(), self.frame(1, (("AS", "S"),))]
        evidence = self.evidence([self.proof(frames[0]), self.proof(frames[1], frames[0])])
        result = accumulate_recovery_frames(frames, evidence=evidence)
        self.assertEqual(result["status"], "CONFLICT")
        proof = self.proof(frames[0])
        proof["screen_to_seat"] = {"N": "N", "E": "W", "S": "S", "W": "E"}
        self.assertEqual(accumulate_recovery_frames([frames[0]], evidence=self.evidence([proof]))["status"], "CLOSED_UNKNOWN")

    def test_played_requires_exact_seat_and_time_evidence(self):
        frame = self.frame(1)
        proof = self.proof(frame)
        proof["cards"][0]["kind"] = "PLAYED"
        result = accumulate_recovery_frames([frame], evidence=self.evidence([proof]))
        self.assertEqual(result["scopes"][0]["facts"], [])
        proof["cards"][0].update(played_at_ms=2000, played_by="N", seat_time_ref="reviewed-play")
        result = accumulate_recovery_frames([frame], evidence=self.evidence([proof]))
        self.assertEqual(result["scopes"][0]["recovery"]["deal"]["hands"]["N"]["observed_cards"], ["AS"])
        for malformed in ([], {}, True, 42, ""):
            bad = deepcopy(proof)
            bad["cards"][0]["trick_id"] = malformed
            scope = accumulate_recovery_frames([frame], evidence=self.evidence([bad]))["scopes"][0]
            self.assertEqual(scope["facts"], [])
            self.assertIn("PLAYED_TRICK_ID_INVALID", [r.get("reason") for r in scope["review_observations"]])

    def test_unknown_frame_closes_prior_scope_and_no_absence_deduction(self):
        frames = [self.frame(0), self.frame(1, ()), self.frame(2, (("KS", "N"),))]
        proofs = [self.proof(frames[0]), self.proof(frames[2], frames[1])]
        result = accumulate_recovery_frames(frames, evidence=self.evidence(proofs))
        self.assertEqual([s["status"] for s in result["scopes"]], ["VERIFIED_PARTIAL", "CLOSED_UNKNOWN", "VERIFIED_PARTIAL"])
        self.assertEqual(result["scopes"][2]["recovery"]["observed"].keys(), {"KS"})

    def test_source_tamper_and_malformed_are_rejected(self):
        frame = self.frame()
        self.source.write_bytes(b"changed")
        with self.assertRaisesRegex(RecoveryInputError, "SOURCE_BYTES_MISMATCH"):
            self.evidence([self.proof(frame)])
        for frames in [None, [None], [{}], [dict(frame, observation_index=True)],
                       [dict(frame, observations=[{}] * 53)]]:
            with self.assertRaises(RecoveryInputError):
                accumulate_recovery_frames(frames)

    def test_review_boolean_float_aliases_do_not_bind(self):
        frame = self.frame()
        for field, value in (("board_epoch", True), ("board_number", True), ("timestamp_ms", False),
                             ("raw_frame_size", {"width": 100.0, "height": 100})):
            proof = dict(self.proof(frame), **{field: value})
            result = accumulate_recovery_frames([frame], evidence=self.evidence([proof]))
            self.assertEqual(result["status"], "CLOSED_UNKNOWN", field)

    def test_played_then_in_hand_is_conflict(self):
        frames = [self.frame(1), self.frame(2)]
        proofs = [self.proof(frames[0]), self.proof(frames[1], frames[0])]
        proofs[0]["cards"][0].update(kind="PLAYED", played_by="N", played_at_ms=2000, seat_time_ref="review")
        self.assertEqual(accumulate_recovery_frames(frames, evidence=self.evidence(proofs))["status"], "CONFLICT")

    def test_existing_teacher_extractor_enters_same_temporal_solver_with_distinct_provenance(self):
        frame = self.frame(1, ())
        candidate = extract_teacher_card_declarations([{
            "start": 2.25, "end": 2.75, "text": "У Запада туз пик", "speaker": "SPEAKER_A",
            "speaker_role_candidate": "teacher", "speaker_role_confidence": .99,
            "unreliable": False}])["declarations"][0]
        frame["teacher_declarations"] = [candidate]
        proof = self.proof(frame)
        proof.update(deal_start_ms=1000, deal_end_ms=5000)
        proof["speech_reviews"] = [{**{k: frame[k] for k in ("source_video_sha256", "episode_id", "board_epoch", "board_number")},
                                   "declaration_sha256": declaration_digest(candidate), "modality": "ACTUAL_HOLDING",
                                   "audio_review_ref": "reviewed-audio", "speaker_review_ref": "reviewed-speaker",
                                   "evidence_ref": "teacher-AS-W"}]
        result = accumulate_recovery_frames([frame], evidence=self.evidence([proof]))
        hand = result["scopes"][0]["recovery"]["deal"]["hands"]["W"]
        self.assertEqual(hand["declared_cards"], ["AS"])
        self.assertEqual(hand["observed_cards"], [])
        proof["speech_reviews"][0]["board_epoch"] = 2
        result = accumulate_recovery_frames([frame], evidence=self.evidence([proof]))
        self.assertEqual(result["scopes"][0]["facts"], [])

    def test_selected_r265_wrapper_runs_existing_transcript_then_reviewed_recovery(self):
        from bridge_vision import bridgit_primary_production_r265 as production
        cards = [(c, SEATS[i // 13]) for i, c in enumerate(sorted(FULL_DECK)[:39])]
        frame = self.frame(0, cards)
        segments = [{"start": 0.0, "end": 1.0, "text": "У Запада туз пик", "speaker": "SPEAKER_A",
                     "speaker_role_candidate": "teacher", "speaker_role_confidence": .95}]
        calls = []
        def obtain(*args):
            calls.append("existing_transcriber")
            return segments, {}, []
        def derive(*args):
            calls.append("existing_visual")
            return [{"hands": {}, "recognizer": {}, "evidence": ["shot-1"]}], []
        def provider(deal, extraction, target):
            calls.append("reviewed_recovery")
            self.assertEqual(extraction["declarations"][0]["card"], "AS")
            proof = dict(self.proof(frame), output_deal_sha256=target["target_sha256"], output_deal_binding_ref="synthetic-target-review")
            return ReviewedRecoveryBatch(("shot-1",), [frame], self.evidence([proof]), target["target_sha256"])
        base = SimpleNamespace(PROMPT="", obtain_transcript=obtain, derive_deals_decisions=derive,
                               master_analysis_payload=lambda: {})
        with patch.object(production, "_INSTALLED_BASE_IDS", set()):
            production.install(base, lambda: "unused", recovery_provider=provider)
            base.obtain_transcript(None, None, None, self.source, None, None, None)
            deals, _ = base.derive_deals_decisions([], {})
            self.assertEqual(calls, ["existing_transcriber", "existing_visual", "reviewed_recovery"])
            self.assertEqual(deals[0]["verified_recovery"]["status"], "VERIFIED_FULL")
            self.assertEqual(len(deals[0]["verified_recovery"]["inferred"]), 13)
            self.assertEqual(deals[0]["hands"], {})
            with self.assertRaisesRegex(ValueError, "RECONFIGURATION"):
                production.install(base, lambda: "unused")
        base = SimpleNamespace(PROMPT="", obtain_transcript=obtain, derive_deals_decisions=derive,
                               master_analysis_payload=lambda: {})
        with patch.object(production, "_INSTALLED_BASE_IDS", set()):
            production.install(base, lambda: "unused")
            deals, _ = base.derive_deals_decisions([], {})
            self.assertEqual(deals[0]["temporal_recovery"]["status"], "CLOSED_UNKNOWN")
            self.assertIsNone(deals[0]["verified_recovery"])

    def test_runner_target_binding_and_visual_precedence_fail_closed(self):
        from bridge_vision import bridgit_primary_production_r265 as production
        cards = [(c, SEATS[i // 13]) for i, c in enumerate(sorted(FULL_DECK)[:39])]
        frame = self.frame(0, cards)
        first_card = cards[0][0]
        for change in ("target_sha", "source_sha", "missing_target_review", "visual_conflict"):
            raw_hands = {"E": [first_card]} if change == "visual_conflict" else {}
            base = SimpleNamespace(PROMPT="", obtain_transcript=lambda *a: ([], {}, []),
                derive_deals_decisions=lambda *a: ([{"hands": deepcopy(raw_hands), "recognizer": {}, "evidence": ["shot-1"]}], []),
                master_analysis_payload=lambda: {})
            def provider(deal, extraction, target):
                proof = dict(self.proof(frame), output_deal_sha256=target["target_sha256"], output_deal_binding_ref="review")
                if change == "missing_target_review":
                    proof.pop("output_deal_binding_ref")
                evidence = self.evidence([proof])
                if change == "source_sha":
                    evidence._source_sha = "b" * 64  # adversarial caller dependency
                return ReviewedRecoveryBatch(("shot-1",), [frame], evidence,
                    "a" * 64 if change == "target_sha" else target["target_sha256"])
            production.install(base, lambda: "unused", recovery_provider=provider)
            base.obtain_transcript(None, None, None, self.source, None, None, None)
            deals, _ = base.derive_deals_decisions([], "job-1")
            self.assertIsNone(deals[0]["verified_recovery"], change)
            self.assertEqual(deals[0]["hands"], raw_hands)
            self.assertEqual(deals[0]["temporal_recovery"]["status"], "CONFLICT" if change == "visual_conflict" else "CLOSED_UNKNOWN")

    def rank_case(self, ranks):
        frame = self.frame(cards=tuple((rank + "S", "N") for rank in ranks if rank is not None))
        proof = self.proof(frame)
        ordinal = 0
        slots = []
        for index, rank in enumerate(ranks):
            slot = {"slot_id": str(index), "physical_slot_ref": "independent-slot-review",
                    "suit": "S", "box": {"x": index * 6, "y": 0, "w": 5, "h": 8},
                    "status": "UNKNOWN" if rank is None else "ANCHOR"}
            if rank:
                slot.update(card=rank + "S", observation_index=ordinal)
                ordinal += 1
            slots.append(slot)
        proof["rank_rows"] = [{"row_id": "spades-N", "screen_seat": "N", "suit": "S", "direction": "DESCENDING",
            "visible_complete_interval": True, "layout_ref": "layout-review", "scale_ref": "scale-review",
            "sort_direction_ref": "order-review", "same_suit_ref": "suit-review", "no_occlusion_ref": "occlusion-review", "slots": slots}]
        return frame, proof

    def test_rank_gap_and_boundaries_have_distinct_inferred_provenance(self):
        for ranks, expected, method in ((["A", None, "Q"], ["KS"], "INFERRED_ORDER_GAP"),
                (["Q", None, "T"], ["JS"], "INFERRED_ORDER_GAP"),
                (["9", None, "7"], ["8S"], "INFERRED_ORDER_GAP"),
                (["A", None, None, "J"], ["KS", "QS"], "INFERRED_ORDER_GAP"),
                (["3", None], ["2S"], "INFERRED_ORDER_BOUNDARY"),
                ([None, "K"], ["AS"], "INFERRED_ORDER_BOUNDARY")):
            frame, proof = self.rank_case(ranks)
            result = accumulate_recovery_frames([frame], evidence=self.evidence([proof]))["scopes"][0]["recovery"]
            self.assertEqual(set(result["inferred"]), set(expected), ranks)
            for card in expected:
                self.assertNotIn(card, result["observed"])
                self.assertEqual(result["inferred"][card]["method"], method)
                self.assertEqual(result["inferred"][card]["derivations"][0]["frame_sha256"], frame["frame_sha256"])

    def test_ambiguous_order_keeps_both_and_never_invents_boundary_slot(self):
        frame, proof = self.rank_case(["A", None, "J"])
        scope = accumulate_recovery_frames([frame], evidence=self.evidence([proof]))["scopes"][0]
        self.assertEqual(scope["recovery"]["inferred"], {})
        self.assertEqual(scope["rank_intervals"][0]["possibilities"], [["KS"], ["QS"]])
        for ranks in (["3"], ["K"], ["4", None], [None, "Q"]):
            frame, proof = self.rank_case(ranks)
            result = accumulate_recovery_frames([frame], evidence=self.evidence([proof]))["scopes"][0]["recovery"]
            self.assertEqual(result["inferred"], {}, ranks)

    def test_unproven_order_suit_slots_occlusion_scale_and_conflicting_anchors(self):
        for field in ("direction", "same_suit_ref", "layout_ref", "visible_complete_interval", "no_occlusion_ref", "scale_ref"):
            frame, proof = self.rank_case(["A", None, "Q"])
            proof["rank_rows"][0].pop(field)
            scope = accumulate_recovery_frames([frame], evidence=self.evidence([proof]))["scopes"][0]
            self.assertEqual(scope["recovery"]["inferred"], {}, field)
            self.assertTrue(scope["review_observations"])
        frame, proof = self.rank_case(["A", None, "Q"])
        proof["rank_rows"][0]["slots"][1]["suit"] = "H"
        self.assertEqual(accumulate_recovery_frames([frame], evidence=self.evidence([proof]))["scopes"][0]["recovery"]["inferred"], {})
        for ranks in (["Q", None, "A"], ["A", None, None, "Q"]):
            frame, proof = self.rank_case(ranks)
            self.assertEqual(accumulate_recovery_frames([frame], evidence=self.evidence([proof]))["status"], "CONFLICT")
        frame, proof = self.rank_case(["A", None, "Q"])
        frame["observations"].append({"status": "ACCEPTED_VISUAL", "source": "VISUAL", "card": "KS", "seat": "E"})
        proof["cards"].append({"observation_index": 2, "card": "KS", "screen_seat": "E", "kind": "IN_HAND", "evidence_ref": "other-king"})
        self.assertEqual(accumulate_recovery_frames([frame], evidence=self.evidence([proof]))["status"], "CONFLICT")

    def test_uncalibrated_colour_cursor_and_weights_are_retained_without_exclusions(self):
        frame = self.frame(cards=())
        frame["colour_evidence"] = {"theme": "unknown-or-four-colour", "roi_kind": "background", "suit": "H", "confidence": 1}
        frame["speech_weight"] = 100
        frame["cursor"] = {"seat": "N"}
        result = accumulate_recovery_frames([frame], evidence=self.evidence([self.proof(frame)]))
        self.assertEqual(result["scopes"][0]["recovery"]["inferred"], {})
        self.assertTrue(all(len(v) == 4 for v in result["scopes"][0]["recovery"]["domains"].values()))
        self.assertEqual(result["history"][0]["frame"]["colour_evidence"], frame["colour_evidence"])

    def test_rank_owner_capacity_cascade_reaches_fixed_point_and_replays_identically(self):
        frame, proof = self.rank_case(["A", None, "J"])
        # Initially N has twelve and E/S thirteen. AS-?-JS allows K/Q, but
        # independently observed KS at E forces QS into N. This fills N and
        # forces the entire W complement in the next global solve.
        rest = sorted(FULL_DECK - {"AS", "JS", "KS", "QS"})
        assignments = [("AS", "N"), ("JS", "N")]
        assignments += [(c, "N") for c in rest[:10]]
        assignments += [("KS", "E")] + [(c, "E") for c in rest[10:22]]
        assignments += [(c, "S") for c in rest[22:35]]
        frame["observations"] = [{"status": "ACCEPTED_VISUAL", "source": "VISUAL", "card": c, "seat": s} for c, s in assignments]
        rows = proof["rank_rows"]
        proof = self.proof(frame)
        proof["rank_rows"] = rows
        evidence = self.evidence([proof])
        result = accumulate_recovery_frames([frame], evidence=evidence)
        scope = result["scopes"][0]
        self.assertEqual(scope["recovery"]["status"], "VERIFIED_FULL")
        self.assertEqual([r["solution_status"] for r in scope["propagation_trace"]], ["MULTIPLE", "UNIQUE"])
        self.assertGreaterEqual(scope["propagation_trace"][0]["new_slot_facts"], 1)
        self.assertEqual(scope["propagation_trace"][1]["new_slot_facts"], 0)
        self.assertTrue(scope["propagation_complete"])
        qproof = scope["recovery"]["inferred"]["QS"]
        self.assertEqual(qproof["method"], "INFERRED_ORDER_GAP")
        self.assertNotIn(qproof["premise_evidence_ids"][0], qproof["derivations"][0]["premise_evidence_ids"])
        for card in scope["recovery"]["deal"]["hands"]["W"]["inferred_cards"]:
            derivation = scope["recovery"]["inferred"][card]["derivations"][0]
            self.assertTrue(derivation["global_constraint_probe"])
            self.assertEqual(derivation["rank_constraints_sha256"], scope["recovery"]["rank_constraints_sha256"])
            self.assertTrue(scope["recovery"]["rank_constraints_snapshot"])
        self.assertEqual(result, accumulate_recovery_frames([frame, frame], evidence=evidence))

    def test_rank_rows_cannot_self_support_without_independently_observed_anchors(self):
        frame, proof = self.rank_case(["A", None, "Q"])
        proof["cards"] = []
        result = accumulate_recovery_frames([frame], evidence=self.evidence([proof]))["scopes"][0]
        self.assertEqual(result["recovery"]["inferred"], {})
        self.assertEqual(result["facts"], [])

    def test_speech_frame_must_be_inside_same_proved_board_interval(self):
        frame = self.frame(1, ())
        declaration = extract_teacher_card_declarations([{"start": 2.25, "end": 2.75,
            "text": "West has ace of spades", "speaker_role_candidate": "teacher",
            "speaker_role_confidence": .99}])["declarations"][0]
        frame["teacher_declarations"] = [declaration]
        proof = self.proof(frame)
        proof.update(deal_start_ms=2000, deal_end_ms=2800)
        proof["speech_reviews"] = [{**{k: frame[k] for k in ("source_video_sha256", "episode_id", "board_epoch", "board_number")},
            "declaration_sha256": declaration_digest(declaration), "modality": "ACTUAL_HOLDING",
            "audio_review_ref": "audio", "speaker_review_ref": "speaker", "evidence_ref": "AS-W"}]
        scope = accumulate_recovery_frames([frame], evidence=self.evidence([proof]))["scopes"][0]
        self.assertEqual(scope["facts"], [])

    def test_exact_installed_row_handoff_keeps_domains_and_does_not_promote(self):
        from bridge_vision.temporal_recovery_export import installed_row_sha256, reconstruction_by_row_sha256
        row = {"sequence": 1, "payload": {"title": "Тест", "cards": []}, "receipt": {}}
        key = installed_row_sha256(row)
        self.assertEqual(key, sha256(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                              separators=(",", ":"), allow_nan=False).encode()).hexdigest())
        frame, proof = self.rank_case(["A", None, "Q"])
        proof.update(input_row_sha256=key, input_row_binding_ref="independent-exact-row-review")
        result = accumulate_recovery_frames([frame], evidence=self.evidence([proof]))
        claim = reconstruction_by_row_sha256(row, result)[key]
        self.assertEqual(claim["scope"]["recovery"]["inferred"]["KS"]["method"], "INFERRED_ORDER_GAP")
        self.assertEqual(len(claim["scope"]["recovery"]["domains"]), 52)
        self.assertFalse(claim["native_pbn_qualified"])
        with self.assertRaisesRegex(RecoveryInputError, "EXACT_ROW"):
            reconstruction_by_row_sha256(dict(row, sequence=2), result)

    def test_r3_handoff_preserves_complete_producer_and_reports_unsupported_abi(self):
        from bridge_vision.temporal_recovery_export import installed_row_sha256, reviewed_pbn_handoff
        row = {"sequence": 3, "payload": {"title": "Тест"}, "receipt": {}}
        frame, proof = self.rank_case(["A", None, "Q"])
        proof.update(input_row_sha256=installed_row_sha256(row), input_row_binding_ref="synthetic-row")
        result = accumulate_recovery_frames([frame], evidence=self.evidence([proof]))
        pins = {p: "a" * 64 for p in ("bridge_vision/temporal_deal_recovery.py", "bridge_vision/original_deal_solver.py")}
        handoff = reviewed_pbn_handoff(row, result, producer_pins=pins)
        self.assertEqual(handoff["sidecar"]["temporal_recovery"], result)
        self.assertEqual(handoff["producer_result_sha256"], sha256(json.dumps(result, ensure_ascii=True,
            sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")).hexdigest())
        self.assertIn("R3_UNSUPPORTED_DERIVED_PREMISE:INFERRED_ORDER_GAP", handoff["consumer_r3_limitations"])
        self.assertFalse(handoff["native_pbn_qualified"])
        handoff["sidecar"]["temporal_recovery"]["scopes"].clear()
        self.assertEqual(len(result["scopes"]), 1)
        for bad in ({}, {"bridge_vision/temporal_deal_recovery.py": True}):
            with self.assertRaisesRegex(RecoveryInputError, "PRODUCER_PINS"):
                reviewed_pbn_handoff(row, result, producer_pins=bad)
        malformed = deepcopy(result)
        malformed["scopes"] = [None]
        with self.assertRaisesRegex(RecoveryInputError, "RECEIPTS"):
            reviewed_pbn_handoff(row, malformed, producer_pins=pins)

    def test_joint_rank_capacity_impossible_not_just_marginally_possible(self):
        frame, proof = self.rank_case(["A", None, None, "T"])
        extra = [c for c in sorted(FULL_DECK) if c[1] != "S"][:10]
        for card in extra:
            idx = len(frame["observations"])
            frame["observations"].append({"card": card, "seat": "N", "status": "ACCEPTED_VISUAL", "source": "VISUAL"})
            proof["cards"].append({"observation_index": idx, "card": card, "screen_seat": "N", "kind": "IN_HAND", "evidence_ref": "extra-" + card})
        result = accumulate_recovery_frames([frame], evidence=self.evidence([proof]))
        self.assertEqual(result["status"], "CONFLICT")

    def test_rank_lifecycle_excludes_played_card_and_budget_never_guesses(self):
        from bridge_vision.bridgit_rank_order_evidence import recover_with_rank_intervals
        first = self.frame(0, (("KS", "N"),))
        p1 = self.proof(first)
        p1["cards"][0].update(kind="PLAYED", played_at_ms=0, played_by="N", seat_time_ref="play-proof")
        frame, proof = self.rank_case(["A", None, "Q"])
        frame.update(timestamp_ms=3000, observation_index=1, frame_sha256=sha256(b"later").hexdigest())
        proof.update({k: frame[k] for k in BINDINGS})
        proof.update(continuous_from_event_id=event_id(first), continuity_ref="continuity")
        self.assertEqual(accumulate_recovery_frames([first, frame], evidence=self.evidence([p1, proof]))["status"], "CONFLICT")
        frame, proof = self.rank_case(["A", None, "J"])
        scope = accumulate_recovery_frames([frame], evidence=self.evidence([proof]))["scopes"][0]
        bounded, _, _, _, complete = recover_with_rank_intervals(scope["facts"], scope["rank_intervals"], node_limit=1)
        self.assertFalse(complete)
        self.assertEqual(bounded["status"], "UNRESOLVED_LIMIT")
        self.assertEqual(bounded["inferred"], {})

    def test_calibrated_symbol_colour_conflict_and_unknown_palette_abstention(self):
        frame = self.frame()
        proof = self.proof(frame)
        colour = {"frame_sha256": frame["frame_sha256"], "card_evidence_id": event_id(frame) + ":0",
            "review_status": "VERIFIED", "roi_kind": "SUIT_SYMBOL", "measured_colour": "red",
            "palette": {"red": ["H", "D"], "black": ["S", "C"]},
            "symbol_roi": {"x": 0, "y": 0, "w": 4, "h": 4},
            "calibration_ref": "independent-known-theme", "measurement_ref": "symbol-sample", "slot_binding_ref": "card-symbol-binding"}
        proof["colour_reviews"] = [colour]
        self.assertEqual(accumulate_recovery_frames([frame], evidence=self.evidence([proof]))["status"], "CONFLICT")
        for field in ("calibration_ref", "slot_binding_ref", "palette"):
            bad = deepcopy(proof)
            bad["colour_reviews"][0].pop(field)
            result = accumulate_recovery_frames([frame], evidence=self.evidence([bad]))["scopes"][0]
            self.assertEqual(result["status"], "VERIFIED_PARTIAL")
            self.assertEqual(result["colour_evidence"], [])
        proof["colour_reviews"][0]["palette"] = {"red": ["S"], "blue": ["H"], "green": ["D"], "black": ["C"]}
        self.assertEqual(accumulate_recovery_frames([frame], evidence=self.evidence([proof]))["status"], "SCOPED_OBSERVATIONS")

    def component_slot(self, frame, components, slot_id="unknown-0", box=None):
        from bridge_vision.bridgit_component_evidence import observation_sha256
        frame["rejected_observations"] = [{"reason": "CARD_UNCERTAIN", "factor_diagnostic": {
            "best_card": "KS", "rank_margin": .9, "suit_margin": .01,
            "roi_xyxy": [0, 0, 5, 8], "roi_bgr_sha256": "c" * 64}}]
        return {"unknown_index": 0, "slot_id": slot_id, "box": box or {"x": 0, "y": 0, "w": 5, "h": 8},
            "observation_sha256": observation_sha256(frame["rejected_observations"][0]),
            "physical_slot_ref": "review-slot", "layout_ref": "review-layout", "scale_ref": "review-scale",
            "no_occlusion_ref": "review-occlusion", "components": {
                part: {"domain": domain, "review_ref": "independent-" + part} for part, domain in components.items()}}

    def test_partial_rank_suit_owner_domains_stay_separate_from_top_one(self):
        cases = (({}, 52, "BOTH_UNKNOWN"), ({"rank": ["K"]}, 4, "RANK_KNOWN_SUIT_UNKNOWN"),
                 ({"suit": ["S"]}, 13, "RANK_UNKNOWN_SUIT_KNOWN"),
                 ({"rank": ["K"], "suit": ["S"]}, 1, "BOTH_KNOWN_OWNER_UNKNOWN"))
        for components, count, state in cases:
            frame = self.frame(cards=())
            proof = self.proof(frame)
            proof["component_slots"] = [self.component_slot(frame, components)]
            result = accumulate_recovery_frames([frame], evidence=self.evidence([proof]))
            interval = result["scopes"][0]["rank_intervals"][0]
            self.assertEqual(len(interval["slot_domains"]["unknown-0"]), count)
            self.assertEqual(interval["component_evidence"]["state"], state)
            self.assertIsNone(interval["component_evidence"]["rank"]["confidence"])
            self.assertEqual(interval["component_evidence"]["rank"]["diagnostic_margin"], .9)
            self.assertEqual(result["scopes"][0]["recovery"]["inferred"], {})

    def test_components_intersect_order_and_reject_cross_component_conflict(self):
        for suit, expected in (("S", "SCOPED_OBSERVATIONS"), ("H", "CONFLICT")):
            frame, proof = self.rank_case(["A", None, "J"])
            proof["component_slots"] = [self.component_slot(frame, {"rank": ["K"], "suit": [suit]},
                slot_id="1", box={"x": 6, "y": 0, "w": 5, "h": 8})]
            result = accumulate_recovery_frames([frame], evidence=self.evidence([proof]))
            self.assertEqual(result["status"], expected)
            if expected != "CONFLICT":
                self.assertEqual(result["scopes"][0]["recovery"]["inferred"]["KS"]["seat"], "N")

    def test_component_fixed_point_uses_deck_without_dropping_known_rank(self):
        non_kings = [c for c in sorted(FULL_DECK) if c[0] != "K"]
        n_cards = non_kings[:12]
        others = sorted(FULL_DECK - set(n_cards) - {"KS"})
        cards = [(c, "N") for c in n_cards] + [(c, SEATS[1 + i // 13]) for i, c in enumerate(others)]
        frame = self.frame(cards=cards)
        proof = self.proof(frame)
        proof["component_slots"] = [self.component_slot(frame, {"rank": ["K"], "owner": ["N"]})]
        result = accumulate_recovery_frames([frame], evidence=self.evidence([proof]))["scopes"][0]
        view = result["rank_intervals"][0]["component_evidence"]
        self.assertEqual(view["rank"]["domain"], ["K"])
        self.assertEqual(view["suit"]["domain"], ["S"])
        self.assertEqual(view["owner"]["domain"], ["N"])
        self.assertEqual(view["state"], "CARD_AND_OWNER_KNOWN")

    def test_physical_box_alias_cannot_turn_one_slot_into_two_cards(self):
        frame, proof = self.rank_case(["A", None, "J"])
        duplicate = deepcopy(proof["rank_rows"][0])
        duplicate["row_id"] = "alias"
        for slot in duplicate["slots"]:
            slot["slot_id"] = "alias-" + slot["slot_id"]
        proof["rank_rows"].append(duplicate)
        result = accumulate_recovery_frames([frame], evidence=self.evidence([proof]))
        self.assertEqual(result["status"], "CONFLICT")
        self.assertEqual(result["scopes"][0]["recovery"]["inferred"], {})

    def zone(self, zone_type, mapping=None):
        mapping = mapping or dict(zip(("p0", "p1", "p2", "p3"), SEATS))
        return {"zone_id": zone_type, "zone_type": zone_type, "layout_ref": "reviewed-Gambler-layout",
            "calibration_ref": "reviewed-zone-calibration", "orientation_ref": "reviewed-zone-orientation",
            "orientation_id": "rotation-1", "slot_to_seat": mapping,
            "slot_boxes": {p: {"x": i * 10, "y": 20, "w": 8, "h": 8} for i, p in enumerate(mapping)}}

    def zone_card(self, proof, ordinal, zone, slot, owner):
        proof.setdefault("deal_start_ms", 0)
        proof.setdefault("deal_end_ms", 10000)
        proof["cards"][ordinal].update(kind="PLAYED", played_at_ms=2000, played_by=owner,
            seat_time_ref="reviewed-play-time", trick_id="trick-7", zone_id=zone["zone_id"],
            zone_type=zone["zone_type"], zone_slot=slot, zone_slot_box=zone["slot_boxes"][slot])

    def test_played_zone_repeat_transition_rotation_and_unknown_mapping(self):
        frames = [self.frame(i) for i in (1, 2, 3)]
        proofs = [self.proof(f, frames[i-1] if i else None) for i, f in enumerate(frames)]
        for i, proof in enumerate(proofs):
            zone = self.zone("current_trick" if i == 0 else "previous_trick")
            proof["played_zones"] = [zone]
            self.zone_card(proof, 0, zone, "p0", "N")
        scope = accumulate_recovery_frames(frames, evidence=self.evidence(proofs))["scopes"][0]
        self.assertEqual(len(scope["played_events"]), 1)
        event = next(iter(scope["played_events"].values()))
        self.assertEqual(len(event["display_evidence_ids"]), 3)
        self.assertEqual(event["initial_owner"], "N")
        rotated = self.zone("played_in_front_of_hand", dict(zip(("p0", "p1", "p2", "p3"), ("E", "S", "W", "N"))))
        proof = self.proof(frames[0])
        proof["played_zones"] = [rotated]
        self.zone_card(proof, 0, rotated, "p3", "N")
        self.assertEqual(accumulate_recovery_frames([frames[0]], evidence=self.evidence([proof]))["scopes"][0]["facts"][0]["initial_owner"], "N")
        proof["played_zones"][0].pop("calibration_ref")
        self.assertEqual(accumulate_recovery_frames([frames[0]], evidence=self.evidence([proof]))["scopes"][0]["facts"], [])

    def test_multiple_visible_play_instances_and_board_reset(self):
        frame = self.frame(1, (("AS", "N"), ("AS", "S")))
        proof = self.proof(frame)
        zones = [self.zone("current_trick"), self.zone("previous_trick")]
        proof["played_zones"] = zones
        for i, zone in enumerate(zones):
            self.zone_card(proof, i, zone, "p0", "N")
        frame["detector_conflicts"] = [{"reason": "SAME_CARD_DIFFERENT_SEATS", "card": "AS", "seats": ["N", "S"]}]
        scope = accumulate_recovery_frames([frame], evidence=self.evidence([proof]))["scopes"][0]
        self.assertEqual(scope["status"], "VERIFIED_PARTIAL")
        self.assertEqual(len(scope["played_events"]), 1)
        later = deepcopy(frame)
        later.update(observation_index=2, timestamp_ms=6000, board_epoch=2)
        later_proof = deepcopy(proof)
        later_proof.update({k: later[k] for k in BINDINGS})
        later_proof["deal_start_ms"] = 5000
        for c in later_proof["cards"]:
            c["played_at_ms"] = 5500
        scopes = accumulate_recovery_frames([frame, later], evidence=self.evidence([proof, later_proof]))["scopes"]
        self.assertEqual(len(scopes), 2)
        self.assertNotEqual(set(scopes[0]["played_events"]), set(scopes[1]["played_events"]))

    def test_trick_winner_does_not_own_all_four_played_cards(self):
        frame = self.frame(1, tuple((c, "W") for c in ("AS", "KS", "QS", "JS")))
        proof = self.proof(frame)
        zone = self.zone("previous_trick")
        zone["winner"] = "W"
        proof["played_zones"] = [zone]
        for i, seat in enumerate(SEATS):
            self.zone_card(proof, i, zone, "p" + str(i), seat)
        scope = accumulate_recovery_frames([frame], evidence=self.evidence([proof]))["scopes"][0]
        self.assertEqual([f["initial_owner"] for f in scope["facts"]], list(SEATS))
        self.assertTrue(all(f["membership"] == "PLAYED" for f in scope["facts"]))

    def test_played_zones_require_same_board_interval_and_one_card_per_seat_per_trick(self):
        frames = [self.frame(1), self.frame(2, (("KS", "N"),))]
        proofs = [self.proof(frames[0]), self.proof(frames[1], frames[0])]
        for i, proof in enumerate(proofs):
            zone = self.zone("current_trick" if i == 0 else "previous_trick")
            proof["played_zones"] = [zone]
            self.zone_card(proof, 0, zone, "p0", "N")
            proof["cards"][0]["played_at_ms"] = 2000 if i == 0 else 4000
        self.assertEqual(accumulate_recovery_frames(frames, evidence=self.evidence(proofs))["status"], "CONFLICT")
        proof = deepcopy(proofs[1])
        proof["deal_start_ms"] = 5000
        scope = accumulate_recovery_frames([frames[1]], evidence=self.evidence([proof]))["scopes"][0]
        self.assertEqual(scope["facts"], [])
        self.assertEqual(scope["review_observations"][0]["reason"], "PLAYED_BOARD_INTERVAL_UNPROVEN")

    def test_saved_real_diagnostics_abstain_without_human_labels(self):
        base = Path(__file__).resolve().parents[1] / "evidence"
        for name in ("BRIDGIT_PARTIAL_PIXEL_SINGLE_FRAME_SMOKE_20261015.json",
                     "BRIDGIT_PARTIAL_AUTO_GEOMETRY_SMOKE_20261015.json"):
            raw = json.loads((base / name).read_text(encoding="utf-8"))
            # No extraction of answers/labels into proofs. Replay the saved raw
            # artifact as unreviewed evidence with only a transport envelope.
            frame = self.frame(cards=())
            frame.update(data_class="REAL_SHADOW", saved_diagnostic=raw)
            result = accumulate_recovery_frames([frame])
            self.assertEqual(result["status"], "CLOSED_UNKNOWN")
            self.assertIsNone(result["scopes"][0]["recovery"])
            self.assertEqual(result["history"][0]["frame"]["saved_diagnostic"], raw)

    def test_native_conflict_retention_and_snapshot_callsite(self):
        frame = self.frame(cards=(("AS", "N"), ("AS", "S")))
        path = self.root / "frame.png"
        path.write_bytes(b"synthetic pixels")
        frame["frame_sha256"] = sha256(path.read_bytes()).hexdigest()
        frame["pixel_observer_version"] = None
        proof = self.proof(frame)
        payload = {"table_region": {"x": 0, "y": 0, "w": 100, "h": 100}, "cards": [
            {"card": "AS", "confidence": 1, "box": {"x": 45, "y": y, "w": 10, "h": 10}} for y in (5, 85)]}
        detector = NativeFourSeatCardDetector(lambda _: payload, retain_conflicts=True)
        sample = dict(frame, frame_path=path)
        sample.pop("observations")
        result = accumulate_detected_frames([sample], detector=detector,
            contract=SamplingContract(PROFILE.version, 1000, 3000, PROFILE.max_gap_ms, .8),
            recovery_evidence=self.evidence([proof]))
        self.assertEqual(result["recovery"]["status"], "CONFLICT")
        self.assertEqual(result["tracks"][0]["status"], "REVIEW_CONFLICT")


if __name__ == "__main__":
    unittest.main()
