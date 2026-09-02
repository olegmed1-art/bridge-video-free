# L1 canonical runtime v1

This module is a deterministic, side-effect-free executable mirror of the approved School L1 canonical rules that were promoted into the Drive catalog on 2026-08-21.

## Safety boundary

- System version is fixed to `SCHOOL_L1_DB_V1`.
- Tournament-system rules are ignored unless an explicit system switch is made outside this runtime.
- `PARTIAL_CANON_SCOPE` and `BLOCKED_PENDING_TEACHER` content cannot be auto-promoted.
- The Lesson 15 attitude/count/suit-preference signalling code remains undefined and is explicitly blocked.
- Same-rank rules that still disagree return `RULE_CONFLICT`; the runtime never guesses a winner.
- No Drive, Neon, DDS, network, or production writes are performed.

## Scope

`bridge_school_api/l1_canonical_runtime.py` implements the executable core needed by the current L1 regression contract: HCP, contract/trick rules, major/NT openings, weak and strong openings, basic responses, Stayman/Transfer semantics, competitive overcalls and doubles, revaluation, scoring, declarer planning, and governance gates.

The initial contract contains 52 regression tests in `tests/test_l1_canonical_runtime.py`. They correspond to the 52 cases in the Drive tab `AI — L1 Regression` at the time of implementation.

The module intentionally does not attempt to parse free-form `Conditions` / `Action` text from Sheets. Each executable handler is explicit and references the canonical Fact/Skill IDs in evidence. This avoids silently inventing a grammar or filling missing conditions from general bridge knowledge.

## CI

`.github/workflows/l1-canonical-runtime-ci.yml` runs the 52-case regression suite on Python 3.12. A passing CI run proves the repository runtime matches the encoded L1 regression contract; it does **not** imply Neon synchronization or production deployment.
