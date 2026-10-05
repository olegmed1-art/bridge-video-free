"""Opt-in candidate3 runner integration; synthetic auction pixels, stubbed cards.

Run only with clean, exact-SHA baseline and candidate3 checkouts. The candidate
checkout must include the main auction patch. This is not a real-video/card eval.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

RUNNER = Path(__file__).parents[1] / "tools" / "recognizer_compare.py"
spec = importlib.util.spec_from_file_location("auction_comparison_runner", RUNNER)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

MAKE_FIXTURE = r"""
import json, pathlib, sys
root, output = map(pathlib.Path, sys.argv[1:])
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "tests"))
import cv2
from test_visual_auction_extraction import fixture, frame
ref, config = fixture()
assert cv2.imwrite(str(output / "reference.png"), ref)
(output / "profile.json").write_text(json.dumps({"auction": config}), encoding="utf-8")
video = output / "synthetic.avi"
writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"FFV1"), 1, (400, 360))
assert writer.isOpened(), "FFV1 is required for lossless synthetic pixels"
try:
    for i in range(3):
        writer.write(frame(ref, config, ["1S", "X", "XX", "PASS", "PASS", "PASS"], counter=i))
finally:
    writer.release()
(output / "sprite.dat").write_bytes(b"synthetic: card backend is stubbed\n")
(output / "gold.json").write_text("[]\n", encoding="utf-8")
"""

CHILD = r"""
import importlib.util, json, pathlib, sys
from types import SimpleNamespace
runner_path, config_path = map(pathlib.Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location("runner_under_test", runner_path)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
config = json.loads(config_path.read_text(encoding="utf-8"))
original_load = runner.load_runtime
def load_with_synthetic_card_setup(root, variant):
    # Actual runtime installation, revision gate and checkout-path validation.
    module = original_load(root, variant)
    if variant == "candidate":
        runtime = sys.modules["bridge_runtime_hardening_r26_candidate"]
        assert runtime.REVISION == "3.1-free-r26.3-auction-candidate3"
    import cv2
    ref = cv2.imread(config["inputs"]["reference"]["path"])
    assert ref is not None
    # Only card calibration/assets and card event selection are replaced.
    # Decoder, raw scan, embedded-profile loader, AuctionObserver, recorder,
    # audit hook, output persistence and comparison are the real implementations.
    module.rank_layout.load_profile = lambda *_: SimpleNamespace(width=400, height=360)
    module.rank_layout._template_bank = lambda *_: {}
    module.derive_original_asset_reference = lambda *a, **k: ref
    module.variant_for_pinned_sprite_sha256 = lambda _: 5
    module.load_sprite = lambda *a, **k: SimpleNamespace(card_width=109, card_height=147)
    def no_card_signature(*args):
        raise ValueError("synthetic auction-only fixture has no full hand")
    module.frame_signature = no_card_signature
    return module
runner.load_runtime = load_with_synthetic_card_setup
raise SystemExit(runner.worker(config_path))
"""


def test_candidate3_compare_embedded_auction_pixels_in_separate_offline_workers(tmp_path, monkeypatch):
    names = ("RECOGNIZER_BASELINE_ROOT", "RECOGNIZER_BASELINE_SHA",
             "RECOGNIZER_AUCTION_CANDIDATE_ROOT", "RECOGNIZER_AUCTION_CANDIDATE_SHA")
    values = {name: os.environ.get(name) for name in names}
    if not values["RECOGNIZER_AUCTION_CANDIDATE_ROOT"]:
        pytest.skip("explicit candidate3 checkout required; this gate has not run")
    assert all(values.values()), "provide both exact-SHA checkouts: " + ", ".join(names)
    roots = {
        "baseline": {"root": values[names[0]], "sha": values[names[1]]},
        "candidate": {"root": values[names[2]], "sha": values[names[3]]},
    }
    for runtime in roots.values():
        runner.verify_checkout(runtime["root"], runtime["sha"])
    inputs_dir = tmp_path / "inputs"
    inputs_dir.mkdir()
    real_run = subprocess.run
    fixture = real_run(
        [sys.executable, "-I", "-B", "-c", MAKE_FIXTURE, roots["candidate"]["root"], str(inputs_dir)],
        text=True, capture_output=True, timeout=60, check=False,
    )
    assert fixture.returncode == 0, fixture.stderr
    files = {"video": "synthetic.avi", "reference": "reference.png",
             "profile": "profile.json", "sprite": "sprite.dat", "gold": "gold.json"}
    inputs = {name: {"path": str(inputs_dir / filename),
                     "sha256": runner.digest(inputs_dir / filename)}
              for name, filename in files.items()}
    manifest = dict(schema=runner.SCHEMA, case_id="candidate3-synthetic", source_offset_ms=0,
                    gold_frozen_before_outputs=True, inputs=inputs, **roots)
    manifest_path = tmp_path / "manifest.json"
    runner.write_json(manifest_path, manifest)
    launched = []
    def launch(command, **kwargs):
        assert command[:3] == [sys.executable, "-I", "-B"]
        assert command[4:6] == ["_worker", "--config"]
        config = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
        assert "gold" not in config["inputs"]
        launched.append(config)
        return real_run(
            [sys.executable, "-I", "-B", "-c", CHILD, str(RUNNER), command[-1]],
            **kwargs,
        )
    monkeypatch.setattr(runner, "launch_worker", launch)
    output = tmp_path / "comparison"
    exit_code = runner.compare(manifest_path, runner.digest(manifest_path), output, timeout=90)
    summary = json.loads((output / "comparison.json").read_text())
    assert exit_code == 0, summary
    assert [item["variant"] for item in launched] == ["baseline", "candidate"]
    runs = summary["runs"]
    assert len({os.getpid(), runs["baseline"]["pid"], runs["candidate"]["pid"]}) == 3
    seal = json.loads((output / "seal.json").read_text())
    expected_scope = "PRIMARY_VISUAL_WITH_OPTIONAL_EMBEDDED_PROFILE_AUCTION; NO_ASR_DDS_OR_PUBLISHER"
    for record in (seal, summary, *runs.values()):
        assert record["scope"] == expected_scope
        assert record["runner_version"] == "recognizer-comparison-v1-auction-scope-r3"
    assert runs["baseline"]["auction_result_status"] == "NOT_REPORTED"
    assert runs["candidate"]["auction_result_status"] == "OBSERVED"
    assert runs["candidate"]["version"] == "bridgit-primary-video-gambler-v2-auction-candidate3"
    result_path = Path(launched[1]["output"]) / "result.json"
    result = json.loads(result_path.read_text())
    assert result["deals"] == []
    auction = result["auction_recognition"]["auctions"][0]
    assert auction["complete"] is True and auction["contract"] == "1SXX"
    assert [call["call"] for call in auction["ordered_calls"]] == [
        "1S", "X", "XX", "PASS", "PASS", "PASS"]
    assert len(auction["observations"]) == 3
    for observation in auction["observations"]:
        assert runner.digest(observation["frame_path"]) == observation["frame_sha256"]
    assert summary["accuracy_evaluated"] is False
    assert summary["promotion_allowed"] is False
