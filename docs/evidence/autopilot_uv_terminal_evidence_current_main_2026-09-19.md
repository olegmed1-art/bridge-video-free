# Universal Video terminal-evidence current-main repair

Date: 2026-09-21
Governance: ASSURED
Status: AUTOPILOT_WORKSPACE

## Purpose

Repair the exact-head PR #1698 evidence gate without changing the inherited Universal Video terminal-v2 implementation. At the repair preflight, the fetched `main` tip was `6156fa09934b29d045092039f517a236642b717a` and the merge-base with PR head `8c8f2a3eb8dc28b94d7e2dc466eb3d04f49ba2de` was `43a4a7fda68c9457f56b1dc7c6fe7f1b161d6272`.

The terminal-v2 implementation, migration, and their existing tests are inherited from the base history and are not changes introduced by this PR. This repair changes only the pre-canary workflow, its regression test, and this evidence record.

## Required safety properties

- Revalidate immutable source identity at the terminal boundary.
- Re-read and validate processor artifacts rather than trusting cached or caller-constructed mappings.
- Enforce terminal evidence inside the database transition so alternate callers cannot bypass it.
- Preserve fail-closed behavior for malformed, partial, stale, oversized, or mismatched evidence.
- Do not change Canon semantics, enable Canon/WORLD writes, run production media, deploy, or mutate production.

## Acceptance

- The pre-canary workflow validates exact base and head SHAs and checks whitespace over the complete merge-base-to-head range.
- A regression test proves that a defect in an earlier PR commit is caught even when the final commit is clean.
- Focused repository checks are recorded below and run on the repaired exact head.
- No production promotion or merge is authorized by this repair.

## Repair verification

- PASS: `git diff --check` completed with no findings.
- PASS: the repository diff is limited to the three repair files named above.
- PASS: `python -m pytest -q tests/test_issue_881_precanary_hardening.py tests/test_universal_video_terminal_evidence_v2.py tests/test_universal_video_neon_queue.py` completed with `102 passed`.
- The authoritative exact-head CI result is recorded by the pull-request checks rather than copied into this commit.

## Rollback

Revert the three-file repair commit if the evidence-gate change must be withdrawn. No production application, deployment, or merge is authorized by this workspace commit.
