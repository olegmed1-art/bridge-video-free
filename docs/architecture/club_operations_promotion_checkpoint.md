# Club Operations promotion checkpoint — 2026-08-15

Candidate schema `0020–0028` has passed the PostgreSQL 18 database CI on `main` after three integrity-review passes.

Latest verified post-merge CI for the correction-semantics candidate: GitHub Actions run `31906622297`, conclusion `success`.

Verified gates:

- clean migration install on ephemeral PostgreSQL 18;
- all legacy database invariant tests;
- Club Operations tests `011–014`;
- migration idempotence;
- historical migration checksum/tamper protection;
- migration registry verification;
- runtime permission boundaries for application, communication worker and finance capability;
- deterministic current-state projections;
- payment-allocation upper bounds under concurrency locks;
- append-only payment-allocation reversal and corrected re-allocation semantics;
- member account balance includes received but unallocated payments while allocation reconciliation remains a separate view;
- exact entitlement and financial-adjustment reversal guards;
- entitlement corrections remain possible after entitlement expiry while fresh expired consumption remains blocked;
- package-to-entitlement and service-to-price consistency at the current catalog-definition level;
- non-overlapping active commercial version periods;
- contact/channel/explicit-denial delivery guards and coherent delivery timestamps;
- cross-school scope guards;
- historical production migrations `0001–0019` remain untouched.

Production has been rechecked separately and still contains only migrations `0001–0019`; `0020–0028` are not applied there. No real Person/Student/ClubMember data has been imported.

## Promotion is currently BLOCKED

The block is intentional. The GitHub `database-production` branch is currently unprotected, and the workflow currently installed on that branch auto-runs on qualifying pushes and can apply migrations to Neon after preflight.

A hardened release workflow has been merged to `main`: manual dispatch only, exact `database-production` ref, explicit `MIGRATE` confirmation, pre-migration fingerprint and `database-production` environment boundary. It is not yet active on the production branch.

Before any `0020+` promotion:

1. establish a recoverable Neon checkpoint/branch or equivalent restore point and record it;
2. deliberately install/activate the hardened production release workflow on `database-production` without promoting Club Operations migrations in the same uncontrolled step;
3. protect the `database-production` branch or establish an equivalent enforced repository rule;
4. re-run the production fingerprint/health check and verify it still shows `0019` before migration;
5. promote only the reviewed database files/required workflow changes, not an indiscriminate merge of the heavily diverged `main` branch;
6. run production migration manually and verify migration registry plus operational health afterward.

Even after DB promotion, AuthIdentity/object-level authorization, audit actor context, controlled Person import and Knowledge visibility/ingestion remain required before Member API, Club Window or Bridge Coach access is opened to real members.
