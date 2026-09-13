from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bridge_vision.bridgit_visible_timeline import VisibleTimelineError
from tools.bridge_vision_visible_timeline import run


def request() -> dict:
    identity = {"kind": "EXPLICIT_BOARD", "scope": "session-a", "value": "board-1"}
    return {
        "observations": [
            {
                "frame_sha256": f"{index:064x}",
                "decoded_pixel_sha256": f"{index + 100:064x}",
                "timestamp_ms": index * 1000,
                "deal_identity": identity,
                "cards": [
                    {
                        "card": "AS",
                        "seat": "N",
                        "source": "HAND",
                        "confidence": 0.99,
                        "evidence_pixel_sha256": f"{index + 1000:064x}",
                    }
                ],
            }
            for index in (1, 2)
        ]
    }


def test_cli_runner_writes_private_atomic_receipt(tmp_path: Path) -> None:
    source = tmp_path / "observations.json"
    target = tmp_path / "receipt.json"
    source.write_text(json.dumps(request()), encoding="utf-8")

    receipt = run(tmp_path, Path(source.name), Path(target.name))

    assert receipt["status"] == "SHADOW_PARTIAL_TEMPORAL_OBSERVATION"
    assert json.loads(target.read_text(encoding="utf-8")) == receipt
    assert stat_mode(target) == 0o600


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777


def test_cli_runner_rejects_path_escape(tmp_path: Path) -> None:
    with pytest.raises(VisibleTimelineError, match="escapes job_root"):
        run(tmp_path, Path("../outside.json"), Path("receipt.json"))


def test_cli_runner_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    source = tmp_path / "observations.json"
    source.write_text('{"observations":[],"observations":[]}', encoding="utf-8")
    with pytest.raises(VisibleTimelineError, match="duplicate JSON key"):
        run(tmp_path, source, tmp_path / "receipt.json")


def test_cli_runner_rejects_symlink_input(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_text(json.dumps(request()), encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(VisibleTimelineError, match="input must not be a symlink"):
        run(tmp_path, link, tmp_path / "receipt.json")
