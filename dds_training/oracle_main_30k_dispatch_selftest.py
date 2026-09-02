from __future__ import annotations

import json
import tempfile
from pathlib import Path

from oracle_main_30k_dispatch import TRAIN_TASKS, validate_train_identity


def write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def must_fail(tasks: Path, predictions: Path, fragment: str) -> None:
    try:
        validate_train_identity(tasks, predictions)
    except SystemExit as exc:
        assert fragment in str(exc), (fragment, str(exc))
    else:
        raise AssertionError("unsafe 30k identity was accepted")


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        tasks = root / "tasks.jsonl"
        predictions = root / "predictions.jsonl"
        task_rows = [{"task_id": "T%05d" % i, "split": "train"} for i in range(TRAIN_TASKS)]
        prediction_rows = [{"task_id": "T%05d" % i, "locked": True} for i in range(TRAIN_TASKS)]
        write(tasks, task_rows)
        write(predictions, prediction_rows)
        assert validate_train_identity(tasks, predictions) == TRAIN_TASKS

        bad = list(prediction_rows)
        bad[-1] = {"task_id": "WRONG", "locked": True}
        write(predictions, bad)
        must_fail(tasks, predictions, "identity mismatch")

        write(predictions, prediction_rows)
        bad_tasks = list(task_rows)
        bad_tasks[-1] = {"task_id": "T27999", "split": "validation"}
        write(tasks, bad_tasks)
        must_fail(tasks, predictions, "TRAIN-only")

        write(tasks, task_rows)
        unlocked = list(prediction_rows)
        unlocked[-1] = {"task_id": "T27999", "locked": False}
        write(predictions, unlocked)
        must_fail(tasks, predictions, "must be locked")

        print(json.dumps({"ok": True, "train_tasks": TRAIN_TASKS,
                          "holdout_blocked": True, "identity_bound": True,
                          "unlocked_predictions_blocked": True}))


if __name__ == "__main__":
    main()
