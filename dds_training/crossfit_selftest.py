from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from crossfit import _resolve_family, annotate_file, audit_tasks, fold_for_family


class AncestryRegressionTests(unittest.TestCase):
    def parent_child(self):
        parent = {"task_id": "P", "deal_id": "R1", "root_deal_id": "R1",
                  "split": "train", "crossfit_fold": 0}
        child = {"task_id": "C", "deal_id": "DERIVED", "root_deal_id": "R2",
                 "derived_from_task_id": "P", "split": "train", "crossfit_fold": 1}
        return parent, child

    def test_explicit_root_cannot_override_parent(self):
        parent, child = self.parent_child()
        with self.assertRaises(ValueError):
            _resolve_family(child, {"P": parent, "C": child})

    def test_both_explicit_roots_must_agree(self):
        parent, _ = self.parent_child()
        parent["source_root_deal_id"] = "OTHER"
        with self.assertRaises(ValueError):
            _resolve_family(parent, {"P": parent})

    def test_explicit_root_does_not_hide_cycle(self):
        parent, child = self.parent_child()
        child["root_deal_id"] = "R1"
        parent["derived_from_task_id"] = "C"
        with self.assertRaises(ValueError):
            _resolve_family(child, {"P": parent, "C": child})

    def test_explicit_root_does_not_hide_missing_parent(self):
        _, child = self.parent_child()
        with self.assertRaises(ValueError):
            _resolve_family(child, {"C": child})

    def test_audit_rejects_consistent_labels_with_false_ancestry(self):
        parent, child = self.parent_child()
        self.assertEqual(audit_tasks([parent, child])["status"], "error")

    def test_audit_rejects_duplicate_task_ids(self):
        parent, child = self.parent_child()
        child["task_id"] = "P"
        self.assertEqual(audit_tasks([parent, child])["status"], "error")

    def test_annotation_rejects_before_creating_output(self):
        parent, child = self.parent_child()
        with tempfile.TemporaryDirectory() as td:
            source, target = Path(td) / "in.jsonl", Path(td) / "out.jsonl"
            source.write_text("\n".join(json.dumps(x) for x in [parent, child]), encoding="utf-8")
            with self.assertRaises(ValueError):
                annotate_file(source, target, folds=5, seed=17)
            self.assertFalse(target.exists())

    def test_matching_ancestry_and_root_only_manifest_remain_valid(self):
        parent, child = self.parent_child()
        child["root_deal_id"] = "R1"
        child["crossfit_fold"] = 0
        self.assertEqual(_resolve_family(child, {"P": parent, "C": child}), "R1")
        self.assertEqual(audit_tasks([parent, child])["status"], "ok")
        root_only = dict(child)
        del root_only["derived_from_task_id"]
        self.assertEqual(_resolve_family(root_only, {"C": root_only}), "R1")


def main() -> None:
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(AncestryRegressionTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
    tasks = [
        {"task_id": "D1-CT", "deal_id": "D1", "split": "train", "task_type": "contract_tricks"},
        {"task_id": "D1-OL", "deal_id": "D1", "split": "train", "task_type": "opening_lead"},
        {
            "task_id": "D1-CT-ROT1", "deal_id": "D1-ROT1", "split": "derived",
            "source_root_split": "train", "source_root_deal_id": "D1", "task_type": "contract_tricks",
        },
        {"task_id": "D2-CT", "deal_id": "D2", "split": "validation", "task_type": "contract_tricks"},
    ]
    with tempfile.TemporaryDirectory() as td:
        source = Path(td) / "tasks.jsonl"
        target = Path(td) / "crossfit.jsonl"
        source.write_text("".join(json.dumps(x) + "\n" for x in tasks), encoding="utf-8")
        summary = annotate_file(source, target, folds=5, seed=17)
        rows = [json.loads(x) for x in target.read_text(encoding="utf-8").splitlines() if x.strip()]
        audit = audit_tasks(rows)
        assert audit["status"] == "ok", audit
        d1_folds = {x["crossfit_fold"] for x in rows if x["root_deal_id"] == "D1"}
        assert len(d1_folds) == 1
        assert summary["families"] == 2
        assert fold_for_family("D1", folds=5, seed=17) == next(iter(d1_folds))

        broken = [dict(x) for x in rows]
        broken[2]["crossfit_fold"] = (broken[2]["crossfit_fold"] + 1) % 5
        assert audit_tasks(broken)["status"] == "error"

    print(json.dumps({
        "ok": True,
        "same_family_same_fold": True,
        "derived_family_preserved": True,
        "leak_detection": True,
    }, indent=2))


if __name__ == "__main__":
    main()
