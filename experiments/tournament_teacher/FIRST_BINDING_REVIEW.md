# Independent review: request-driven, SQL-gated shape assessment

Reviewed on 2026-10-04 by a separate agent under the ASSURED review requirement.
This document is the reviewer's only edit for this stage. No production tools,
database connections, deployment actions, or workflow launches were used.

## Conclusion and scope

No unresolved blocking defect was identified after the two fixes below. The
new request branch is narrowly an assessment of 3H or 3S after 1NT-PASS. It
obtains the acting hand, school, auction and seat from the stored position;
callers cannot override them. Its connection starts a READ ONLY transaction,
and runtime rules come from the existing gated school-catalog SQL function.
There is no write, scheduler, worker, finalizer change, L1 extension, or unique
call recommendation. Every assessment retains `action=null`.

The meaning checked is exactly a 5431 distribution with the named major
singleton and minors of lengths five and four. Points and selection priority
remain unresolved; matching this shape does not establish sufficient strength
for a game force or the uniquely appropriate bid. The response states the
shape-only scope rather than inventing a point threshold.

## Findings fixed during review

1. **JSON boolean/number substitutions bypassed column equality.** Python's
   equality considers `true` equal to `1`. With the pinned compiled payload
   unchanged, altering the separate catalog shape column from 1 to true still
   returned SUPPORTED. The reviewer reproduced this locally. Column bindings
   now compare canonical JSON digests, retaining JSON type distinctions. Added
   regressions cover both shape and action metadata substitutions.

2. **Malformed source-locator JSON could crash instead of abstaining.** The
   source-locator column permits JSON values other than objects; calling `.get`
   unconditionally could raise an exception. The source check now requires an
   object. Five independent connection-mocked probes verified null, array,
   string, incomplete object, and correct locator behavior, without opening a
   database connection. Only the correct locator produced SUPPORTED; all four
   malformed/incomplete variants abstained. Every probe also verified that the
   first SQL command requests a read-only transaction and action remains null.

## Verification performed

The reviewer's final affected-test command passed **178 tests**, with one
non-failing Starlette/httpx deprecation warning:

```sh
python -m pytest -q \
  experiments/tournament_teacher/test_formal_teacher.py \
  experiments/tournament_teacher/test_api_integration.py \
  tests/test_l1_canonical_registry.py tests/test_l1_canonical_runtime.py \
  tests/test_knowledge_read_api.py tests/test_ai_api_routes.py \
  tests/test_ai_final_decision_contract.py
```

The new evaluator tests enumerate all 560 suit distributions for each call
against an explicit set of four allowed ordered shapes. Other tests cover
incomplete/public context, inactive school, malformed hands, no or duplicate
bindings, wrong school/scope/version/payload/columns, and input envelopes that
must not reach the legacy writer. Existing endpoint authentication remains in
place. Payload hashes match the two-rule compiler output.

Static review confirmed that the runtime source check additionally requires one
active, same-school source with matching canonical locator and literal source
and teacher excerpts. Successful responses include the actual database source
UUID separately from the canonical source label. This checks database binding;
it does not independently verify the original document's authenticity.

## Import and rehearsal boundaries

`initialize_candidates` locks the supplied source and school, requires matching
active identities and absent rule/item keys, and atomically creates only two
research candidates with unreviewed status and ingestion history. It does not
create a source, approve a version, create a position, or activate rules.
An active locator/status match alone is not a substitute for externally
verifying the intended production school and source UUID before any future
production import.

The separate disposable rehearsal creates synthetic identities, executes
candidate evaluator cases, records their SQL evidence, and simulates review and
activation only in its local database. It exercises the real app-role HTTP path
before activation, after activation, after source mismatch/restoration, and
after revocation. Revocation owns only the four created activation rows and
appends audit events while retaining rules and test history. The reviewer
checked this code but did not execute its SQL lifecycle.

## Assurance limits and publication boundary

This is an I1 independent same-model review with finite shape tests and mocked
source-binding probes. It is not proof of concurrent database behavior, live
source truth, production authorization, or deployed caller identity. The exact
published commit still requires successful disposable PostgreSQL CI to establish
the first-binding lifecycle, SQL permissions, runtime gates, HTTP readback, and
rollback evidence.

The initially proposed two-candidate production import is blocked until a source
identity is verified. The implementer's subsequent targeted source lookups
returned no verified match, so the next proposed production action is source
registration only, separately scoped and authorized. Candidate import must wait
for the resulting verified source binding. No production source UUID is invented
here. No production activation, deployment, approval, teacher-output write, or
production replay follows from this review or a passing local rehearsal.

## Follow-up: false-green CI and principal fixture correction

The implementer's investigation of commit `c789e8b6`, CI run
[37192128465](https://github.com/olegmed1-art/bridge-video-free/actions/runs/37192128465),
found a first-binding permission failure masked by the `python ... | tee ...`
pipeline. That green job is **not evidence of successful SQL initialization or
HTTP binding**. The earlier local unit-test results remain valid within their
stated scope; they did not establish the SQL lifecycle.

The reviewer inspected the corrective workflow diff. The step now explicitly
uses Bash, writes program output directly to the artifact without a pipeline,
then parses that file and requires JSON `status == 'PASS'`. Python failure,
empty or malformed output, and a non-PASS status therefore cannot reach the
successful artifact-display step under this shell's fail-fast behavior.
Preflight now checks the explicit shell, removal of the tee pipeline, and PASS
validation. A new exact-commit CI run is still required.
The reviewer independently reran preflight and the affected test command after
these corrections: preflight passed and **178 tests passed**. This repeat does
not execute the PostgreSQL rehearsal.

The original local fixture used `bridge_school_app`, which lacks the `ai` schema
read capability needed for stored positions. The implementer reports targeted
read-only privilege probes showing that the existing
`bridge_school_app_principal` has the required read access, while the capability
role does not. The reviewer did not repeat those external probes. The fixture
now switches to that principal, and bootstrap grants only `USAGE` on schema
`ai` and `SELECT` on `ai.decision_position` to the principal in the disposable
container. Test-owner membership permits SET ROLE with inheritance disabled.
These are local fixture grants, not repository migrations or production
permission changes.

The changes address the identified masking and fixture mismatch without
establishing a deployed caller, production initialization, or successful SQL
rehearsal. Source registration, a later candidate import, canonical activation,
and deployment remain distinct actions with separate prerequisites.
