import unittest

from tools.diana167_server_report import complete_unrecognized_cards


RANKS = "AKQJT98765432"
SUITS = "HCDS"
SEATS = "NESW"


def _deal(weight: float = 0.90):
    hands = {seat: {suit: [] for suit in SUITS} for seat in SEATS}
    weights = []
    for seat, suit in zip(SEATS, SUITS):
        hands[seat][suit] = list(RANKS)
        for rank in RANKS:
            weights.append({"seat": seat, "card": rank + suit, "weight_median": weight})
    return {"hands": hands, "weights": weights, "status": "VISUAL"}


class Diana167CompletionTest(unittest.TestCase):
    def test_completes_low_cards_and_confirms_voids(self):
        deal = _deal()
        target = next(row for row in deal["weights"] if row["card"] == "2H")
        target["weight_median"] = 0.40
        data = {"deals": [deal]}

        result = complete_unrecognized_cards(data)

        self.assertEqual(result["total_deck_constrained_completed_cards"], 1)
        self.assertEqual(target["visual_weight"], 0.40)
        self.assertEqual(target["fused_weight"], 0.40)
        self.assertEqual(target["identification_source"], "DECK_COMPLETION")
        self.assertEqual(deal["suit_states"]["N"]["C"]["status"], "VOID_CONFIRMED")
        self.assertEqual(deal["completion"]["hand_counts"], {seat: 13 for seat in SEATS})

    def test_pointer_and_play_memory_are_positive_only(self):
        deal = _deal(0.50)
        data = {
            "deals": [deal],
            "teacher_pointer_events": [
                {
                    "deal": 1,
                    "claimed_card": "2H",
                    "claimed_seat": "N",
                    "confidence": 0.80,
                    "teacher_role_verified": True,
                    "spatial_overlap": True,
                },
                {
                    "deal": 1,
                    "claimed_card": "3H",
                    "claimed_seat": "E",
                    "confidence": 0.99,
                    "teacher_role_verified": True,
                    "spatial_overlap": True,
                },
            ],
            "played_card_memory": [
                {"deal": 1, "card": "4H", "seat": "N", "confidence": 0.75, "evidence_verified": True}
            ],
        }

        result = complete_unrecognized_cards(data)
        by_card = {row["card"]: row for row in deal["weights"]}

        self.assertEqual(by_card["2H"]["fused_weight"], 0.90)
        self.assertIn("TEACHER_POINTER", by_card["2H"]["provenance"])
        self.assertEqual(by_card["4H"]["fused_weight"], 0.875)
        self.assertIn("PLAY_MEMORY", by_card["4H"]["provenance"])
        self.assertEqual(by_card["3H"]["fused_weight"], 0.50)
        self.assertEqual(result["conflicts"][0]["kind"], "CURSOR_CONFLICT")

    def test_rejects_non_bijective_deck(self):
        deal = _deal()
        deal["weights"][0]["card"] = deal["weights"][1]["card"]
        with self.assertRaisesRegex(RuntimeError, "invalid deck assignment"):
            complete_unrecognized_cards({"deals": [deal]})


if __name__ == "__main__":
    unittest.main()
