"""Fail-closed offline gate for one reviewed, synthetic PostgreSQL CI commit.

Before publication: --prepublish --manifest /private/external-manifest.json.
The external manifest authenticates all twelve files, including this gate. The CI
check authenticates the other eleven files; it does not self-authenticate this code.
Remote branch freshness, repository visibility and external app hooks still need
an independent check immediately before the single approved push.
At BASE, prepublication checking is source-only. Repeat it on the final clean
single-parent commit, then create the remote branch atomically at that commit.
Never publish a branch at BASE first or push intermediate candidate history.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import yaml

BASE = "4f81878711b825563282d4879ac0386c12a51d6e"
BRANCH = "test/teacher-postgres-20261007"
WORKFLOW = ".github/workflows/teacher-pilot-postgres.yml"
SELF = "tools/teacher_pilot_pg_preflight.py"
NAME = "Teacher pilot disposable PostgreSQL"
# These are reviewed bytes, not hashes learned from the checkout at runtime.
PINS = {
    "bridge_school_api/teacher_pilot_sessions.py": "82c19dad77532003a3be0f916967d163f013b1513410da291409fbe466b8cb75",
    "bridge_school_api/teacher_pilot_checkpoint.py": "89e3946d39a644fa7bbb6bde5dcdf7112bc13098aa6b11b187f5ab2edb497e95",
    "bridge_school_api/teacher_pilot_postgres.py": "82b2ac8b77d422231c096c7a1d3f12e93b188133c155b3c2549c4aed3e3f27f0",
    "bridge_school_api/teacher_pilot_http.py": "5fa82f6d180af20e3d98ef9519568699bfc001a62b47ce53b2b07c47d6a18078",
    "proposal/teacher_pilot_schema.sql": "d3e091eb9827778a3f933022c2a440ff0da687b0cab8ad93967f700f467a62b9",
    "tests/test_teacher_pilot_public.py": "e8d018a16b8b065bfd918c3658a35b1cbc980da671097a619c4ffcbdbcda79fe",
    "tests/test_teacher_pilot_postgres_contract.py": "c77e41e77612a9cb68ac415035c431f539d41f2aac3d3f1301c4100031a85eeb",
    "tests/disposable_teacher_postgres.py": "4b2bff922edb54ae61f82f1a978a2c82f60b497262f1b9c24b0dd3b6eeba2721",
    WORKFLOW: "1459ed356a8e8c57ed57712ad034f24fa3f2b52ca7f1eb735eca64cfdb2979a3",
    ".github/workflows/bridge-video-r29-evidence.yml": "422c9102342437b11b8b3dfbbef043bf5760f7390ecea848f3a7493e98c721b2",
    ".github/workflows/video-production-evidence-contract.yml": "92c26effc6bcdeaf017b3f6210ef923385efea0b7e04807d8bd6f0eda2820d62",
}
ALLOW = set(PINS) | {SELF}


def require(condition, message):
    # Security checks must remain active under python -O / PYTHONOPTIMIZE.
    if not condition:
        raise RuntimeError(message)


def verify_hashes(blobs, pins, sizes=None):
    """Pure byte verification, shared by CI and the external-manifest gate."""
    require(set(blobs) == set(pins), "HASH_PATH_SET_MISMATCH")
    if sizes is not None:
        require(set(sizes) == set(pins), "SIZE_PATH_SET_MISMATCH")
    for path, expected in pins.items():
        require(type(expected) is str and re.fullmatch(r"[0-9a-f]{64}", expected),
                "INVALID_SHA256: " + path)
        require(type(blobs[path]) is bytes, "BYTE_INPUT_REQUIRED: " + path)
        require(hashlib.sha256(blobs[path]).hexdigest() == expected,
                "PUBLIC_BYTES_MISMATCH: " + path)
        if sizes is not None:
            require(type(sizes[path]) is int and sizes[path] >= 0,
                    "INVALID_BYTE_COUNT: " + path)
            require(len(blobs[path]) == sizes[path], "PUBLIC_SIZE_MISMATCH: " + path)


def glob_regex(pattern):
    """Only literal segments, * within a segment, and whole-segment **.

    Reject GitHub's other operators instead of approximating their semantics.
    A single * never crosses /; **/ can match zero directory segments.
    """
    require(type(pattern) is str and bool(pattern), "INVALID_FILTER_PATTERN")
    require(not any(char in pattern for char in "!?+[]{}\\"),
            "UNSUPPORTED_FILTER_PATTERN: " + pattern)
    parts = pattern.split("/")
    expressions = []
    for index, part in enumerate(parts):
        if part == "**":
            expressions.append(".*" if index == len(parts) - 1 else "(?:[^/]+/)*")
        else:
            require("**" not in part, "UNSUPPORTED_FILTER_PATTERN: " + pattern)
            expressions.append(re.escape(part).replace(r"\*", "[^/]*"))
            if index != len(parts) - 1:
                expressions.append("/")
    return re.compile(r"\A" + "".join(expressions) + r"\Z")


def patterns(value):
    require(type(value) is list and bool(value), "FILTER_LIST_REQUIRED")
    return [glob_regex(pattern) for pattern in value]


def matches(value, values):
    return any(pattern.fullmatch(value) is not None for pattern in patterns(values))


class UniqueSafeLoader(yaml.SafeLoader):
    pass


def unique_yaml_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        require(isinstance(key, (str, bool, int, float)) and key not in result,
                "DUPLICATE_OR_UNSUPPORTED_YAML_KEY")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
                                unique_yaml_mapping)


def parse_workflow(text):
    workflow = yaml.load(text, Loader=UniqueSafeLoader)
    require(type(workflow) is dict, "WORKFLOW_MAPPING_REQUIRED")
    require(not ("on" in workflow and True in workflow), "AMBIGUOUS_EVENT_KEY")
    events = workflow.get("on", workflow.get(True))
    if type(events) is str:
        events = {events: None}
    elif type(events) is list:
        require(all(type(event) is str for event in events), "EVENT_NAMES_REQUIRED")
        events = dict.fromkeys(events)
    require(type(events) is dict and bool(events), "EVENT_MAPPING_REQUIRED")
    require(all(type(event) is str for event in events), "EVENT_NAMES_REQUIRED")
    return workflow, events


def potential_push(config):
    if config is None:
        config = {}
    require(type(config) is dict, "PUSH_MAPPING_REQUIRED")
    supported = {"branches", "branches-ignore", "tags", "tags-ignore", "paths", "paths-ignore"}
    require(set(config) <= supported, "UNSUPPORTED_PUSH_CONFIGURATION")
    for positive, negative in (("branches", "branches-ignore"), ("tags", "tags-ignore"),
                               ("paths", "paths-ignore")):
        require(not (positive in config and negative in config), "CONFLICTING_PUSH_FILTERS")
    for values in config.values():
        patterns(values)  # Validate even filters that are conservatively ignored.
    has_branches = "branches" in config or "branches-ignore" in config
    has_tags = "tags" in config or "tags-ignore" in config
    if has_tags and not has_branches:
        return False  # A tag-only filter does not subscribe to branch pushes.
    branch_ok = "branches" not in config or matches(BRANCH, config["branches"])
    if "branches-ignore" in config:
        branch_ok = branch_ok and not matches(BRANCH, config["branches-ignore"])
    # Do not use BASE..HEAD to emulate GitHub's new-branch changed-file list.
    # Any branch-matching workflow is potentially triggered, regardless of paths.
    return branch_ok


def verify_workflows(workflows):
    triggered = []
    for path, source in workflows.items():
        _, events = parse_workflow(source)
        require("create" not in events, "BRANCH_CREATE_WORKFLOW_SIDE_EFFECT: " + path)
        # External integrations can emit these events after a branch push/check.
        require(not (set(events) & {"status", "check_run", "check_suite", "deployment",
                                    "deployment_status"}),
                "POTENTIAL_EXTERNAL_CASCADE: " + path)
        if "push" in events and potential_push(events["push"]):
            triggered.append(path)
        if "workflow_run" in events:
            config = events["workflow_run"]
            require(type(config) is dict, "WORKFLOW_RUN_MAPPING_REQUIRED: " + path)
            names = config.get("workflows")
            require(type(names) is list and bool(names), "WORKFLOW_RUN_NAMES_REQUIRED: " + path)
            for name in names:
                require(type(name) is str and not any(c in name for c in "*!?+[]{}\\"),
                        "UNSUPPORTED_WORKFLOW_RUN_NAME: " + path)
                require(name != NAME, "WORKFLOW_RUN_SIDE_EFFECT: " + path)
    require(set(triggered) == {WORKFLOW}, "POTENTIAL_PUSH_WORKFLOWS: " + repr(sorted(triggered)))


def unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "DUPLICATE_MANIFEST_KEY")
        result[key] = value
    return result


def verify_manifest(text, blobs, actual_sha):
    manifest = json.loads(text, object_pairs_hook=unique_json_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(
                              RuntimeError("NONFINITE_MANIFEST_VALUE")))
    require(type(manifest) is dict, "MANIFEST_MAPPING_REQUIRED")
    require(manifest.get("schema") == "teacher-public-ci-candidate-sha256-v1",
            "MANIFEST_SCHEMA_MISMATCH")
    require(manifest.get("base_sha") == BASE and manifest.get("branch") == BRANCH,
            "MANIFEST_SOURCE_MISMATCH")
    require(manifest.get("private_files_permitted") is False, "PRIVATE_FILES_NOT_REFUSED")
    require(manifest.get("commit_sha") in (None, actual_sha), "MANIFEST_COMMIT_MISMATCH")
    entries = manifest.get("files")
    require(type(entries) is list and len(entries) == len(ALLOW), "MANIFEST_TWELVE_FILES_REQUIRED")
    pins, sizes = {}, {}
    for entry in entries:
        require(type(entry) is dict and set(entry) == {"path", "sha256", "utf8_bytes"},
                "MANIFEST_FILE_FIELDS_MISMATCH")
        path = entry["path"]
        require(type(path) is str and path in ALLOW and path not in pins,
                "MANIFEST_FILE_SET_MISMATCH")
        pins[path], sizes[path] = entry["sha256"], entry["utf8_bytes"]
    require({path: pins[path] for path in PINS} == PINS, "MANIFEST_EMBEDDED_PINS_MISMATCH")
    verify_hashes(blobs, pins, sizes)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepublish", action="store_true")
    parser.add_argument("--manifest")
    args = parser.parse_args(argv)
    require(args.prepublish == bool(args.manifest), "PREPUBLISH_EXTERNAL_MANIFEST_REQUIRED")
    root = Path(__file__).resolve().parents[1]
    def git(*arguments, binary=False):
        return subprocess.check_output(["git", *arguments], cwd=root,
                                       text=not binary)
    actual_sha = git("rev-parse", "HEAD").strip()
    require(bool(re.fullmatch("[0-9a-f]{40}", actual_sha)), "INVALID_COMMIT_SHA")
    require(git("rev-parse", "origin/main").strip() == BASE, "LOCAL_BASE_DRIFT")
    source_only = args.prepublish and actual_sha == BASE
    if not source_only:
        require(git("rev-list", "--parents", "-n", "1", "HEAD").split() == [actual_sha, BASE],
                "ONE_COMMIT_DIRECTLY_ON_BASE_REQUIRED")
        require(not git("status", "--porcelain").strip(), "CLEAN_COMMITTED_TREE_REQUIRED")
    if args.prepublish and not source_only:
        require(git("symbolic-ref", "--short", "HEAD").strip() == BRANCH,
                "CANDIDATE_BRANCH_REQUIRED")
    elif not args.prepublish:
        expected = {"GITHUB_REPOSITORY": "olegmed1-art/bridge-video-free",
                    "GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/" + BRANCH,
                    "GITHUB_SHA": actual_sha, "GITHUB_RUN_ATTEMPT": "1"}
        require(all(os.environ.get(key) == value for key, value in expected.items()),
                "ACTIONS_CONTEXT_MISMATCH")
    diff_target = [] if source_only else [actual_sha]
    changed = set(git("diff", "--name-only", "--no-renames", BASE, *diff_target).splitlines())
    if source_only:
        changed |= set(git("ls-files", "--others", "--exclude-standard").splitlines())
    require(changed == ALLOW, "EXACT_TWELVE_PATH_DIFF_REQUIRED")
    if source_only:
        blobs = {}
        for path in ALLOW:
            local = root / path
            require(local.is_file() and not local.is_symlink(), "REGULAR_PUBLIC_FILE_REQUIRED")
            blobs[path] = local.read_bytes()
    else:
        listing = git("ls-tree", "-z", actual_sha, "--", *sorted(ALLOW), binary=True)
        seen = set()
        for row in listing.split(b"\0"):
            if not row:
                continue
            metadata, raw_path = row.split(b"\t", 1)
            mode, kind, _ = metadata.split()
            path = raw_path.decode("utf-8")
            require(mode in (b"100644", b"100755") and kind == b"blob" and path in ALLOW,
                    "REGULAR_COMMITTED_PUBLIC_FILE_REQUIRED")
            seen.add(path)
        require(seen == ALLOW, "COMMITTED_FILE_SET_MISMATCH")
        blobs = {path: git("show", actual_sha + ":" + path, binary=True) for path in ALLOW}
    verify_hashes({path: blobs[path] for path in PINS}, PINS)
    if args.prepublish:
        manifest_path = Path(args.manifest).resolve()
        require(not manifest_path.is_relative_to(root), "MANIFEST_MUST_STAY_OUTSIDE_CHECKOUT")
        verify_manifest(manifest_path.read_text(encoding="utf-8"), blobs, actual_sha)
    workflows = {}
    for path in (root / ".github/workflows").iterdir():
        if path.suffix in (".yml", ".yaml"):
            require(path.is_file() and not path.is_symlink(), "REGULAR_WORKFLOW_REQUIRED")
            workflows[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
    verify_workflows(workflows)
    # The exact workflow digest above binds all job/step settings, action pins,
    # permissions, service settings and the absence of matrices/secrets/artifacts.
    if source_only:
        print("SOURCE CHECK ONLY:", BASE, "external manifest verified for all 12 worktree files.")
        print("No publication receipt: repeat --prepublish on the final clean single-parent commit; "
              "then create the remote branch atomically at that commit, with no intermediate pushes.")
        return
    mode = "external manifest verified for all 12 files" if args.prepublish else (
        "11 embedded file pins verified; gate self-authentication requires prepublication manifest receipt")
    print("PASS:", actual_sha, "one clean commit on pinned base;", mode)
    print("One reviewed 10-minute job; conservative workflow check passed. "
          "Remote freshness, external integrations and single-push execution are outside this offline gate.")


if __name__ == "__main__":
    main()
