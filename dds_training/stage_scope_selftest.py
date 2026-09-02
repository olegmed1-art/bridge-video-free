from __future__ import annotations

import json

from stage_scope import expected_base_deals, task_in_stage


def main() -> None:
    assert expected_base_deals("pilot") == 10_000
    assert expected_base_deals("main") == 20_000
    assert expected_base_deals("targeted") == 0

    assert task_in_stage({"task_id": "p1", "split": "train", "board": 1}, "pilot")
    assert task_in_stage({"task_id": "p10k", "split": "sealed_test", "board": 10_000}, "pilot")
    assert not task_in_stage({"task_id": "mstart", "split": "train", "board": 10_001}, "pilot")

    assert not task_in_stage({"task_id": "old", "split": "sealed_test", "board": 10_000}, "main")
    assert task_in_stage({"task_id": "mstart", "split": "train", "board": 10_001}, "main")
    assert task_in_stage({"task_id": "mend", "split": "sealed_test", "board": 30_000}, "main")

    assert not task_in_stage({"task_id": "base", "split": "train", "board": 30_001}, "targeted")
    assert task_in_stage({"task_id": "derived", "split": "derived", "source_root_split": "train"}, "pilot")
    assert task_in_stage({"task_id": "derived", "split": "derived", "source_root_split": "train"}, "main")
    assert task_in_stage({"task_id": "derived", "split": "derived", "source_root_split": "train"}, "targeted")

    print(json.dumps({
        "ok": True,
        "pilot_fresh_deals": expected_base_deals("pilot"),
        "main_fresh_deals": expected_base_deals("main"),
        "pilot_holdout_excluded_from_main": True,
        "targeted_random_base_disabled": True,
    }, indent=2))


if __name__ == "__main__":
    main()
