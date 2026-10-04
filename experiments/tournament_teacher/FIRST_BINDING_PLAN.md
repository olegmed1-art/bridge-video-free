# First working request-driven teacher binding

Status: test-branch implementation and disposable rehearsal; NO production write,
merge, deployment, approval, activation or new service is authorized by this file.

## Target and exact meaning

Use the existing Vercel `app:app` endpoint
`POST /v1/ai/positions/{position_id}/teacher-evidence`, with its existing API-token
authentication. A manual authenticated request is the caller. No scheduled worker,
queue drain, new endpoint, always-on server or new key is needed. The known API
production source is `1440920191e1778fb9a9ba24e6701937a1a7459c`; runtime caller
availability has not been proven. Historical BEN evidence and BEN_DEFAULT are not
canon bindings. This implementation does not depend on that missing worker.

Only the confirmed shape meaning of 3H and 3S after exactly 1NT,PASS is included:
5431, singleton in the named major, minors 5-4 in either orientation, FG.
The answer assesses this meaning and always has action=null. It does not claim
that the bid is permissible on points, uniquely preferred, or a complete strategy.
No points method, point threshold, alert rule or priority is invented. All other
rules, DRAFTs and unresolved questions remain outside this activation scope.

Profile: `SCHOOL_TOURNAMENT_CURRENT_V1`.
Scope: `school-tournament-1nt-shape-assessment-v1`.
Method/version: `tour-1nt-shape-assessment-v1`.

| Rule key | Decision | Canonical payload SHA-256 |
|---|---|---|
| RULE-TOUR-1NT-RSP-3H | TDEC-20261003-002 | 4127a40bfd62d7fa9d5d5b6e1f5b72b3c751881f2765cef5f690265382dcdb43 |
| RULE-TOUR-1NT-RSP-3S | TDEC-20261003-002 | 2bb321fc675343a8dd3228cd06497d54ba66727be1a104512808338d9ffd2989 |

`formal_package.py` emits the exact candidate payloads and original source/teacher
wording, source links, sheet rows 284/285, statuses and decision links. These hashes
identify reviewed rule payloads, not the bytes of the original PDF. Each payload
maps to one real knowledge_item, version_no=1, knowledge_version_source, and
bidding.rule in the verified school. UUIDs are allocated by the database and
returned privately. No synthetic UUID may become a production identity.

## Executable request and expected response

The position must already exist in `ai.decision_position`, belong to the verified
active school and contain COMPLETE/BIDDING, the exact profile, a valid own 13-card
PBN hand, explicit dealer and seat, and auction ["1NT","PASS"]. Example stored
synthetic hand `234.2.2345.23456`, dealer N, seat S has shape 3=1=4=5.
Callers cannot submit a replacement hand, school, hidden hand or output.

```json
{
  "teacher_key": "school-tournament-shape",
  "teacher_version": "tour-1nt-shape-assessment-v1",
  "teacher_system": "SCHOOL_TOURNAMENT_CURRENT_V1",
  "canon_request": {
    "task": "assess_call",
    "scope_key": "school-tournament-1nt-shape-assessment-v1",
    "version": "tour-1nt-shape-assessment-v1",
    "proposed_call": "3H"
  }
}
```

With an eligible active binding: HTTP200, SUPPORTED, action=null,
assessment_scope=shape_meaning_only, FG, literal-source explanation and actual
rule/version/runtime-activation/source UUIDs plus payload hash and decision ID.
The same hand for 3S is CONTRADICTED. Candidate-only, eligibility without activation,
revoked binding or unsupported context produces ABSTAIN. Stale scope/version is
409; injected inputs/outputs 422; nonexistent position404. No teacher_output,
final_decision, queue or decision-position write occurs. SQL uses READ ONLY.

The API reads the existing gated SQL catalog. Both compiled payload and evaluated
columns must match exact JSON types/hashes. It also verifies the active same-school
source locator and literal excerpts against the knowledge-version source link.
L1 remains unchanged. Source or binding mismatch fails closed.

## Ordered initialization; independent review points

1. Resolve the private existing source UUID in the verified school for SRC-0096's
   exact canonical locator and verify the source/version evidence. Do not infer
   identity from a display label. The initializer requires an active source and
   school, locks them for the candidate transaction, and refuses missing/mismatched
   identity. It creates no source. If no verified source exists, stop and prepare
   a separate source-registration action; do not silently expand this import.
2. After separately reviewed source registration if required, execute only the reviewed candidate action below. It creates no tests, approval,
   activation, position or deployed code and is not a working-production claim.
3. Separately evaluate the two stored candidates with positive, negative, boundary,
   hidden-information and interference fixtures; persist actual outcomes. Review
   source and this narrow pedagogical scope. Only then consider changing the two
   versions to school_canon/reviewed and rules to validated. Passing tests alone
   does not perform those transitions. Never use the rehearsal's test-only approval
   as production provenance or activate through the empty video source policy.
4. Separately review application deployment and activation. The new route branch
   is inert without matching active SQL bindings. Deployment must be reviewed on
   its exact code SHA; activation must name exactly two versions/rules, one scope,
   authority lane school_canon and matching effective intervals, with real approval
   provenance. The normal SQL gates, row locks and READ COMMITTED requirement stay.
   No L1 gate bypass or mass status changes are included.
