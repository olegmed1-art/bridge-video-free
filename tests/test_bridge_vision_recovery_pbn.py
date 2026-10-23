"""Pinned synthetic producer result; no human labels or video inference."""
import copy
import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

from bridge_vision.pbn import export_frame_pbns, observation_record_sha256
from bridge_vision.recovery_pbn import load_reviewed_recovery

FIXTURE_SHA = "ed06f13c7c927417383db10dc5da13e33d84667bb1f0288664a19c3f58d3f7c0"
PRODUCER_RESULT_SHA = "0101827157dae60bde91dd99e3a355591a048148857ffd7f801b18eb3e1467f2"


def encode(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class ReviewedOwnershipTests(unittest.TestCase):
    def setUp(self):
        raw = (Path(__file__).parent / "fixtures/recognized_cards_recovery_synthetic.json").read_bytes()
        self.assertEqual(sha(raw), FIXTURE_SHA)
        self.fixture = json.loads(raw)
        self.assertIs(self.fixture["synthetic_only"], True)
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def admit(self, envelope=None, *, result_sha=PRODUCER_RESULT_SHA):
        fixture = self.fixture
        envelope = envelope if envelope is not None else fixture["envelope"]
        sidecar = self.root / "sidecar.json"
        review = self.root / "review.json"
        sidecar.write_bytes(encode(envelope))
        review.write_bytes(encode(fixture["review_manifest"]))
        return load_reviewed_recovery(sidecar, expected_sha256=sha(sidecar.read_bytes()),
            expected_temporal_sha256=result_sha, review_manifest_path=review,
            expected_review_manifest_sha256=sha(review.read_bytes()),
            expected_producer_pins=fixture["envelope"]["producer_pins"], target_binding=fixture["binding"])

    def test_full_roundtrip_keeps_conditional_observed_and_inferred(self):
        record = self.fixture["record"]
        out = export_frame_pbns(self.root, [record], source_metadata={"title": "SYNTHETIC TITLE"},
            qualified_by_record_sha256={observation_record_sha256(record): self.admit()})[0]
        data = (self.root / out["path"]).read_bytes()
        # Independent decoding of the standard Deal tag, not writer helpers.
        deal = re.search(rb'\[Deal "([^"]+)"\]', data).group(1).decode("ascii")
        first, hands = deal.split(":")
        self.assertEqual(first, "N")
        decoded = [{rank + suit for suit, ranks in zip("SHDC", hand.split(".")) for rank in ranks}
                   for hand in hands.split()]
        self.assertEqual([len(hand) for hand in decoded], [13] * 4)
        self.assertEqual(len(set.union(*decoded)), 52)
        self.assertEqual(decoded[3], {rank + "C" for rank in "AKQJT98765432"})
        self.assertEqual((out["observed_card_count"], out["inferred_card_count"]), (39, 13))
        self.assertIn(b'"inferred_cards"', data)
        self.assertIn(b"CONDITIONAL_ON_REVIEWED_PREMISES", data)
        self.assertIs(out["canonical_promotion_allowed"], False)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dds_training"))
        try:
            from dds_training.corpus import validate_pbn_corpus
            parser_copy = self.root / "synthetic-independent-parser.pbn"
            parser_copy.write_bytes(b'[DealID "SYNTHETIC-QUALIFIED"]\r\n' + data)
            self.assertTrue(validate_pbn_corpus(parser_copy, expected_count=1)["ok"])
        finally:
            sys.path.pop(0)

    def test_consistent_owner_swap_cannot_repin_sidecar_or_override_review(self):
        envelope = copy.deepcopy(self.fixture["envelope"])
        recovery = envelope["temporal_recovery"]["scopes"][0]["recovery"]
        for card, old, new in (("AS", "N", "E"), ("AH", "E", "N")):
            recovery["observed"][card][new] = recovery["observed"][card].pop(old)
            recovery["domains"][card] = [new]
            for key in ("cards", "observed_cards"):
                recovery["deal"]["hands"][old][key].remove(card)
                recovery["deal"]["hands"][new][key].append(card)
        with self.assertRaisesRegex(ValueError, "PINNED_PRODUCER_RESULT"):
            self.admit(envelope)
        with self.assertRaisesRegex(ValueError, "OBSERVED_REVIEW_OWNER"):
            self.admit(envelope, result_sha=sha(encode(envelope["temporal_recovery"])))

    def test_inferred_mutation_cannot_replace_pinned_result(self):
        envelope = copy.deepcopy(self.fixture["envelope"])
        envelope["temporal_recovery"]["scopes"][0]["recovery"]["inferred"]["AC"]["method"] = "UNREVIEWED"
        with self.assertRaisesRegex(ValueError, "PINNED_PRODUCER_RESULT"):
            self.admit(envelope)

    def test_raw_claim_cannot_authorize_native_cards(self):
        record = self.fixture["record"]
        with self.assertRaises(ValueError):
            export_frame_pbns(self.root, [record], qualified_by_record_sha256={
                observation_record_sha256(record): self.fixture["envelope"]})
        self.assertFalse(list(self.root.rglob("*.pbn")))


if __name__ == "__main__":
    unittest.main()
