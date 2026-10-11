"""Offline negative probes for the bounded HTTP/SQL integration gate."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.tournament_pilot import integration_preflight as guard


def event():
    return {"number": 2147, "repository": {"full_name": guard.REPOSITORY},
            "pull_request": {"number": 2147,
                "head": {"ref": guard.BRANCH, "sha": "a" * 40, "repo": {"full_name": guard.REPOSITORY}},
                "base": {"ref": "main", "sha": guard.MAIN, "repo": {"full_name": guard.REPOSITORY}}}}


def workflow():
    return {"permissions": {"contents": "read"},
        "on": {"push": {"branches": ["test/tournament-shape-pilot-20261004"]},
               "pull_request": {"branches": ["main"]}},
        "jobs": {"offline-consumer": {
            "if": guard.JOB_IF, "runs-on": "ubuntu-24.04", "timeout-minutes": 10,
            "services": {"postgres": {"image": "postgres:18", "ports": ["127.0.0.1:55432:5432"],
                "env": {"POSTGRES_DB": "tournament_rehearsal", "POSTGRES_USER": "postgres",
                        "POSTGRES_HOST_AUTH_METHOD": "trust"}}},
            "steps": [
                {"uses": "actions/checkout@test", "with": {"persist-credentials": False, "fetch-depth": 0}},
                {"name": "Exact SHA and isolation preflight", "if": "github.event_name == 'push'"},
                {"name": "PR2147 exact revision and isolation preflight", "if": "github.event_name == 'pull_request'"},
                {"name": "Authenticated screen, SQL gates and rollback", "shell": "bash",
                 "run": "assert result['status'] == 'PASS'"},
                {"name": "Bound and verify evidence before upload",
                 "run": "python -m tools.tournament_pilot.integration_preflight evidence"},
                {"uses": "actions/upload-artifact@test", "with": {"path": "\n".join(guard.ARTIFACTS), "retention-days": 7}},
            ]}}}


def evidence():
    row_budget = {"public.source": 1, "public.knowledge_item": 2, "public.knowledge_version": 2,
                  "public.knowledge_version_source": 2, "bidding.rule": 2, "bidding.rule_test": 8,
                  "bidding.rule_test_run": 8, "ai.decision_position": 1, "public.canon_activation": 4,
                  "bidding.runtime_activation": 4, "bidding.ingestion_run": 1, "bidding.ingestion_event": 5}
    cases = []
    for _ in range(2):
        for name in ("positive", "minor_orientation", "wrong_singleton", "singleton_boundary", "short_minor",
                     "hidden_context", "wrong_turn", "interference", "missing_hand"):
            status = "PARTIAL" if name in ("positive", "minor_orientation", "wrong_singleton", "singleton_boundary", "short_minor") else "ABSTAIN"
            shape = "UNKNOWN" if status == "ABSTAIN" else ("MATCH" if name in ("positive", "minor_orientation") else "MISMATCH")
            cases.append({"case": name, "observed": status, "expected": status, "shape_status": shape,
                          "assessment_version": "tournament-call-assessment-v2"})
    return {"status": "PASS", "authenticated_api": True, "auth_missing": 401, "auth_wrong": 403,
            "screen_lifecycle": ["ABSTAIN"] * 3, "screen_shape_lifecycle": ["MATCH", "UNKNOWN", "MATCH"],
            "total_pilot_rows": 40, "row_budget": row_budget, "teacher_output_writes": 0,
            "search_runs": 0, "final_decisions": 0, "reactivation_extends_expiry": False, "ttl_hours": 24,
            "extra_synthetic_school_fixture": 1, "other_position": "ABSTAIN",
            "assessment_version": "tournament-call-assessment-v2", "semantic_cases": cases}


class IntegrationGuardTests(unittest.TestCase):
    def test_exact_event_and_reviewed_delta(self):
        self.assertEqual(guard.validate_event(event()), "a" * 40)
        root = Path(__file__).resolve().parents[2]
        guard.validate_delta({p: "M" for p in guard.ALLOWED},
                             {p: (root / p).read_text(encoding="utf-8") for p in guard.ALLOWED})

    def test_fork_branch_base_and_command_injection_are_rejected(self):
        for side, field, value in [("head", "ref", "main"), ("base", "sha", "b" * 40),
                                   ("head", "sha", "; curl invalid")]:
            changed = event()
            changed["pull_request"][side][field] = value
            with self.subTest(side=side, field=field), self.assertRaises(ValueError):
                guard.validate_event(changed)
        changed = event()
        changed["pull_request"]["head"]["repo"]["full_name"] = "someone/fork"
        with self.assertRaises(ValueError):
            guard.validate_event(changed)

    def test_other_pr_is_rejected(self):
        changed = event()
        changed["number"] = 2148
        with self.assertRaises(ValueError):
            guard.validate_event(changed)

    def test_runtime_change_or_deletion_cannot_enter_integration_patch(self):
        changes = dict.fromkeys(guard.ALLOWED, "M")
        for changed in (changes | {"bridge_school_api/main.py": "M"},
                        changes | {guard.WORKFLOW: "D"}):
            with self.assertRaises(ValueError):
                guard.validate_delta(changed, dict.fromkeys(changed, ""))

    def test_secret_and_private_provenance_scanner_remains_active(self):
        changes = dict.fromkeys(guard.ALLOWED, "M")
        samples = ["ghp_" + "a" * 24, "postgres" + "://private/db", "Sent" + "inel_abc123"]
        for sample in samples:
            contents = dict.fromkeys(changes, "") | {guard.WORKFLOW: sample}
            with self.subTest(sample=sample), self.assertRaises(ValueError):
                guard.validate_delta(changes, contents)

    def test_valid_isolation_contract(self):
        guard.validate_workflow(workflow(), "")
        guard.validate_evidence(evidence())

    def test_budget_port_permissions_and_credentials_cannot_expand(self):
        mutations = [lambda w: w["permissions"].update(contents="write"),
                     lambda w: w.update(env={"DATABASE_URL": "unexpected"}),
                     lambda w: w["jobs"]["offline-consumer"].update(**{"timeout-minutes": 11}),
                     lambda w: w["jobs"]["offline-consumer"].update(**{"runs-on": "larger-runner"}),
                     lambda w: w["jobs"]["offline-consumer"]["services"]["postgres"].update(ports=["55432:5432"]),
                     lambda w: w["jobs"]["offline-consumer"]["steps"][0].update(env={"TOKEN": "unexpected"}),
                     lambda w: w["jobs"]["offline-consumer"]["steps"][-1]["with"].update(**{"retention-days": 90}),
                     lambda w: w["on"].update(workflow_dispatch={})]
        for mutate in mutations:
            w = workflow()
            mutate(w)
            with self.assertRaises(ValueError):
                guard.validate_workflow(w, "")

    def test_upload_must_follow_size_check(self):
        w = workflow()
        steps = w["jobs"]["offline-consumer"]["steps"]
        steps[-2], steps[-1] = steps[-1], steps[-2]
        with self.assertRaises(ValueError):
            guard.validate_workflow(w, "")

    def test_false_positive_integration_evidence_is_rejected(self):
        for change in ({"authenticated_api": False}, {"screen_lifecycle": ["SUPPORTED"] * 3},
                       {"screen_shape_lifecycle": ["MATCH"] * 3}, {"teacher_output_writes": 1},
                       {"teacher_output_writes": False}, {"reactivation_extends_expiry": True},
                       {"semantic_cases": []}, {"total_pilot_rows": 41}, {"row_budget": {"fixture": 40}},
                       {"assessment_version": "old"}, {"semantic_cases": [evidence()["semantic_cases"][0]] * 18}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                guard.validate_evidence(evidence() | change)
        bad = deepcopy(evidence())
        bad["semantic_cases"][0].update(observed="SUPPORTED", expected="SUPPORTED")
        with self.assertRaises(ValueError):
            guard.validate_evidence(bad)

    def test_missing_or_oversized_artifacts_cannot_upload(self):
        with TemporaryDirectory(dir=Path.cwd(), prefix="integration-evidence-test-") as directory:
            root = Path(directory)
            self.assertTrue(root.resolve().is_relative_to(Path.cwd().resolve()))
            with self.assertRaisesRegex(ValueError, "MISSING_OR_LINKED_ARTIFACT"):
                guard.verify_artifacts(root)
            package, result = (root / name for name in guard.ARTIFACTS)
            package.parent.mkdir(parents=True)
            package.write_text("{}", encoding="utf-8")
            result.write_text(json.dumps(evidence()), encoding="utf-8")
            guard.verify_artifacts(root)
            package.write_bytes(b"x" * guard.MAX_ARTIFACT_BYTES)
            with self.assertRaisesRegex(ValueError, "ARTIFACT_SIZE_LIMIT"):
                guard.verify_artifacts(root)

    def test_artifact_pass_label_does_not_hide_wrong_semantics(self):
        with TemporaryDirectory(dir=Path.cwd(), prefix="integration-evidence-test-") as directory:
            root = Path(directory)
            self.assertTrue(root.resolve().is_relative_to(Path.cwd().resolve()))
            package, result = (root / name for name in guard.ARTIFACTS)
            package.parent.mkdir(parents=True)
            package.write_text("{}", encoding="utf-8")
            result.write_text(json.dumps(evidence() | {"teacher_output_writes": 1}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "UNEXPECTED_WRITES"):
                guard.verify_artifacts(root)


if __name__ == "__main__":
    unittest.main()
