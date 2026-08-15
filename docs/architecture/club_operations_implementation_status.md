# Club Operations implementation status

Status as of 2026-08-16 after seven integrity-review passes.

## Current candidate state

- Architecture v0.3 remains an extension of the existing School core, not a parallel member database.
- Google Drive area `Управление клубом` is a document/file layer and is not the operational source of truth.
- Candidate PostgreSQL migrations `0020–0037` are implemented on `main`.
- Production Neon has been rechecked directly and still contains only migrations `0001–0019`; no Club Operations migration has been promoted.
- Production does not contain `club_membership`, `club_payment_refund` or `person_package_grant` tables.
- Production operational health at the repeat check is `ok`: 0 critical, 0 warning, 15 ok signals.
- No real Person/Student/ClubMember import has been performed.
- Machine Knowledge/Canon remains unpopulated; Bridge Coach is not ready for autonomous member-facing teaching until controlled ingestion/visibility is implemented.

## Implemented audit corrections

### 0027–0028 — correction semantics

- entitlement usage can be reversed after entitlement expiry while fresh expired consumption remains blocked;
- mistaken `PaymentAllocation` is corrected append-only through `payment_allocation_reversal` rather than UPDATE/DELETE;
- allocation reconciliation uses only effective allocations;
- allocation upper bounds are reversal-aware.

### 0029–0030 — acquired packages and commercial immutability

- `person_package_grant` distinguishes separate acquisitions/assignments of the same catalog package;
- `package_price_version` versions package pricing;
- package-derived entitlements are tied to the exact acquisition instance;
- package charges can reference the exact acquisition and agreed package price;
- commercial version amounts/currencies/effective starts/terms/rules cannot be rewritten by runtime finance; changed commercial definitions require new versions.

### 0031–0032 — cash refunds and acquired-package snapshots

- `club_payment_refund` records actual cash refunds separately from accounting adjustments;
- refund/reversal history is append-only and cannot exceed net unallocated cash;
- `club_payment_net`, member balance and unallocated-payment projections use net cash after refunds;
- new allocations cannot exceed payment net of refunds;
- refund accounting documents can reference the exact payment/refund;
- package service-rule sets freeze after first acquisition;
- an acquisition price must have been effective at grant time;
- later package charges cannot silently switch to another price version.

### 0033–0035 — lifecycle, communication identity and provenance

- ClubMembership status changes are preserved automatically in append-only `club_membership_state_event` history;
- runtime cannot directly forge membership history rows; status updates are captured by hardened trigger helpers;
- the one-open-membership invariant is based on membership-period closure (`valid_to`) rather than mutable status;
- communication/campaign/admin identity fields cannot be rewritten through broad runtime UPDATE grants;
- campaign audience/template content is frozen after the campaign first leaves draft, even if its status later returns to draft;
- a closed communication must have a close timestamp;
- campaign recipient identity/selection is immutable and its communication link can only be assigned once;
- service/package price provenance must be effective at the charge/grant timestamp and cannot reference an unapproved `candidate` version;
- amount equality between a price version and a charge is intentionally NOT enforced because discount/override policy has not been approved and is not invented by the implementation.

### 0036–0037 — historical boundaries and ledger provenance

- historical entitlement usage is judged by the entitlement validity window at `occurred_at`, rather than by the entitlement's later current lifecycle label;
- historical message delivery may reference a now-revoked/superseded contact when `queued_at` was inside that contact's former validity window; delivery outside the window remains blocked;
- package-backed entitlement usage must also fall inside the exact acquired package grant's validity window;
- closing lifecycle states for membership, contact methods, entitlements and acquired package grants require an explicit `valid_to` boundary where the state means the period actually ended;
- `valid_to`/`effective_to` cannot later be shortened so that already-recorded entitlement usage, message delivery, package acquisition or charge provenance becomes impossible in hindsight;
- a charge cannot simultaneously claim both a direct Service and an acquired Package as its primary commercial origin;
- when a Charge cites both Booking and Service, the Service must agree with the booked ClubEvent service when that event has a service;
- `PaymentAllocation.allocated_at` cannot precede either the Payment or the Charge it connects.

## Verification

- Database tests now cover Club Operations tests `011–023` in addition to all legacy database tests.
- PR #79, #82, #83, #84 and #87 were merged only after their candidate database CI passed.
- Seventh-pass candidate CI: run `31910012398`, conclusion `success`.
- Latest post-merge database CI on `main`: run `31910114003`, conclusion `success`.
- Verified steps include PostgreSQL 18 clean install, runtime DSN regression tests, all invariant tests, migration idempotence, immutable-history checksum guard and migration registry verification.

## Important financial semantics

- `person_financial_balance` is the member account balance and uses actual net cash received after refunds, including not-yet-allocated payments.
- `person_allocated_receivable_balance` is a reconciliation view showing charges not covered by currently effective explicit allocations.
- `PaymentAllocation` explains application of cash to charges; it does not redefine whether cash was received.
- allocation correction is append-only: reverse the mistaken allocation, then append corrected allocation(s).
- cash refund is a `club_payment_refund`; a `financial_adjustment` alone does not mean money physically left the club.

## Release safety — production promotion remains BLOCKED

- GitHub branch `database-production` is currently unprotected.
- The workflow currently installed on that branch still auto-runs on qualifying pushes and can apply database migrations to Neon after preflight.
- A hardened production workflow is present on `main`: manual dispatch only, exact `database-production` ref, explicit `MIGRATE` confirmation, pre-migration fingerprint and `database-production` environment boundary.
- That hardened workflow has NOT been installed on the actual `database-production` branch.
- Therefore no `0020+` production promotion should occur until a recovery checkpoint is created, the production release boundary is deliberately hardened, and the production fingerprint is reverified at `0019` immediately before migration.
- Because `main` and `database-production` are heavily diverged, do not indiscriminately merge all of `main` into `database-production`; promote only reviewed database/release files through the controlled path.

## Remaining gates before real member use

1. AuthIdentity + object-level authorization/RLS or equivalent fail-closed data isolation.
2. Actor context/audit identity for user-initiated sensitive changes; current automatic membership events preserve chronology but do not yet know the authenticated member/admin actor.
3. Controlled Person/Student/ClubMember import and reconciliation.
4. Knowledge/Canon ingestion plus member/instructor/admin/private visibility policy.
5. Member API and Club Window only after authorization is verified.
6. Provider-neutral message-delivery transition policy before stricter state-machine enforcement.
7. Event capacity/waitlist rules and `ContactPreference=unknown` behavior remain explicit club/business/legal policy decisions and are intentionally not invented.
8. Recurring subscription/billing semantics are not yet modeled beyond acquired package/grant instances; add them only if the approved service model requires recurrence.
9. Backup/restore drill, privacy/retention policy, security review and load/cost gates before broad rollout.

## Policy intentionally unresolved rather than guessed

- `ContactPreference=denied` is a hard delivery stop in the candidate schema.
- `ContactPreference=unknown` requires an approved policy.
- discount/override rules determine whether a charge must equal, differ from or derive from a catalog price; the database currently preserves provenance without inventing that policy.
- event overbooking/waitlist behavior requires an approved club rule before database enforcement.
