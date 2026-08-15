# Club Operations migration plan

Status: candidate architecture on `main`; production remains unchanged on schema 0001–0019 until explicit promotion.

## Implemented candidate sequence

1. `0020_club_operations_core` — membership, contact methods/preferences, services, price versions, packages/entitlements, club events and bookings.
2. `0021_club_financial_ledger` — charges, payments, payment allocations, adjustments, accounting-document references and a dedicated finance capability.
3. `0022_club_communications_admin` — communication/message/delivery, campaigns, recipients, admin tasks and task-state history.
4. `0023_club_state_order_hardening` — deterministic ordering of booking/task state events when timestamps coincide.
5. `0024_club_runtime_financial_hardening` — narrower runtime permissions, bounded payment allocations and stronger financial scope validation.
6. `0025_club_integrity_hardening` — entitlement consumption/reversal guards, separation of true member account balance from allocation reconciliation, exact adjustment reversals, delivery chronology/channel/explicit-denial checks, and trusted entitlement-grant permissions.
7. `0026_club_semantic_integrity` — non-overlapping active commercial versions, package-to-entitlement semantic checks, service/price consistency, active-contact delivery routing, one preferred active contact per channel, and one specialized Session/Tournament reference per ClubEvent.

## Verification gates

- `database/tests/011_club_operations.sql` — end-to-end Club Operations and capability boundaries.
- `database/tests/012_club_integrity_hardening.sql` — adversarial financial, entitlement and communication integrity cases.
- `database/tests/013_club_semantic_integrity.sql` — commercial-version, package, price/service, contact and event semantic invariants.
- PostgreSQL 18 clean install, all legacy invariant tests, idempotence, immutable migration checksum guard and migration registry must all pass before promotion.

## Promotion boundary

Do not import real members or financial facts before the candidate schema is promoted and separately verified in production. Promotion must use the existing `database-production` path; migrations 0001–0019 remain immutable.

## Next layers after schema promotion

1. AuthIdentity and object-level authorization.
2. Controlled pilot import of the first real Person/Student/ClubMember records with reconciliation.
3. Knowledge/Canon ingestion and visibility policy.
4. Member API and minimal Club Window.
5. Communication adapters and administrative UI.
6. Backup/restore, privacy/security and load gates before broad rollout.
