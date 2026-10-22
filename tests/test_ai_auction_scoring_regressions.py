"""Stdlib regressions for scoring boundaries; no BEN/DDS process is launched."""
from __future__ import annotations

import hashlib
import unittest

from bridge_school_api.ai_auction_rollout import (
    AuctionRolloutError,
    ben_request_sha256,
    rollout_worlds,
)
from bridge_school_api.ai_auction_scoring import (
    AuctionScoringError,
    normalize_vulnerability,
    score_duplicate_contract,
    score_rollout_with_dds3,
)
from bridge_school_api.dds3.service import (
    DDS_UPSTREAM,
    canonical_dds3_table_request,
    dds3_table_request_sha256,
)


PBN = "N:AKQJT98765432... .AKQJT98765432.. ..AKQJT98765432. ...AKQJT98765432"
DEAL_SHA256 = hashlib.sha256(PBN.encode("utf-8")).hexdigest()
FINGERPRINT = "a" * 64
UNKNOWN_VULNERABILITIES = (None, False, 0, 0.0, [], {}, True, 1, 1.5)


def _rollout(*, passed_out=False, vulnerability="NONE", dealer="N"):
    return {
        "engine": "BEN", "fallback_used": False,
        "evidence_class": "BEN_AUCTION_ROLLOUT",
        "candidate_call": "PASS" if passed_out else "4S",
        "dealer": dealer, "vulnerability": vulnerability,
        "requested_worlds": 1, "completed_worlds": 1,
        "complete": True, "dds_evaluated": False,
        "worlds": [{
            "world_index": 0, "world_fingerprint": FINGERPRINT,
            "deal_pbn_sha256": DEAL_SHA256,
            "auction": ["PASS"] * 4 if passed_out else ["4S", "PASS", "PASS", "PASS"],
            "contract": None if passed_out else "4S",
            "declarer": None if passed_out else "N", "passed_out": passed_out,
        }],
    }


def _dds_envelope(*, dealer="N"):
    request = canonical_dds3_table_request(pbn=PBN, dealer=dealer, vulnerability="None")
    return {
        "world_fingerprint": FINGERPRINT, "request": request,
        "result": {
            "engine": "DDS3", "engine_version": DDS_UPSTREAM, "operation": "dd_table",
            "input_validated": True, "fallback_used": False,
            "hand_order": ["N", "E", "S", "W"],
            "strain_order": ["S", "H", "D", "C", "NT"],
            "deal_pbn_sha256": DEAL_SHA256,
            "request_sha256": dds3_table_request_sha256(request),
            "dd_table": {
                "S": [10, 3, 10, 3], "H": [9, 4, 9, 4], "D": [8, 5, 8, 5],
                "C": [7, 6, 7, 6], "NT": [9, 4, 9, 4],
            },
        },
    }


