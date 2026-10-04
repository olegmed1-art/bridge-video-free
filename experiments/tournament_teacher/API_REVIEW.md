# Independent review of local teacher API integration

Reviewed on 2026-10-04 by a separate agent. The review did not modify the
implementation, contact a database or deployed service, or activate a target.
This document is the only file added by this integration-review pass.

## Result and verification

No unresolved defect was identified in the reviewed intended offline path.
The final current-code integration suite passed **59 tests**:

```sh
python -m pytest -q experiments/tournament_teacher/test_api_integration.py
```

The run emitted one Starlette/httpx deprecation warning, without a test failure.
The suite exercises the existing root `app:app` route through ASGI HTTP,
including the 26 scenario groups, default-disabled behavior, authentication,
source/profile/version mismatches, input identity, snapshot digest, output
injection, legacy writer behavior with a transaction double, and finalizer
refusal. Database and post-startup socket connection sentinels remain enabled.

## Resolved findings

The initial dispatch logic allowed an envelope with the reserved test teacher
version but another teacher key, without `test_*` fields, to enter the legacy
database path. CamelCase `testRequest` / `testSourceVersion` fields were also
silently discarded by model parsing and could enter that path. Both cases were
reproduced using a connection function that raised immediately; no database was
accessed.

The corrected model retains extra fields for dispatch inspection. Reserved test
key/version prefixes and recognized test-like extra names enter the guarded
offline branch. The adapter rejects extra fields rather than interpreting
aliases. Ordinary legacy extras remain ignored by the existing SQL contract.

The reviewer independently reran the two original counterexamples with the
target both absent and installed. All four calls refused before the connection
sentinel: disabled requests returned 404; enabled requests returned 409 for the
incomplete version-tagged envelope and 422 for unrecognized extra fields.

The harness and integration fixture now blank ambient API and Vercel OIDC token
variables. Existing middleware therefore cannot inject an inherited OIDC token
while these local tests run. This was verified by reading the changes, without
inspecting any credential value.

## Boundaries confirmed by review

- The normal entrypoint does not install the opt-in target. Request flags and
  environment variables cannot enable it; an exact in-process target instance
  is required.
- The existing authentication dependency remains mounted on the original route.
  The harness overrides auth success only locally; tests also exercise refusal
  through the real dependency.
- The adapter checks the requested source/profile/teacher version and rules
  digest. UUID5 input identities include the complete serialized request and
  source version. Rule UUIDs are explicitly synthetic, with null database IDs.
- Caller-supplied generated output is rejected. Successful offline responses
  report no persistence, queueing, finalization, or formal database activation.
- The normal SQL writer, finalizer, production profile gate, and original L1
  registry are not replaced by the offline consumer.

## Limits

This is an I1 separate same-model review plus local integration tests, not an
independent verification of live canonical sources or a deployed application.
The concrete source target is root `app:app`; the Docker target using
`bridge_school_api.main:app` must not be conflated with it. No deployed caller,
deployed SHA, authenticated production request trace, or production database
binding has been established. The manifest and integration notes accurately
retain that boundary.

The dispatch checks fence the documented reserved markers and tested aliases;
they do not infer intent from every arbitrary malformed string. Unmarked
ordinary teacher evidence retains the existing legacy contract. Source version
identifies the pinned rule snapshot, not a verified deployment identity.

Passing local HTTP checks establishes an executable isolated integration with
the existing route. It does not establish production readiness, complete bidding
strategy, or permission to deploy or activate tournament rules.

## Follow-up: cross-platform snapshot binding

The initial raw-byte digest described a Windows CRLF working copy, while Git
stores the same snapshot with LF newlines. The corrected adapter hashes UTF-8
text after universal-newline normalization, matching the consumer's file-reading
semantics. The reviewer independently confirmed that the normalized working-copy
digest and the actual Git blob digest both equal the new pin:
`45081a9d6ceb60dd7facf58bbbba83296fcfe9eace71824b5d23939e912b951d`.
The regenerated source version and synthetic UUID bindings change consistently;
no bidding predicates or canonical rule content changed.

The reviewer independently reran the final API suite: **64 tests passed**, with
the same non-failing Starlette/httpx deprecation warning. Four new HTTP cases
verify that LF and CRLF snapshots are accepted and that adding a space to either
snapshot is still rejected with 409. Newline normalization does not disable
content integrity checks.

Review also identified that decoding a corrupted non-UTF-8 snapshot could raise
an uncaught `UnicodeDecodeError`. The implementation now catches `UnicodeError`,
and the added HTTP test confirms a structured 503
`TEST_RULE_SNAPSHOT_UNAVAILABLE` refusal. No unresolved defect was identified in
this follow-up. These are local verification results; publication still requires
the new exact commit's CI result and does not establish a deployed consumer.
