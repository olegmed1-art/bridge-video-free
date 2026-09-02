#!/usr/bin/env python3
"""Live zero-secret smoke test for speaker diarization v2.

Downloads only public sherpa-onnx release assets, runs the official four-speaker
wave through the same local engine used by Bridge Video, and fails unless the
engine returns multiple speakers and non-empty speaker turns.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from bridge_speaker_diarization_v2 import _download, _ensure_models, _run_sherpa

TEST_WAV_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-segmentation-models/0-four-speakers-zh.wav"
)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="bridge-speaker-v2-smoke-") as tmp:
        root = Path(tmp)
        wav = root / "0-four-speakers-zh.wav"
        _download(TEST_WAV_URL, wav)
        models = _ensure_models(root / "models")
        turns, report = _run_sherpa(wav, models, num_speakers=4)
        speakers = sorted({int(turn["speaker"]) for turn in turns})
        if len(turns) < 4:
            raise SystemExit(f"SMOKE_FAIL: too few speaker turns: {len(turns)}")
        if len(speakers) < 3:
            raise SystemExit(f"SMOKE_FAIL: too few distinct speakers: {speakers}")
        print(
            "SPEAKER_DIARIZATION_V2_SMOKE_PASS",
            f"turns={len(turns)}",
            f"speakers={speakers}",
            f"engine={report.get('engine')}",
            f"version={report.get('engine_version')}",
        )


if __name__ == "__main__":
    main()
