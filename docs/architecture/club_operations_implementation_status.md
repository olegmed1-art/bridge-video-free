# Club Operations implementation status

Status as of 2026-08-15 after third-pass review:

- Architecture v0.3 is documented and remains an extension of the existing school core, not a parallel member database.
- Google Drive area `Управление клубом` exists as a document/file layer; it is not the operational source of truth.
- Candidate PostgreSQL migrations `0020–0028` are implemented on `main`.
- The earlier audit corrected integrity gaps in entitlement usage/reversals, member balance semantics, adjustment reversals, contact/delivery routing, commercial-version overlap, package entitlement semantics and price/service consistency.
- The third-pass audit found two further correction-history defects: entitlement usage reversal was blocked after the entitlement validity window ended, and an incorrect `PaymentAllocation` had no append-only unallocation mechanism. `0027` adds correction semantics; `0028` makes allocation upper-bound guards reversal-aware.
- `payment_allocation_reversal` now preserves the original allocation while allowing a full reversal followed by corrected allocations. Reconciliation views use only effective allocations; the member account balance still uses actual received payments.
- Database verification covers legacy invariants plus Club Operations tests `011–014`, clean PostgreSQL 18 migration, idempotence, checksum protection and registry verification.
- PR #79 passed database CI and was merged to `main`. The post-merge database CI run `31906622297` completed with `success`; all database CI steps passed.
- Production Neon was rechecked directly and remains on migrations `0001–0019`; Club Operations tables/migrations `0020–0028` have not been promoted there.
- Production operational health remains `ok` with 0 critical, 0 warning and 15 ok signals at the repeat check.
- Production currently has no real `Person` or `Student` records, so no member/student data migration has occurred.
- Machine Knowledge/Canon remains unpopulated and must be ingested under the existing authority/review rules before autonomous Bridge Coach use.
- Member authentication, object-level authorization, Member API and Club Window remain separate subsequent stages. Public/member access must not be opened before those controls are implemented and verified.

Important semantic distinctions:

- `person_financial_balance` is the member account balance and includes all received payments, including not-yet-allocated payments.
- `person_allocated_receivable_balance` is a reconciliation view showing what charges remain uncovered by currently effective explicit allocations.
- `PaymentAllocation` explains application of cash to charges; it does not redefine whether the club actually received the payment.
- An allocation correction is append-only: reverse the mistaken allocation, then append the corrected allocation(s); do not UPDATE/DELETE the historical allocation.

Release-safety finding:

- GitHub branch `database-production` is currently unprotected.
- The workflow currently present on that production branch auto-runs on a qualifying push and can apply migrations to Neon after preflight.
- A hardened manual production workflow has been implemented and tested on `main` via PR #80: manual dispatch only, exact `database-production` ref, explicit `MIGRATE` confirmation, pre-migration fingerprint and a `database-production` environment boundary.
- This workflow hardening has NOT been copied to `database-production`; therefore production promotion remains blocked until that release boundary is deliberately hardened and a recovery/checkpoint procedure is confirmed.

Remaining structural gaps before real club use:

- a person-specific acquired package/subscription instance is still missing; catalog `club_package_version` is not sufficient to distinguish two separate purchases/grants of the same package;
- package pricing and the financial link between a package acquisition, its charge and the entitlements generated from it still need a reviewed model;
- member AuthIdentity/object-level authorization and audit actor context are not implemented;
- member-facing knowledge visibility/ingestion is not implemented;
- message-delivery state transition semantics need a provider-neutral policy before they are hardened further;
- event capacity/waitlist rules and `ContactPreference=unknown` behavior remain policy decisions and are intentionally not invented.

Policy intentionally left unresolved rather than invented:

- `ContactPreference=denied` is a hard delivery stop in the candidate schema.
- The required behavior for `ContactPreference=unknown` depends on the club's approved business/legal policy and is not inferred by the implementation.
