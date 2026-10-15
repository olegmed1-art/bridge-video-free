"""Offline contract for the Vercel Ignored Build Step."""

import os
from pathlib import Path
import subprocess
import tempfile


GATE = Path(__file__).resolve().parents[1] / "scripts" / "vercel_ignore_build.sh"
SHA = "a" * 40
OTHER_SHA = "b" * 40


def run_gate(**changes):
    env = os.environ.copy()
    for name in (
        "VERCEL_GIT_COMMIT_REF",
        "VERCEL_ENV",
        "VERCEL_GIT_COMMIT_SHA",
        "VERCEL_APPROVED_RUNTIME_SHA",
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
    # The real build bundle has no .git directory.
    with tempfile.TemporaryDirectory() as directory:
        return subprocess.run(
            ["bash", str(GATE)], cwd=directory, env=env,
            capture_output=True, text=True, check=False,
        )


def test_exact_main_production_release_builds_without_git():
    result = run_gate()
    assert result.returncode == 1, result.stdout + result.stderr
    assert "exact runtime release approved; build" in result.stdout


def test_unapproved_and_different_commits_skip():
    for approval in (None, "", OTHER_SHA, "not-a-sha", SHA.upper()):
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


def test_missing_git_comparison_base_cannot_trigger_build():
    result = run_gate(VERCEL_APPROVED_RUNTIME_SHA=None, VERCEL_GIT_PREVIOUS_SHA=None)
    assert result.returncode == 0, result.stdout + result.stderr


def main():
    test_exact_main_production_release_builds_without_git()
    test_unapproved_and_different_commits_skip()
    test_missing_or_invalid_commit_skips()
    test_nonproduction_and_nonmain_skip_even_when_approved()
    test_missing_git_comparison_base_cannot_trigger_build()
    print("VERCEL_IGNORE_BUILD_CONTRACT_PASS")


if __name__ == "__main__":
    main()