class AuctionScoringBoundaryRegressions(unittest.TestCase):
    def test_unknown_vulnerability_is_not_none(self):
        for value in UNKNOWN_VULNERABILITIES:
            with self.subTest(value=value):
                with self.assertRaisesRegex(AuctionScoringError, "vulnerability"):
                    normalize_vulnerability(value)

    def test_unknown_vulnerability_cannot_produce_a_duplicate_score(self):
        for value in UNKNOWN_VULNERABILITIES:
            with self.subTest(value=value):
                with self.assertRaisesRegex(AuctionScoringError, "vulnerability"):
                    score_duplicate_contract(contract="4S", declarer="N", tricks=10,
                                             vulnerability=value)

    def test_known_vulnerability_aliases_and_legacy_empty_string(self):
        for value, expected in (("", "NONE"), ("none", "NONE"), ("love", "NONE"),
                                ("n-s", "NS"), ("e_w", "EW"), ("all", "BOTH")):
            with self.subTest(value=value):
                self.assertEqual(normalize_vulnerability(value), expected)
        for value in ("-", "_", "unknown", "dealer"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(AuctionScoringError, "vulnerability"):
                    normalize_vulnerability(value)

    def test_unknown_rollout_vulnerability_is_rejected_on_both_boundaries(self):
        for value in UNKNOWN_VULNERABILITIES:
            for scoring_value in (value, "NONE"):
                with self.subTest(source_value=value, scoring_value=scoring_value):
                    with self.assertRaisesRegex(AuctionScoringError, "vulnerability"):
                        score_rollout_with_dds3(
                            rollout=_rollout(vulnerability=value),
                            dds3_results={FINGERPRINT: _dds_envelope()},
                            vulnerability=scoring_value,
                        )

    def test_missing_rollout_vulnerability_is_rejected(self):
        rollout = _rollout()
        del rollout["vulnerability"]
        with self.assertRaisesRegex(AuctionScoringError, "vulnerability"):
            score_rollout_with_dds3(rollout=rollout,
                                    dds3_results={FINGERPRINT: _dds_envelope()},
                                    vulnerability="NONE")

    def test_old_contradictory_passed_out_fixture_is_rejected(self):
        rollout = _rollout(passed_out=True)
        rollout["worlds"][0]["auction"] = ["4S", "PASS", "PASS", "PASS"]
        with self.assertRaisesRegex(AuctionScoringError, "auction"):
            score_rollout_with_dds3(rollout=rollout, dds3_results={}, vulnerability="NONE")

    def test_claimed_contract_declarer_and_completion_must_match_auction(self):
        cases = (
            {"contract": "3N"}, {"contract": "4SX"}, {"contract": "4SXX"},
            {"declarer": "S"}, {"declarer": None}, {"contract": None},
            {"auction": ["PASS"] * 4}, {"auction": ["4S", "PASS"]},
            {"auction": ["4S", "4H", "PASS", "PASS", "PASS"]},
            {"auction": ["4S", "PASS", "PASS", "PASS", "PASS"]},
            {"auction": None}, {"auction": "4S PASS PASS PASS"},
            {"auction": []}, {"auction": [4, "PASS", "PASS", "PASS"]},
            {"passed_out": 0}, {"passed_out": None},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                rollout = _rollout()
                rollout["worlds"][0].update(overrides)
                with self.assertRaisesRegex(AuctionScoringError, "auction"):
                    score_rollout_with_dds3(rollout=rollout,
                                            dds3_results={FINGERPRINT: _dds_envelope()},
                                            vulnerability="NONE")

    def test_passed_out_flag_requires_a_boolean(self):
        for flag in (1, "true"):
            with self.subTest(flag=flag):
                rollout = _rollout(passed_out=True)
                rollout["worlds"][0]["passed_out"] = flag
                with self.assertRaisesRegex(AuctionScoringError, "auction"):
                    score_rollout_with_dds3(rollout=rollout, dds3_results={}, vulnerability="NONE")

    def test_missing_passed_out_flag_is_rejected(self):
        rollout = _rollout()
        del rollout["worlds"][0]["passed_out"]
        with self.assertRaisesRegex(AuctionScoringError, "auction"):
            score_rollout_with_dds3(rollout=rollout,
                                    dds3_results={FINGERPRINT: _dds_envelope()},
                                    vulnerability="NONE")

    def test_dealer_required_and_consistent_with_declarer(self):
        for dealer in (None, False, 0, "", "invalid", "S"):
            with self.subTest(dealer=dealer):
                with self.assertRaisesRegex(AuctionScoringError, "dealer|auction"):
                    score_rollout_with_dds3(rollout=_rollout(dealer=dealer),
                                            dds3_results={FINGERPRINT: _dds_envelope()},
                                            vulnerability="NONE")
        rollout = _rollout()
        del rollout["dealer"]
        with self.assertRaisesRegex(AuctionScoringError, "dealer"):
            score_rollout_with_dds3(rollout=rollout,
                                    dds3_results={FINGERPRINT: _dds_envelope()},
                                    vulnerability="NONE")

    def test_legal_complete_auctions_doubles_redoubles_and_first_bidder(self):
        cases = (
            ("N", ["4S", "PASS", "PASS", "PASS"], "4S", "N", 420),
            ("N", ["4S", "X", "PASS", "PASS", "PASS"], "4SX", "N", 590),
            ("N", ["4S", "X", "XX", "PASS", "PASS", "PASS"], "4SXX", "N", 880),
            ("E", ["PASS", "1N", "PASS", "3NT", "PASS", "PASS", "PASS"], "3NT", "S", 400),
            ("E", ["3NT", "PASS", "PASS", "PASS"], "3N", "E", 250),
        )
        for dealer, auction, contract, declarer, score_ns in cases:
            with self.subTest(dealer=dealer, auction=auction):
                rollout = _rollout(dealer=dealer)
                rollout["worlds"][0].update(auction=auction, contract=contract, declarer=declarer)
                result = score_rollout_with_dds3(
                    rollout=rollout, dds3_results={FINGERPRINT: _dds_envelope(dealer=dealer)},
                    vulnerability="NONE",
                )
                self.assertEqual(result["worlds"][0]["score_ns"], score_ns)
                self.assertEqual(result["worlds"][0]["declarer"], declarer)

    def test_four_passes_score_zero_without_dds(self):
        result = score_rollout_with_dds3(rollout=_rollout(passed_out=True), dds3_results={},
                                         vulnerability="NONE")
        self.assertEqual(result["worlds"][0]["score_ns"], 0)
        self.assertIs(result["dds_evaluated"], False)
        self.assertEqual(result["dds_required_worlds"], 0)

    def test_rollout_rejects_unknown_vulnerability_before_ben(self):
        world = {"hands": dict(zip(("N", "E", "S", "W"), PBN[2:].split(), strict=True))}
        for value in UNKNOWN_VULNERABILITIES:
            with self.subTest(value=value):
                calls = []

                def bidder(request):
                    calls.append(request)
                    return {"bid": "PASS", "candidates": [{"call": "PASS", "insta_score": 1.0}],
                            "request_sha256": ben_request_sha256(request)}

                with self.assertRaisesRegex(AuctionRolloutError, "vulnerability"):
                    rollout_worlds(worlds=[world], dealer="N", auction=[], decision_seat="N",
                                   candidate_call="1C", ben_bidder=bidder, vulnerability=value)
                self.assertEqual(calls, [])

    def test_generated_rollout_retains_canonical_dealer_for_scoring(self):
        world = {"hands": dict(zip(("N", "E", "S", "W"), PBN[2:].split(), strict=True))}

        def bidder(request):
            return {"bid": "PASS", "candidates": [{"call": "PASS", "insta_score": 1.0}],
                    "request_sha256": ben_request_sha256(request)}

        rollout = rollout_worlds(worlds=[world], dealer="s", auction=[], decision_seat="S",
                                  candidate_call="1N", ben_bidder=bidder)
        self.assertEqual(rollout["dealer"], "S")
        fingerprint = rollout["worlds"][0]["world_fingerprint"]
        envelope = _dds_envelope(dealer="S")
        envelope["world_fingerprint"] = fingerprint
        result = score_rollout_with_dds3(rollout=rollout, dds3_results={fingerprint: envelope},
                                         vulnerability="NONE")
        self.assertEqual(result["worlds"][0]["declarer"], "S")
        self.assertEqual(result["worlds"][0]["score_ns"], 150)


if __name__ == "__main__":
    unittest.main()
