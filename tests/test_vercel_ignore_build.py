"""Offline contract for the Vercel Ignored Build Step."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


GATE = Path(__file__).resolve().parents[1] / "scripts" / "vercel_ignore_build.sh"
ROOT = GATE.parents[1]
BASH = shutil.which("bash")
SHA = "a" * 40
OTHER_SHA = "b" * 40


def test_deployment_commit_contains_gate_and_whitelist():
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert config["ignoreCommand"] == "bash scripts/vercel_ignore_build.sh"
    ignore = (ROOT / ".vercelignore").read_text(encoding="utf-8").splitlines()
    assert "!scripts" in ignore
    assert "!scripts/vercel_ignore_build.sh" in ignore


def run_gate(**changes):
    env = os.environ.copy()
    for name in (
        "VERCEL_GIT_COMMIT_REF",
        "VERCEL_ENV",
        "VERCEL_GIT_COMMIT_SHA",
        "VERCEL_APPROVED_RUNTIME_SHA",
        "VERCEL_APPROVED_PREVIEW_SHA",
        "VERCEL_GIT_PREVIOUS_SHA",
    ):
        env.pop(name, None)
    env.update({
        "VERCEL_GIT_COMMIT_REF": "main",
        "VERCEL_ENV": "production",
        "VERCEL_GIT_COMMIT_SHA": SHA,
        "VERCEL_APPROVED_RUNTIME_SHA": SHA,
    })
    for name, value in changes.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    # Match the build bundle: no .git and no git executable on PATH.
    with tempfile.TemporaryDirectory() as directory:
        assert not Path(directory, ".git").exists()
        env["PATH"] = directory
        return subprocess.run(
            [BASH, str(GATE)], cwd=directory, env=env,
            capture_output=True, text=True, check=False,
        )


def test_exact_main_production_release_builds_without_git():
    result = run_gate()
    assert result.returncode == 1, result.stdout + result.stderr
    assert "exact release approved; build" in result.stdout


def test_unapproved_and_different_commits_skip():
    for approval in (None, "", OTHER_SHA, "not-a-sha", SHA.upper(), "g" * 40, "a" * 39, "a" * 41, "$(exit 1)"):
        result = run_gate(VERCEL_APPROVED_RUNTIME_SHA=approval)
        assert result.returncode == 0, (approval, result.stdout, result.stderr)
        assert "skip" in result.stdout


def test_missing_or_invalid_commit_skips():
    for commit in (None, "", "not-a-sha"):
        result = run_gate(VERCEL_GIT_COMMIT_SHA=commit)
        assert result.returncode == 0, (commit, result.stdout, result.stderr)


def test_nonproduction_and_nonmain_skip_even_when_approved():
    for changes in (
        {"VERCEL_GIT_COMMIT_REF": "feature/runtime"},
        {"VERCEL_GIT_COMMIT_REF": None},
        {"VERCEL_ENV": "preview"},
        {"VERCEL_ENV": None},
    ):
        result = run_gate(**changes)
        assert result.returncode == 0, (changes, result.stdout, result.stderr)


def test_preview_requires_separate_exact_sha_and_nonmain_ref():
    approved = run_gate(
        VERCEL_GIT_COMMIT_REF="feature/runtime",
        VERCEL_ENV="preview",
        VERCEL_APPROVED_RUNTIME_SHA=OTHER_SHA,
        VERCEL_APPROVED_PREVIEW_SHA=SHA,
    )
    assert approved.returncode == 1, approved.stdout + approved.stderr
    production_with_preview_only = run_gate(
        VERCEL_APPROVED_RUNTIME_SHA=None,
        VERCEL_APPROVED_PREVIEW_SHA=SHA,
    )
    assert production_with_preview_only.returncode == 0
    for changes in (
        {"VERCEL_APPROVED_PREVIEW_SHA": None},
        {"VERCEL_APPROVED_PREVIEW_SHA": OTHER_SHA},
        {"VERCEL_APPROVED_PREVIEW_SHA": "$(exit 1)"},
        {"VERCEL_GIT_COMMIT_REF": "main"},
        {"VERCEL_GIT_COMMIT_REF": ""},
        {"VERCEL_ENV": "production"},
    ):
        env = {
            "VERCEL_GIT_COMMIT_REF": "feature/runtime",
            "VERCEL_ENV": "preview",
            "VERCEL_APPROVED_RUNTIME_SHA": None,
            "VERCEL_APPROVED_PREVIEW_SHA": SHA,
        }
        env.update(changes)
        result = run_gate(**env)
        assert result.returncode == 0, (changes, result.stdout, result.stderr)


def test_missing_git_comparison_base_cannot_trigger_build():
    result = run_gate(VERCEL_APPROVED_RUNTIME_SHA=None, VERCEL_GIT_PREVIOUS_SHA=None)
    assert result.returncode == 0, result.stdout + result.stderr


def main():
    test_deployment_commit_contains_gate_and_whitelist()
    test_exact_main_production_release_builds_without_git()
    test_unapproved_and_different_commits_skip()
    test_missing_or_invalid_commit_skips()
    test_nonproduction_and_nonmain_skip_even_when_approved()
    test_preview_requires_separate_exact_sha_and_nonmain_ref()
    test_missing_git_comparison_base_cannot_trigger_build()
    print("VERCEL_IGNORE_BUILD_CONTRACT_PASS")


if __name__ == "__main__":
    main()
