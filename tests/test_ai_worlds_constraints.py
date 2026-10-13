import unittest
from unittest.mock import patch

from bridge_school_api.ai_worlds import WorldGenerationError, generate_worlds


HAND = "AKQJ.T98.765.432"


def generate(constraints):
    return generate_worlds(
        known_seat="N", known_hand_pbn=HAND, constraints=constraints,
        count=2, seed=91, max_attempts=5000,
    )


class NormalizedConstraintKeysTest(unittest.TestCase):
    def test_duplicate_seats_rejected_in_both_orders_even_if_identical(self):
        rules = (
            ({"hcp": [37, 37]}, {"hcp": [0, 37]}),
            ({"hcp": [0, 37]}, {"hcp": [0, 37]}),
            ({"suits": {"H": [13, 13]}}, {}),
        )
        for seat in "NESW":
            for reverse in (False, True):
                for first, second in rules:
                    items = [(seat, first), (seat.lower(), second)]
                    if reverse:
                        items.reverse()
                    with self.subTest(items=items):
                        with self.assertRaisesRegex(WorldGenerationError, "duplicate.*seat"):
                            generate({"seats": dict(items)})

    def test_duplicate_suits_rejected_in_both_orders_even_if_identical(self):
        for seat in "NESW":
            for suit in "SHDC":
                for reverse in (False, True):
                    for first in ([13, 13], [0, 13]):
                        items = [(suit, first), (suit.lower(), [0, 13])]
                        if reverse:
                            items.reverse()
                        with self.subTest(seat=seat, items=items):
                            with self.assertRaisesRegex(WorldGenerationError, "duplicate.*suit"):
                                generate({"seats": {seat: {"suits": dict(items)}}})

    def test_duplicate_keys_are_rejected_before_sampling(self):
        inputs = (
            {"seats": {"S": {}, "s": {}}},
            {"seats": {"S": {"suits": {"H": [0, 13], "h": [0, 13]}}}},
        )
        for constraints in inputs:
            with self.subTest(constraints=constraints):
                with patch("bridge_school_api.ai_worlds.random.Random") as sampler:
                    with self.assertRaises(WorldGenerationError):
                        generate(constraints)
                    sampler.assert_not_called()

    def test_unique_mixed_case_keys_preserve_constraints_and_worlds(self):
        upper = {"seats": {
            "N": {"hcp": [10, 10], "suits": {"S": [4, 4], "H": [3, 3], "D": [3, 3], "C": [3, 3]}},
            "E": {"hcp": [10, 12], "suits": {"S": [4, 6]}},
            "S": {"suits": {"H": [0, 13]}},
        }}
        mixed = {"seats": {
            "n": {"hcp": [10, 10], "suits": {"s": [4, 4], "H": [3, 3], "d": [3, 3], "C": [3, 3]}},
            "E": {"hcp": [10, 12], "suits": {"s": [4, 6]}},
            "s": {"suits": {"h": [0, 13]}},
        }}
        expected, actual = generate(upper), generate(mixed)
        self.assertTrue(actual["complete"])
        self.assertEqual(expected["worlds"], actual["worlds"])
        self.assertEqual(expected["attempts"], actual["attempts"])
        for world in actual["worlds"]:
            self.assertEqual(world["hands"]["N"], HAND)
            east = world["hands"]["E"]
            hcp = sum({"A": 4, "K": 3, "Q": 2, "J": 1}.get(rank, 0) for rank in east)
            self.assertTrue(10 <= hcp <= 12)
            self.assertTrue(4 <= len(east.split(".")[0]) <= 6)


if __name__ == "__main__":
    unittest.main()
