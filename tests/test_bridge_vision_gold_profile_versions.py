"""Successor error handling must not mutate the historical r26.3 dependency."""
import hashlib
from pathlib import Path

from bridge_vision import bridgit_gold_profile as historical
from bridge_vision import bridgit_gold_profile_r264 as successor
from bridge_vision import bridgit_primary_production as production263
from bridge_vision import bridgit_primary_production_r264 as production264
from bridge_vision import bridgit_primary_production_r265 as production265


def test_historical_gold_source_keeps_the_existing_evidence_hash():
    assert hashlib.sha256(Path(historical.__file__).read_bytes()).hexdigest() == (
        '6b3d99cc959c168b71e12de9446aa5a0a1fb37c782cd63fd2b4d11b9f8877221')


def test_each_production_family_uses_its_own_gold_builder():
    assert production263.build_autonomous_gold_profile is historical.build_autonomous_gold_profile
    assert production264.build_autonomous_gold_profile is successor.build_autonomous_gold_profile
    assert production265.previous is production264
    assert production263.build_autonomous_gold_profile is not production264.build_autonomous_gold_profile


def test_successor_output_fault_is_not_invalid_input():
    assert issubclass(successor.BridgitGoldProfileOutputError, RuntimeError)
    assert not issubclass(successor.BridgitGoldProfileOutputError, successor.BridgitGoldProfileError)
    assert production264.BridgitGoldProfileError is successor.BridgitGoldProfileError
    assert successor.BridgitGoldProfileError is not historical.BridgitGoldProfileError
