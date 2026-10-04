# Independent review of the bounded shape pilot

Reviewed 2026-10-04 against the local implementation based on main
`1440920191e1778fb9a9ba24e6701937a1a7459c`. This is a logically separate,
same-model Red Team review (I1), not independent bridge-author approval.

## Result

No unresolved blocker found to publishing this isolated test branch and running
its disposable PostgreSQL workflow. This does not authorize or establish a
production merge, deployment, source registration, canon activation, or a live
teacher caller. Real SQL success must be established by the resulting exact-SHA
CI evidence; it was not executed by this reviewer.

## Checks and evidence

- Independently ran the scoped workflow pytest command after the review fixes:
  **130 passed**. It includes existing L1, knowledge-reader, API-route and final
  decision contract regressions. One dependency deprecation warning remains.
- Compared both complete `source_rule` objects in `package.json` with
  `experiments/tournament_teacher/rules.json` at `6f6001c0`: both are equal.
  This is historical snapshot verification, not a fresh live-source review.
- Reviewed the shape predicate against the explicit allowed SHDC shapes and
  the finite enumeration test. The meaning requires a singleton in the named
  major, the other major of length three, and minors of lengths four and five.
  The API assesses a proposed call; it does not select a bid. It leaves points
  unresolved and does not turn school points into HCP.
- Reviewed the stored-position fence, exact request/version/profile checks,
  pinned payload and catalog-column checks, active-school and source linkage,
  and existing authenticated route. Other stored hands/keys, missing source
  linkage, absent gate eligibility and mismatched context cannot produce the
  successful pilot assessment. The request branch uses a read-only transaction
  and does not write teacher output or invoke finalization.
- Reviewed loopback binding, exact Host and Origin, session nonce, bounded JSON
  input, CSP, text-only rendering and generic upstream errors. The API token
  stays in the local process. Independently exercised an actual loopback HTTP
  redirect with a synthetic token: the opener rejected HTTP 302 and the target
  received no request. The suite now also tests cross-server redirect refusal.
- Reviewed the disposable SQL rehearsal statically: 18 semantic cases feed
  eight gate suites and eight result rows; one journal records five events.
  The budget is **40 pilot rows plus one synthetic school fixture**, not 40
  total fixture rows. Reactivation creates a new activation pair per rule and
  reuses the original expiry. Old activation rows are retained and revoked.
- Reviewed the workflow's single ten-minute job, loopback PostgreSQL service,
  unchanged migration application, local principal fixture and explicit bash
  failure propagation with a JSON PASS assertion. No production connection,
  repository-secret use or deployment step was introduced in this path.

## Finding resolved during review

The initial screen accepted a positive response with missing provenance or a
wrong response scope/call. It now requires the explicit no-write/no-action
contract, exact version/profile/scope/call, one matching pinned source for a
non-abstention, and the expected canary shape/status. The live sender additionally
binds the response position and teacher key. Regression tests pass.

## Assurance limits

The finite predicate checks cover these two shape meanings only. HTTP coverage
uses the single fixed canary; its synthetic hand supports 3H and contradicts
3S. Neither result establishes point strength, bid priority, teaching benefit,
or coverage of the other tournament decisions. SQL catalog activation remains
school/scope-wide; the narrower position guard belongs to this HTTP consumer.
Other consumers and production configuration require their own preflight.

No production database, credentials, remote workflow or browser was exercised
by this reviewer. Browser QA and exact-SHA SQL CI are separate evidence. The
review records source code and local tests, not a deployed end-to-end result.

## Router isolation follow-up

The final integration restores `bridge_school_api/ai_teacher.py` to main and
changes one import in `app.py` to mount the new module's router at the existing
URL. Its request model extends the legacy model, fences canon requests into the
read-only evaluator, and delegates ordinary envelopes to the unchanged legacy
writer. The existing `require_api_token` router dependency remains attached.
Independently checked the zero legacy-file diff and reran the same suite:
**130 passed** after this adjustment.

This avoids matching the existing production BEN workflow's legacy-teacher path
filter; no production workflow or gate was disabled. Test-branch preflight is
not approval for a later main merge, whose workflows must be checked again.
The parent reported successful disposable SQL CI at the preceding `f387d100`
revision (run `37194385971`) and separate browser QA. Those reports are not
independent verification by this reviewer and do not replace exact-SHA CI for
the revised router integration.
