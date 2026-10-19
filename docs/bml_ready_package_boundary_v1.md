# BML installation-shaped boundary candidate

Build: BML-READY-BOUNDARY-INSTALL-CANDIDATE-V2, 2026-10-19.
Runtime module remains bml-ready-package-boundary-test-v1, SHA256
e76069163d8d1c4b1cb9eee729d0ef6bc2ada8aa409927ddd687c9c44340b9b8.
Only test packaging and these installation notes changed. No bid semantics,
positive FULL_CALL path, runtime activation or production readiness is added.

## File ownership

The parent assigned the BML reader and Ready/FULL_CALL boundary to BML. The
single Teacher integrator owns its three Teacher modules, their tests and entry
routing. This patch changes no Teacher production module, app.py, shared L1
runtime, SQL, profile, permission, role or deployment. The two Teacher .py files
under tests/fixtures/bml_ready_owner are exact source fixtures read as AST only;
they are not executable installed Teacher modules. No parallel deploy or IBM.

## Entry contract for the single integrator

read_eligible_skills(query: dict) -> list[dict], with exactly these query fields:
system_profile, skill_ids, role, phase, trump, task_class, adapter_ref.
The existing controller's seven-field callable shape is confirmed. A BML skill
mapping and live runtime/profile binding are still not admitted.

BMLReadyPackageReader(test_only=True).read_eligible_skills(query) raises a
ReadyPackageUnavailable refusal. It does not claim that an empty live catalog
was successfully read. L1 profile is rejected before any BML contract can be
considered. Source snapshots, hands, exercise answers, compiled payloads and
caller authorization flags are not valid reader query fields.

answer_first_response(proposed_call, assessment_mode='FULL_CALL', test_only=True)
returns the teacher-answer-v04-test-v1 withheld envelope. Its scope is
BML_FULL_CALL_BOUNDARY_TEST_ONLY. All ten calls return ABSTAIN, no
answer/decision/proposal/binding, and empty knowledge_versions/trace.
The integrator must not advertise READY, BML bid support or student delivery
from installing this source. Existing unsupported/default routing must retain
the same refusal until a separately reviewed real implementation is accepted.

The offline source-condition evaluator and its opt-in shape-core delta are
retained as offline evidence, not wired into the game reader by this target.
This bundle contains no primary-source parser or new ASR/runtime writer.

## Portable local check

From an installation-shaped checkout containing the five additive files:

```text
python -I -S -B -X utf8 tests/test_bml_ready_package_adapter.py
```

The test now locates bridge_school_api and its two hash-pinned AST fixtures
relative to the repository root. The old prep-only after/input layout is no
longer required. It checks nine boundary cases only, without executing the
Teacher fixtures or its 76-test suite. Prior native two-negative and two-L1
Ready/re-read receipts remain evidence for those exact fixture paths only.

## Unresolved real binding

Fresh primary registry read on 2026-10-19: the ten RULE-TOUR-1NT-RSP rows are
ACTIVE in SCHOOL_TOURNAMENT_CURRENT_V1, but DB UUID is blank and Sync status is
DRIVE_CANON_ONLY. This is not a live database read and does not prove that no
server rows exist. No trusted current server snapshot or permitted runtime
catalog target was supplied to this executor. IDs, active versions, activation
scope/method/payload/source links and revocation/read-fence evidence are UNKNOWN.
Missing transfer lengths, 4D/4H strength and other required conditions remain
unconfirmed; a real row alone would not authorize a complete bid assessment.

## Installation and closure gates

Only the single integrator applies this additive patch and resolves entry
routing. Before closing the developed-to-working gap, require:
1. Current source/main and file ownership reconciliation, with prior versions
   retained by hash/date and an explicit rollback.
2. A real permitted server reader/readback establishing authoritative profile,
   rule/knowledge/runtime IDs, scope, method and accepted compiled/source binding.
3. Reviewed executable predicates and unsupported/unknown behavior; generated
   flags, UUIDs, fixtures or source-document self-claims do not supply authority.
4. Installed entrypoint/version readback and an actual authorized end-to-end
   response. Missing, revoked or changed binding must withhold all old output.
5. Independent acceptance at the required assurance level. Unit/fixture PASS is
   not a production, learner authorization or positive BMLReady acceptance.

Until then this target remains PREPARED_NOT_INSTALLED and FULL_CALL remains
ABSTAIN. The task may report that exact blocker; it must not mark the whole
working path complete.

Rollback before deployment: do not apply the patch. For a later reviewed
installation, the single integrator restores its own exact pre-update snapshot
or removes only the five exact files after proving they are unchanged. The
local history/restore proof covers this executor's prepared sources/artifacts,
not a deployed server or database. Do not overwrite a changed integrator file.
