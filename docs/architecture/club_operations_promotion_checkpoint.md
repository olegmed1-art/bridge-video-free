# Club Operations promotion checkpoint — 2026-08-15

Candidate schema 0020–0024 has passed the PostgreSQL 18 database CI on `main` before production promotion.

Verified gates:

- clean migration install on ephemeral PostgreSQL 18;
- Club Operations invariant tests;
- migration idempotence;
- historical migration checksum protection;
- migration registry verification;
- runtime permission boundaries for application, communication worker and finance capability;
- deterministic current-state projections;
- payment-allocation upper-bound guard;
- cross-school scope guards.

Production promotion remains governed by the existing `database-production` workflow and does not edit migrations 0001–0019.