5. Use an existing reviewed position or obtain separate authorization for a single
   clearly synthetic canary position. Its creation is not part of candidate import.
   Execute the request above using existing authorized credentials, verify response
   bindings/explanation and absence of side effects. No replay or queued jobs.

## Single next production action proposed for review

Action ID: **REGISTER_SRC0096_REFERENCE_V1**. Not executed.

The bounded read-only lookup on 2026-10-04 found no source identity in the
verified school by exact Drive file ID, SRC-0096 title, or associated asset locator.
It also found no collision for the two rule/knowledge keys. These checks do not
prove that differently labeled content cannot exist; recheck before any write.

Review scope: one transaction creating exactly one public.source reference row
in the privately verified active school. Values: source_type=document,
title=SRC-0096, canonical_locator=https://drive.google.com/file/d/1HkVff4iH2e3HT5kwblvd3mY8TUQPR6jf/view,
status=active. Keep author_owner, source_date, trust_class and rights_notes NULL;
do not infer publication rights or canon authority. No file upload or copy.
Use the database-generated UUID and return it privately. No personal identifiers
belong in this public plan. Active means the reference exists, not canon approval.

Before the insert, lock the verified school row FOR UPDATE and recheck its active
state, exact source locator/file ID/title and associated asset locators. Any match,
ambiguous identity or changed target aborts for reconciliation. Validate one new
reference and zero rule/version/test/activation changes before commit. Failure
rolls back the transaction. After commit, withdrawal requires separate review to
retire only this reference if it remains unreferenced; preserve history.

This action creates no rule, version, activation, approval, position, service,
resource, key or grant. It does not merge or deploy code. Candidate import below
is a subsequent separately reviewed action, not part of this proposal.

## Subsequent candidate action (not the next production action)

Action ID: **CANDIDATE_IMPORT_TWO_SHAPE_RULES_V1**.
Implementation: `first_init.initialize_candidates(connection, verified_school_id,
verified_source_id)` using the exact reviewed package/code SHA.

Preconditions: private school and source IDs resolved and verified; source locator
and active states match; both rule keys and knowledge stable keys absent; source
payload hashes above approved for candidate storage; target/schema fingerprint
rechecked. The required source is absent under the checked identifiers; registration and verification must precede this action.

One transaction; exact intended new rows:

- 2 public.knowledge_item (candidate);
- 2 public.knowledge_version (version1, research_candidate, unreviewed, candidate);
- 2 public.knowledge_version_source (existing source, literal locators);
- 2 bidding.rule (candidate, pinned formal assessment payloads);
- 1 bidding.ingestion_run and 2 append-only bidding.ingestion_event.

No other existing rows are changed except closing that newly created ingestion run.
Zero source creation, tests, activation rows, approvals, API evidence, positions,
permission/configuration changes, merge or deployment. Duplicates/mismatches abort.
This scope is a proposal for review; no production execution has occurred.

## Exact rollback boundaries

Candidate transaction failure: rollback all eleven rows atomically.
After committed candidate import: preserve source/version/rule/journal history;
on a separately reviewed withdrawal transaction, require no activation referencing
the two rules, mark only those rules retired, and append an owned-batch retirement
run with two events. Do not delete sources, versions, tests or BEN history.

After a later authorized activation: one transaction closes/revokes only the two
recorded runtime_activation UUIDs, then the matching two canon_activation UUIDs;
append one rollback ingestion run and two events with exact affected bindings.
Retain rules, versions, tests, approval provenance and revoked rows. Check the
SQL active catalog returns neither rule and the same API request now ABSTAINs.
The baseline has no previous tournament activation to restore. Do not restore a
fabricated prior version or substitute L1. Code rollback is separate: revert the
new route branch or restore the reverified prior Vercel deployment; reverting code
alone does not revoke database activation. No worker restart is involved.

## Evidence and limits

The new CI executes `first_init` against a fresh disposable PostgreSQL18 with the
unchanged repository migrations and the known 0200 checksum. It uses a real
application-principal SQL connection through the existing ASGI route and authentication
dependency (auth success alone is stubbed). It tests candidate→eligible→active→revoked
responses, wrong input/version/scope, missing position, source tampering and malformed
source locators, and verifies zero teacher-output writes and retained test history.
Artifacts contain only synthetic/redacted identities. Tests from5945eaeb that were
unchanged are retained as prior evidence instead of rerunning the entire old suite.
Success establishes this limited executable path; live deployment and production
initialization still require the separate reviews above.


## Rehearsal correction and capability evidence

Run 37192128465 on c789e8b6 reported success incorrectly: tee masked a Python
failure before the first HTTP assertion. That run is NOT end-to-end evidence.
The workflow now uses explicit bash, separate commands and a parsed PASS check;
preflight rejects the former masking pipeline.

The failed fixture used bridge_school_app, which has no ai schema/table access.
Read-only privilege probes confirmed the deployed API's distinct existing
bridge_school_app_principal has ai USAGE and decision_position SELECT, as well as
school, source and knowledge_version_source SELECT. The disposable bootstrap now
models those existing ai read grants on that principal only; it never changes
production privileges or repository migrations. API calls SET ROLE to the actual
principal name. This evidence does not establish live deployment of the new code.
