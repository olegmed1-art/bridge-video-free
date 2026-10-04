"""Offline structural regressions; GitHub settings remain an external evidence gate."""
from copy import deepcopy
import json
from pathlib import Path
import re
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
PRIVILEGED = {
    "oracle-light-native-cli-install-preflight.yml": {"probe"},
    "diana-longitudinal-v42-regression.yml": {"v42-field-evidence"},
    "bridge-ai-pilot100.yml": {"pilot100"},
    "oracle-universal-video-oci-auth-probe.yml": {"probe"},
    "oci-object-storage-readonly-inventory.yml": {"inventory"},
    "ibm-vpc-power-probe.yml": {"probe", "oracle-probe"},
}
DISPATCH = set(PRIVILEGED) - {
    "oracle-universal-video-oci-auth-probe.yml",
    "oci-object-storage-readonly-inventory.yml",
}
CI_FILE = "workflow-credential-boundary-contract.yml"
EXPECTED_SECRET_NAMES = {
    "oracle-light-native-cli-install-preflight.yml": [
        "ORACLE_SSH_PRIVATE_KEY"
    ],
    "diana-longitudinal-v42-regression.yml": [
        "GOOGLE_DRIVE_OAUTH_JSON"
    ],
    "bridge-ai-pilot100.yml": [
        "BRIDGE_APP_DATABASE_URL"
    ],
    "oracle-universal-video-oci-auth-probe.yml": [
        "OCI_API_FINGERPRINT",
        "OCI_API_PRIVATE_KEY",
        "OCI_API_REGION",
        "OCI_API_TENANCY",
        "OCI_API_USER",
        "OCI_CLI_CONFIG",
        "OCI_CLI_FINGERPRINT",
        "OCI_CLI_KEY_CONTENT",
        "OCI_CLI_REGION",
        "OCI_CLI_TENANCY",
        "OCI_CLI_USER",
        "OCI_CONFIG",
        "OCI_CONFIG_B64",
        "OCI_CREDENTIALS_JSON",
        "OCI_FINGERPRINT",
        "OCI_PRIVATE_KEY",
        "OCI_REGION",
        "OCI_TENANCY_OCID",
        "OCI_USER_OCID"
    ],
    "oci-object-storage-readonly-inventory.yml": [
        "OCI_CLI_FINGERPRINT",
        "OCI_CLI_KEY_CONTENT",
        "OCI_CLI_REGION",
        "OCI_CLI_TENANCY",
        "OCI_CLI_USER"
    ],
    "ibm-vpc-power-probe.yml": [
        "IBM_CLOUD_API_KEY",
        "ORACLE_SSH_PRIVATE_KEY"
    ]
}


def expression(value):
    return "$" + "{{ " + value + " }}"


def contains_secrets(value):
    return bool(re.search(r"\bsecrets\b", json.dumps(value)))


class WorkflowLoader(yaml.BaseLoader):
    """Keep 'on' as a string, reject duplicate keys, never construct Python objects."""
    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in result:
                raise ValueError(f"duplicate workflow key: {key}")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def parse(text):
    return yaml.load(text, Loader=WorkflowLoader)


