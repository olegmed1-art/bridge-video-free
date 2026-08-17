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
19. `0038_auth_identity_actor_context` — provider-neutral AuthIdentity mapping, school/context role assignments, explicit person-to-person grants and dormant member-facing capability.
20. `0039_member_self_service_views` — fail-closed member self-service projections without broad base-table reader inheritance.
21. `0040_actor_audit_context` — protected actor/request audit facts for sensitive club/auth/finance operations and membership-history attribution.
22. `0041_actor_context_signature_hardening` — transaction/backend-bound HMAC signature so user-settable custom PostgreSQL settings cannot forge actor identity.
23. `0042_verified_audit_and_explicit_person_grants` — audit/membership attribution consumes only verified signed context; arbitrary cross-person permissions require explicit grants.
24. `0043_auth_gateway_and_school_role_scope` — ordinary member capability cannot establish arbitrary AuthIdentity context; trusted server-side auth-gateway capability is separated, and generic role checks are school-wide only.

## Verification gates

- Club Operations/Auth database tests `011–030` plus all legacy database invariant tests.
- `024_auth_identity_actor_context.sql` — AuthIdentity/role/grant/context boundary.
- `025_member_self_service_isolation.sql` — Person/school self-service isolation and message visibility.
- `026_actor_audit_context.sql` — actor-aware immutable audit attribution.
- `027_actor_context_signature_hardening.sql` — forged/malformed custom settings fail closed; signing secret is protected.
- `028_verified_audit_and_explicit_person_grants.sql` — no self wildcard permission and no false audit attribution from forged settings.
- `029_auth_gateway_role_scope.sql` — ordinary member cannot establish identity; trusted NOLOGIN gateway can; scoped role does not become school-wide.
- `030_member_security_definer_surface.sql` — reviewed SECURITY DEFINER execution-surface whitelist for member/member-server capability.

Required CI gates remain: PostgreSQL 18 clean install, runtime DSN contract tests, all invariant/adversarial tests, migration idempotence, immutable migration checksum/tamper guard and migration registry verification.

Round-8 core auth PR #94 post-merge `main` CI: run `32000018735`, `success`.
Round-8 trusted-gateway candidate CI: run `32000241026`, `success`.
Latest post-merge `main` database CI for the `0020–0043` candidate: run `32000346201`, `success`.

## Authentication boundary after round 8

The database now has the candidate structures needed to bind a verified external identity to Person and to fail closed on member reads. It still does not authenticate an external provider token itself. The trusted gateway entry point is intended to be called only by server-side code after provider verification has already succeeded.

`bridge_school_member_principal` remains NOLOGIN. No member database credential has been provisioned, no provider has been selected by the database design, and no real AuthIdentity has been imported.

Before a Member API is externally reachable, the API must use the dedicated narrow member/server boundary rather than the existing broad internal app/reader capability.

## Production promotion boundary

Production was directly rechecked on 2026-08-17 and remains at `0001–0019`; PostgreSQL is 18.4; `club_membership`, `auth_identity` and `actor_context_signing_secret` are absent. Operational health remains `ok` with critical=0, warning=0, ok=15. No Club Operations/Auth migrations have been promoted.

Do not import real members, financial facts or auth identities before controlled promotion and separate post-promotion verification.

Production promotion remains blocked until:

1. a recoverable Neon checkpoint/branch or equivalent restore point is recorded;
2. the hardened manual production workflow is deliberately installed on `database-production` without simultaneously importing the whole diverged `main` branch;
3. the production branch/release path is protected or an equivalent enforced rule is established;
4. production is rechecked immediately before migration and still reports `0019` with healthy signals;
5. only reviewed database/release files are promoted;
6. migration is dispatched manually and migration registry/permissions/health are verified afterward.

## Next layers after the round-8 candidate

1. Integrate a real external authentication verifier/gateway and map only verified provider claims to AuthIdentity; keep database credentials server-side.
2. Add guarded member/admin write functions instead of direct base-table writes.
3. Define explicit instructor/object-scoped authorization before exposing student educational context across Persons.
4. Controlled pilot import of Person/Student/ClubMember/AuthIdentity records with reconciliation.
5. Knowledge/Canon ingestion and visibility policy.
6. Member API and minimal Club Window after end-to-end auth/isolation tests.
7. Communication adapters and administrative UI.
8. Approved policies for `ContactPreference=unknown`, event capacity/waitlist, discounts/overrides and any recurring subscription semantics.
9. Backup/restore, privacy/retention, security and load/cost gates before broad rollout.
