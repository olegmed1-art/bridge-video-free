# Club Operations migration plan

Status: candidate architecture on `main`; production remains unchanged on schema `0001–0019` until explicit controlled promotion.

## Implemented candidate sequence

1. `0020_club_operations_core` — membership, contacts/preferences, services/prices, packages/entitlements, events/bookings.
2. `0021_club_financial_ledger` — charges, payments, allocations, adjustments, accounting-document references and finance capability.
3. `0022_club_communications_admin` — communications/messages/deliveries, campaigns, recipients, admin tasks and task state history.
4. `0023_club_state_order_hardening` — deterministic booking/task current-state ordering.
5. `0024_club_runtime_financial_hardening` — narrower runtime permissions and bounded allocation scope.
6. `0025_club_integrity_hardening` — entitlement/reversal integrity, true account balance vs allocation reconciliation, adjustment reversal, delivery chronology/channel/denial checks.
7. `0026_club_semantic_integrity` — commercial-version overlap guards, package/service semantics, price/service consistency, preferred contact and event specialization guards.
8. `0027_club_correction_semantics` — entitlement correction after expiry plus append-only `payment_allocation_reversal`.
9. `0028_club_effective_allocation_guard` — allocation bounds use only non-reversed effective allocations.
10. `0029_club_package_acquisition` — person-specific `person_package_grant`, package prices and exact package-acquisition links for entitlements/charges.
11. `0030_club_commercial_immutability` — runtime cannot rewrite historical commercial terms/prices/rules; changes require new versions.
12. `0031_club_payment_refunds` — append-only cash refund/reversal facts, refund-aware allocation capacity and net-cash balance projections.
13. `0032_club_package_snapshot_integrity` — freeze acquired package rule snapshots and bind recorded acquisition price to later charge provenance.
14. `0033_club_membership_state_history` — append-only membership lifecycle history captured automatically from the existing status-update contract.
15. `0034_club_communication_identity_hardening` — immutable historical communication/campaign/task identities and campaign content lock after leaving draft.
16. `0035_club_commercial_provenance_time` — charge/grant provenance must reference a non-candidate commercial version effective at the business timestamp.
17. `0036_club_historical_boundary_integrity` — business-time historical imports, explicit closure boundaries and protection against retroactively invalidating already-recorded usage/delivery/commercial provenance.
18. `0037_club_charge_ledger_provenance_integrity` — unambiguous charge origin, Booking/Service consistency and PaymentAllocation business chronology.

## Verification gates

- `database/tests/011_club_operations.sql`
- `012_club_integrity_hardening.sql`
- `013_club_semantic_integrity.sql`
- `014_club_correction_semantics.sql`
- `015_club_package_acquisition.sql`
- `016_club_commercial_immutability.sql`
- `017_club_payment_refunds.sql`
- `018_club_package_snapshot_integrity.sql`
- `019_club_membership_state_history.sql`
- `020_club_communication_identity_hardening.sql`
- `021_club_commercial_provenance_time.sql`
- `022_club_historical_boundary_integrity.sql`
- `023_club_charge_ledger_provenance_integrity.sql`
- all legacy database invariant tests.

Required CI gates remain: PostgreSQL 18 clean install, runtime DSN contract tests, all invariant tests, migration idempotence, immutable migration checksum/tamper guard and migration registry verification.

Seventh-pass candidate CI: run `31910012398`, `success`.
Latest post-merge `main` database CI for the `0020–0037` candidate: run `31910114003`, `success`.

## Production promotion boundary

Production has been directly rechecked and remains at `0001–0019`; no Club Operations tables have been promoted. Do not import real members, financial facts or auth identities before controlled promotion and separate post-promotion verification.

Production promotion remains blocked until:

1. a recoverable Neon checkpoint/branch or equivalent restore point is recorded;
2. the hardened manual production workflow is deliberately installed on `database-production` without simultaneously importing the whole diverged `main` branch;
3. the production branch/release path is protected or an equivalent enforced rule is established;
4. production is rechecked immediately before migration and still reports `0019` with healthy signals;
5. only reviewed database/release files are promoted;
6. migration is dispatched manually and migration registry/health are verified afterward.

## Next layers after schema promotion

1. AuthIdentity and object-level authorization/RLS or equivalent fail-closed isolation.
2. Actor/audit context for sensitive user/admin operations.
3. Controlled pilot import of Person/Student/ClubMember records with reconciliation.
4. Knowledge/Canon ingestion and visibility policy.
5. Member API and minimal Club Window.
6. Communication adapters and administrative UI.
7. Approved policies for `ContactPreference=unknown`, event capacity/waitlist, discounts/overrides and any recurring subscription semantics.
8. Backup/restore, privacy/retention, security and load/cost gates before broad rollout.
