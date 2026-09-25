"""Optional card evidence is unavailable only for expected input failures."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

import pytest

from bridge_vision import bridgit_primary_production_r264 as production
from bridge_vision.bridgit_gold_profile_r264 import BridgitGoldProfileError, BridgitGoldProfileOutputError
from bridge_vision.bridgit_primary_video_r264 import PrimaryVideoInputError, PrimaryVideoRecognitionError


@pytest.mark.parametrize("failure", [
    production.PrimaryInputUnavailable("no pinned assets"),
    BridgitGoldProfileError("invalid gold archive"),
    zipfile.BadZipFile("invalid ZIP"),
    PrimaryVideoInputError("video decoder cannot open source"),
])
def test_expected_input_failure_is_reported_as_unavailable(failure):
    with patch.object(production, "_prepare_profile_seed", side_effect=failure):
        deals, shots, qc = production._run_primary(None, "token", Path("video"), Path("work"), "job")
    assert deals == shots == []
    assert qc == {"status": "UNAVAILABLE", "reason": type(failure).__name__, "detail": str(failure)[:160]}


@pytest.mark.parametrize("boundary", ["_prepare_profile_seed", "_prepare_assets", "recognize_video_primary"])
def test_unexpected_failure_is_not_reported_as_missing_observation(boundary):
    with patch.object(production, "_prepare_profile_seed", return_value=(Path("ref"), Path("profile"), {}, {})):
        with patch.object(production, "_prepare_assets", return_value=Path("assets")):
            with patch.object(production, "recognize_video_primary", return_value={"deals": []}):
                with patch.object(production, boundary, side_effect=RuntimeError("internal bug")):
                    with pytest.raises(RuntimeError, match="internal bug"):
                        production._run_primary(None, "token", Path("video"), Path("work"), "job")


def test_invalid_gold_candidate_can_fall_through_to_a_valid_one(tmp_path):
    io = SimpleNamespace(
        search=lambda *_args: [{"id": "bad"}, {"id": "good"}],
        download=lambda _token, _id, target: target.write_bytes(b"synthetic"),
    )
    base = SimpleNamespace(io=io)

    def parse(_target, directory):
        if directory.name.endswith("-0"):
            raise BridgitGoldProfileError("invalid candidate")
        return Path("reference"), Path("profile"), {"profile_id": "good"}

    with patch.object(production, "build_autonomous_gold_profile", side_effect=parse):
        result = production._prepare_profile_seed(base, "token", tmp_path)
    assert result[3]["id"] == "good"
    assert not (tmp_path / "bridgit-gold-v2-0.zip").exists()


def test_program_failure_during_gold_candidate_validation_propagates(tmp_path):
    io = SimpleNamespace(
        search=lambda *_args: [{"id": "candidate"}],
        download=lambda _token, _id, target: target.write_bytes(b"synthetic"),
    )
    with patch.object(production, "build_autonomous_gold_profile", side_effect=RuntimeError("validation bug")):
        with pytest.raises(RuntimeError, match="validation bug"):
            production._prepare_profile_seed(SimpleNamespace(io=io), "token", tmp_path)


def test_gold_reference_output_failure_is_not_an_invalid_candidate(tmp_path):
    io = SimpleNamespace(
        search=lambda *_args: [{"id": "candidate"}],
        download=lambda _token, _id, target: target.write_bytes(b"synthetic"),
    )
    with patch.object(production, "build_autonomous_gold_profile", side_effect=BridgitGoldProfileOutputError("write failed")):
        with pytest.raises(BridgitGoldProfileOutputError, match="write failed"):
            production._prepare_profile_seed(SimpleNamespace(io=io), "token", tmp_path)


@pytest.mark.parametrize("message", ["cannot encode recognition frame", "same-layout observations disagree with canonical hands"])
def test_integrity_or_encoding_fault_from_video_is_not_unavailable(message):
    with patch.object(production, "_prepare_profile_seed", return_value=(Path("ref"), Path("profile"), {}, {})):
        with patch.object(production, "_prepare_assets", return_value=Path("assets")):
            with patch.object(production, "recognize_video_primary", side_effect=PrimaryVideoRecognitionError(message)):
                with pytest.raises(PrimaryVideoRecognitionError, match=message):
                    production._run_primary(None, "token", Path("video"), Path("work"), "job")
