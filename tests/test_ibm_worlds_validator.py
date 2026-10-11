"""Focused genuine and adversarial fixtures; executable with stdlib unittest."""
from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
import unittest

from bridge_school_api.ai_worlds import generate_worlds
from ops import ibm_worlds_validator as validator


def _refresh_world(world):
    """Refresh declared checksums after a semantic mutation, bypassing no checks."""
    world["pbn"] = "N:" + " ".join(world["hands"][seat] for seat in "NESW")
    world["fingerprint"] = hashlib.sha256(
        json.dumps(world["hands"], sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()


def _swap_cards(world, first_seat, first_card, second_seat, second_card):
    cards = {}
    for seat in (first_seat, second_seat):
        cards[seat] = {suit + rank for suit, ranks in zip("SHDC", world["hands"][seat].split("."))
                       for rank in ranks}
    cards[first_seat].remove(first_card)
    cards[second_seat].remove(second_card)
    cards[first_seat].add(second_card)
    cards[second_seat].add(first_card)
    for seat, hand in cards.items():
        world["hands"][seat] = ".".join(
            "".join(rank for rank in "AKQJT98765432" if suit + rank in hand) for suit in "SHDC"
        )
    _refresh_world(world)


class IBMWorldsValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.first = generate_worlds(**deepcopy(validator.INPUT))
        cls.second = generate_worlds(**deepcopy(validator.INPUT))

    def altered(self):
        return deepcopy(self.first)

    def test_exact_input_and_canonical_encoding_are_pinned(self):
        self.assertEqual(validator.INPUT, {
            "known_seat": "N", "known_hand_pbn": "AKQJ.T98.765.432",
            "constraints": {"seats": {"S": {"hcp": [12, 14], "suits": {"H": [5, 5]}}}},
            "count": 256, "seed": 20261011, "max_attempts": 50000,
        })
        self.assertEqual(validator.INPUT_SHA256,
                         "6cc2c61c49fe3ade94baccf22d8f8e979ba4a13dd4b2836a0496b667789d98de")
        self.assertEqual(validator.CONSTRAINTS_SHA256,
                         "11ffdbe14d3a04c326e9c5b58e778cde411019562e2a33bd83b4d245f1c54e79")
        self.assertTrue(validator.INPUT_BYTES.endswith(b"\n"))
        self.assertEqual(json.loads(validator.INPUT_BYTES), validator.INPUT)
        self.assertEqual(validator.canonical_json_bytes({"b": 2, "a": 1}), b'{"a":1,"b":2}\n')
        with self.assertRaises(ValueError):
            validator.canonical_json_bytes({"bad": float("nan")})

    def test_genuine_complete_result_and_distributions(self):
        verification = validator.validate_result(self.first)
        self.assertTrue(verification["valid"])
        self.assertEqual(verification["accepted"], 256)
        self.assertEqual(verification["distinct_worlds"], 256)
        self.assertEqual(verification["attempts"], 8836)
        self.assertEqual(verification["hcp_distribution_by_seat"]["N"], {"10": 256})
        self.assertEqual(set(verification["hcp_distribution_by_seat"]["S"]), {"12", "13", "14"})
        self.assertEqual(sum(verification["south_shape_distribution_shdc"].values()), 256)
        self.assertTrue(all(shape.split("-")[1] == "5"
                            for shape in verification["south_shape_distribution_shdc"]))
        self.assertEqual(verification["north_south_heart_fit"], 8)
        self.assertEqual(verification["cross_version_determinism"], "not_established")

    def test_reference_hash_and_observed_two_run_repeatability(self):
        strict_reference = (validator.EXPECTED_RESULT_SHA256
                            if platform.python_implementation() == "CPython"
                            and platform.python_version_tuple()[:2] == ("3", "12") else None)
        verification = validator.validate_repeated_results(
            self.first, self.second,
            generation_python_version=f"{platform.python_implementation()} {platform.python_version()}",
            expected_result_sha256=strict_reference,
        )
        self.assertTrue(verification["repeatability"]["identical_canonical_bytes"])
        self.assertEqual(verification["repeatability"]["runs_checked"], 2)
        self.assertEqual(verification["repeatability"]["validator_python_version"], platform.python_version())
        if strict_reference:
            self.assertEqual(verification["result_sha256"], validator.EXPECTED_RESULT_SHA256)

    def test_optional_strict_hash_and_different_valid_runs_fail_closed(self):
        changed = self.altered()
        changed["worlds"][0], changed["worlds"][1] = changed["worlds"][1], changed["worlds"][0]
        for index in (0, 1):
            changed["worlds"][index]["world_index"] = index
        verification = validator.validate_result(changed)
        self.assertFalse(verification["matches_reference_result"])
        with self.assertRaisesRegex(validator.ValidationError, "result SHA-256"):
            validator.validate_result(changed, expected_result_sha256=validator.EXPECTED_RESULT_SHA256)
        with self.assertRaisesRegex(validator.ValidationError, "not byte-identical"):
            validator.validate_repeated_results(self.first, changed,
                                                generation_python_version=platform.python_version())

    def test_exact_schema_rejects_missing_extra_and_wrong_types(self):
        mutations = (
            lambda result: result.pop("generator_version"),
            lambda result: result.update(unexpected=True),
            lambda result: result.update(complete=1),
            lambda result: result.update(accepted=256.0),
            lambda result: result["worlds"][0].update(world_index=False),
            lambda result: result["worlds"][0].update(unexpected=True),
            lambda result: result["worlds"][0]["hands"].update(extra=""),
            lambda result: result.update(worlds=tuple(result["worlds"])),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                result = self.altered()
                mutate(result)
                with self.assertRaises(validator.ValidationError):
                    validator.validate_result(result)

    def test_exact_metadata_rejects_wrong_engine_generator_seed_or_fallback(self):
        for key, value in (("engine", "DDS"), ("fallback_used", True),
                           ("generator", "another-sampler"), ("generator_version", "v2"),
                           ("seed", 91), ("constraint_class", "KNOWN_HAND_ONLY"),
                           ("constraints_sha256", "0" * 64)):
            with self.subTest(field=key):
                result = self.altered()
                result[key] = value
                with self.assertRaisesRegex(validator.ValidationError, key):
                    validator.validate_result(result)

    def test_incomplete_wrong_count_attempts_and_index_are_rejected(self):
        for key, value in (("requested", 255), ("accepted", 255), ("complete", False),
                           ("attempts", 50001), ("attempts", 8835), ("attempts", True)):
            with self.subTest(field=key, value=value):
                result = self.altered()
                result[key] = value
                with self.assertRaises(validator.ValidationError):
                    validator.validate_result(result)
        result = self.altered()
        result["worlds"].pop()
        with self.assertRaisesRegex(validator.ValidationError, "256 worlds"):
            validator.validate_result(result)
        result = self.altered()
        result["worlds"][1]["world_index"] = 2
        with self.assertRaisesRegex(validator.ValidationError, "world_index"):
            validator.validate_result(result)

    def test_bad_pbn_cards_and_rank_order_are_rejected(self):
        for hand in ("AKQJ.T98.765.43", "AKQJJ.T98.765.43", "AKQJ.T98.765.43?",
                     "KAQJ.T98.765.432", "AKQJ.T98.765.432.extra"):
            with self.subTest(hand=hand):
                result = self.altered()
                result["worlds"][0]["hands"]["N"] = hand
                _refresh_world(result["worlds"][0])
                with self.assertRaises(validator.ValidationError):
                    validator.validate_result(result)

    def test_duplicate_card_across_hands_is_rejected(self):
        result = self.altered()
        # Each hand still has 13 locally unique cards; S2 now appears twice.
        result["worlds"][0]["hands"]["E"] = "632.QJ75.K94.T86"
        _refresh_world(result["worlds"][0])
        with self.assertRaisesRegex(validator.ValidationError, "duplicate cards between hands"):
            validator.validate_result(result)

    def test_changed_north_is_rejected_even_with_valid_deck_and_fingerprint(self):
        result = self.altered()
        _swap_cards(result["worlds"][0], "N", "D7", "E", "D9")
        with self.assertRaisesRegex(validator.ValidationError, "hands.N must equal"):
            validator.validate_result(result)

    def test_south_hcp_constraint_is_independently_checked(self):
        result = self.altered()
        _swap_cards(result["worlds"][0], "S", "HA", "E", "H5")
        with self.assertRaisesRegex(validator.ValidationError, "South HCP"):
            validator.validate_result(result)

    def test_south_heart_constraint_is_independently_checked(self):
        result = self.altered()
        _swap_cards(result["worlds"][0], "S", "H2", "E", "S3")
        with self.assertRaisesRegex(validator.ValidationError, "five hearts"):
            validator.validate_result(result)

    def test_pbn_correspondence_and_fingerprint_are_independently_checked(self):
        for field, value in (("pbn", 'N:invalid\n[Injected "tag"]'), ("fingerprint", "0" * 64)):
            with self.subTest(field=field):
                result = self.altered()
                result["worlds"][0][field] = value
                with self.assertRaisesRegex(validator.ValidationError, field):
                    validator.validate_result(result)

    def test_duplicate_deals_are_rejected_with_correct_indices_and_hashes(self):
        result = self.altered()
        result["worlds"][1] = deepcopy(result["worlds"][0])
        result["worlds"][1]["world_index"] = 1
        with self.assertRaisesRegex(validator.ValidationError, "duplicates an earlier deal"):
            validator.validate_result(result)

    def test_pbn_render_is_pure_validated_ascii(self):
        original = deepcopy(self.first)
        rendered = validator.render_pbn(self.first)
        self.assertEqual(self.first, original)
        self.assertIsInstance(rendered, bytes)
        self.assertEqual(rendered.count(b'[Board "'), 256)
        self.assertEqual(rendered.count(b'[Deal "N:'), 256)
        self.assertIn(b'[Board "256"]', rendered)
        rendered.decode("ascii")
        result = self.altered()
        result["worlds"][0]["pbn"] += '\n[Injected "tag"]'
        with self.assertRaises(validator.ValidationError):
            validator.render_pbn(result)

    def test_validator_has_no_generator_or_nonstdlib_imports(self):
        source = Path(validator.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append((node.module or "").split(".")[0])
        self.assertEqual(set(imports), {"__future__", "collections", "hashlib", "json", "platform", "typing"})
        self.assertNotIn("generate_worlds", source)


if __name__ == "__main__":
    unittest.main()