def validate(name, workflow):
    """Check only the six staged workflows; this is not a repository-wide guarantee."""
    errors = []

    def require(condition, reason):
        if not condition:
            errors.append(reason)

    require(set(workflow["on"]) <= {"pull_request", "workflow_dispatch", "push", "issue_comment"},
            "unexpected trigger")
    require(not contains_secrets({k: v for k, v in workflow.items() if k != "jobs"}),
            "workflow-wide credentials")
    require(PRIVILEGED[name] <= set(workflow["jobs"]), "missing privileged job")
    for job_id, job in workflow["jobs"].items():
        prefix = job_id + ": "
        if job_id not in PRIVILEGED[name]:
            require(not contains_secrets(job), prefix + "credential access in PR/contract job")
            require("environment" not in job, prefix + "environment in PR/contract job")
            require("uses" not in job, prefix + "unreviewed reusable workflow")
            require(job.get("runs-on") == "ubuntu-24.04", prefix + "unreviewed runner")
            continue
        guard = job.get("if", "")
        terms = [term.strip() for term in guard.split("&&")]
        require(terms[0] == "false" and "||" not in guard and expression("")[:3] not in guard,
                prefix + "quarantine missing or bypassed")
        require("github.repository == 'olegmed1-art/bridge-video-free'" in terms,
                prefix + "repository restriction missing")
        require("github.ref == 'refs/heads/main'" in terms, prefix + "main restriction missing")
        require(job.get("environment") == "production-operations", prefix + "environment binding missing")
        require(job.get("runs-on") == "ubuntu-24.04", prefix + "unreviewed runner")
        if name in DISPATCH:
            require("github.event_name == 'workflow_dispatch'" in terms, prefix + "dispatch restriction missing")
            require("inputs.expected_main_sha == github.sha" in terms, prefix + "reviewed SHA missing")
            entry = (workflow["on"].get("workflow_dispatch") or {}).get("inputs", {}).get("expected_main_sha", {})
            require(entry.get("required") == "true" and entry.get("type") == "string",
                    prefix + "required SHA input missing")
        elif name.startswith("oracle-universal"):
            require("github.event_name == 'push'" in terms, prefix + "push restriction missing")
        else:
            require("github.event_name == 'issue_comment'" in terms, prefix + "comment restriction missing")
            require("github.event.comment.user.login == 'olegmed1-art'" in terms, prefix + "owner restriction missing")
            require("github.event.issue.number == 580" in terms, prefix + "issue restriction missing")
            require("github.event.comment.body == '/drive-recovery inventory-oci'" in terms,
                    prefix + "command restriction missing")
        require(not contains_secrets({k: v for k, v in job.items() if k != "steps"}),
                prefix + "job-wide credentials")
        checkouts = []
        for step in job.get("steps", []):
            require(not contains_secrets({k: v for k, v in step.items() if k != "env"}),
                    prefix + "credentials outside step env")
            for value in step.get("env", {}).values():
                if contains_secrets(value):
                    require(bool(re.fullmatch(r"\$\{\{ secrets\.[A-Z0-9_]+ \}\}", value)),
                            prefix + "unreviewed credential expression")
            if "uses" in step:
                require(bool(re.fullmatch(r"[\w-]+/[\w-]+@[0-9a-f]{40}", step["uses"])),
                        prefix + "mutable action")
            if step.get("uses", "").startswith("actions/checkout@"):
                checkouts.append(step)
                require(step.get("with", {}).get("ref") == expression("github.sha"),
                        prefix + "mutable or arbitrary checkout ref")
                require(step.get("with", {}).get("persist-credentials") == "false",
                        prefix + "persisted checkout credentials")
            if "pip install" in step.get("run", ""):
                require(not contains_secrets(step), prefix + "installation receives credentials")
        require(len(checkouts) == 1, prefix + "expected one checkout")
    return errors


class CredentialBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflows = {
            name: parse((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))
            for name in PRIVILEGED
        }

    def test_all_seven_jobs_in_six_workflows_are_quarantined(self):
        self.assertEqual(sum(map(len, PRIVILEGED.values())), 7)
        for name, workflow in self.workflows.items():
            with self.subTest(workflow=name):
                self.assertEqual(validate(name, workflow), [])

    def mutate(self, mutator, reason):
        for name, workflow in self.workflows.items():
            for job_id in PRIVILEGED[name]:
                candidate = deepcopy(workflow)
                mutator(candidate["jobs"][job_id])
                with self.subTest(workflow=name, job=job_id):
                    self.assertTrue(any(reason in error for error in validate(name, candidate)))

    def test_quarantine_preserves_only_reviewed_credential_names(self):
        for name, workflow in self.workflows.items():
            actual = set(re.findall(r"\bsecrets\.([A-Z0-9_]+)", json.dumps(workflow)))
            self.assertEqual(actual, set(EXPECTED_SECRET_NAMES[name]), name)

    def test_main_and_owner_guards_without_hold_are_not_accepted(self):
        self.mutate(lambda j: j.update({"if": j["if"].replace("false &&", "", 1)}), "quarantine")

    def test_or_override_of_false_is_rejected(self):
        self.mutate(lambda j: j.update({"if": j["if"] + " || true"}), "quarantine")

    def test_missing_environment_is_rejected(self):
        self.mutate(lambda j: j.pop("environment"), "environment binding")

    def test_missing_main_guard_is_rejected(self):
        self.mutate(lambda j: j.update({"if": j["if"].replace("github.ref == 'refs/heads/main'", "true")}),
                    "main restriction")

    def test_missing_repository_guard_is_rejected(self):
        self.mutate(lambda j: j.update({"if": j["if"].replace(
            "github.repository == 'olegmed1-art/bridge-video-free'", "true")}), "repository restriction")

    def test_job_wide_secret_is_rejected(self):
        self.mutate(lambda j: j.setdefault("env", {}).update({"SYNTHETIC": expression("secrets.SYNTHETIC")}),
                    "job-wide")

    def test_arbitrary_ref_is_rejected(self):
        def change(job):
            next(s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout@"))["with"]["ref"] = expression("inputs.ref")
        self.mutate(change, "checkout ref")

    def test_checkout_credentials_are_rejected(self):
        def change(job):
            next(s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout@"))["with"]["persist-credentials"] = "true"
        self.mutate(change, "persisted checkout")

    def test_secret_in_added_pr_job_is_rejected(self):
        for name, workflow in self.workflows.items():
            candidate = deepcopy(workflow)
            candidate["jobs"]["extra"] = {"runs-on": "ubuntu-24.04", "env": {"SYNTHETIC": expression("secrets.SYNTHETIC")}}
            self.assertTrue(any("credential access in PR" in e for e in validate(name, candidate)))

    def test_secret_interpolation_in_shell_is_rejected(self):
        self.mutate(lambda j: j["steps"].append({"run": "echo " + expression("secrets.SYNTHETIC")}),
                    "outside step env")

    def test_dependency_install_with_secret_is_rejected(self):
        self.mutate(lambda j: j["steps"].append({
            "run": "python -m pip install example",
            "env": {"SYNTHETIC": expression("secrets.SYNTHETIC")},
        }), "installation receives")

    def test_missing_dispatch_sha_binding_is_rejected(self):
        for name in DISPATCH:
            candidate = deepcopy(self.workflows[name])
            for job_id in PRIVILEGED[name]:
                job = candidate["jobs"][job_id]
                job["if"] = job["if"].replace("inputs.expected_main_sha == github.sha", "true")
            self.assertTrue(any("reviewed SHA missing" in e for e in validate(name, candidate)))

    def test_duplicate_keys_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate workflow key"):
            parse("jobs:\n  probe:\n    if: false\n    if: true\n")

    def test_synthetic_ci_has_no_production_access(self):
        ci = parse((ROOT / ".github/workflows" / CI_FILE).read_text(encoding="utf-8"))
        self.assertFalse(contains_secrets(ci))
        self.assertEqual(ci["permissions"], {"contents": "read"})
        self.assertEqual(set(ci["on"]), {"create", "pull_request"})
        for job in ci["jobs"].values():
            self.assertNotIn("environment", job)
            self.assertEqual(job["runs-on"], "ubuntu-24.04")
        paths = ci["on"]["pull_request"]["paths"]
        for name in PRIVILEGED:
            self.assertIn(".github/workflows/" + name, paths)
        self.assertIn("tests/test_workflow_credential_boundary.py", paths)

    def test_synthetic_ci_has_no_credential_bearing_workflow_run_subscriber(self):
        title = parse((ROOT / ".github/workflows" / CI_FILE).read_text(encoding="utf-8"))["name"]
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
            events = workflow.get("on") or {}
            if isinstance(events, dict) and "workflow_run" in events:
                trigger = events["workflow_run"] or {}
                subscriptions = trigger.get("workflows")
                self.assertIsNotNone(subscriptions, str(path))
                import fnmatch
                self.assertFalse(any(fnmatch.fnmatchcase(title, pattern) for pattern in subscriptions),
                                 f"Unreviewed follower: {path.name}")


if __name__ == "__main__":
    unittest.main()
