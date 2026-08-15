# Club Operations promotion checkpoint — 2026-08-15

Candidate schema `0020–0026` has passed the PostgreSQL 18 database CI on `main` after a second-pass integrity review.

Latest verified CI run for the semantic-integrity candidate: GitHub Actions run `31905952353`, job `95063657542`, conclusion `success`.

Verified gates:

- clean migration install on ephemeral PostgreSQL 18;
- all legacy database invariant tests;
- Club Operations tests `011_club_operations`, `012_club_integrity_hardening` and `013_club_semantic_integrity`;
- migration idempotence;
- historical migration checksum/tamper protection;
- migration registry verification;
- runtime permission boundaries for application, communication worker and finance capability;
- deterministic current-state projections;
- payment-allocation upper bounds under concurrency locks;
- member account balance includes received but unallocated payments while allocation reconciliation remains a separate view;
- exact entitlement and financial-adjustment reversal guards;
- package-to-entitlement and service-to-price consistency;
- non-overlapping active commercial version periods;
- contact/channel/explicit-denial delivery guards and coherent delivery timestamps;
- cross-school scope guards;
- historical migrations `0001–0019` remain untouched.

Production has been rechecked separately and still contains only migrations `0001–0019`; `0020–0026` are not yet applied there. No real Person/Student/ClubMember data has been imported.

Production promotion remains governed by the existing `database-production` workflow. AuthIdentity/object-level authorization is still required before Member API or Club Window access is opened.
