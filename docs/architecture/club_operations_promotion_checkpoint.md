# Club Operations promotion checkpoint — 2026-08-15

Candidate schema `0020–0035` has passed the PostgreSQL 18 database CI on `main` after six integrity-review passes.

Latest verified post-merge CI: GitHub Actions run `31907731704`, conclusion `success`.

Verified gates:

- clean migration install on ephemeral PostgreSQL 18;
- all legacy database invariant tests;
- Club Operations tests `011–021`;
- runtime DSN contract regression tests;
- migration idempotence;
- historical migration checksum/tamper protection;
- migration registry verification;
- runtime permission boundaries for application, communication worker and finance capability;
- deterministic current-state projections;
- payment allocation bounds under concurrency locks and after append-only allocation reversals;
- append-only cash refund/reversal facts and refund-aware allocation capacity;
- member account balance uses net cash after refunds while allocation reconciliation remains separate;
- exact entitlement, allocation, refund and financial-adjustment correction semantics;
- person-specific acquired package instances and package-price provenance;
- acquired package rule snapshots freeze after acquisition;
- runtime commercial version content is immutable; changes require new versions;
- service/package price provenance is valid at the charge/grant timestamp and does not reference candidate versions;
- append-only ClubMembership lifecycle history captured automatically from status updates;
- runtime cannot directly forge membership history rows;
- campaign content freezes after first leaving draft and recipient/context identities cannot be rewritten broadly;
- contact/channel/explicit-denial delivery guards and coherent delivery timestamps;
- cross-school scope guards;
- historical production migrations `0001–0019` remain untouched.

Production has been directly rechecked and still contains only migrations `0001–0019`; `0020–0035` are not applied there. Production does not contain Club Operations tables such as `club_membership`, `club_payment_refund` or `person_package_grant`. Operational health remains `ok` with 0 critical, 0 warning and 15 ok signals at the repeat check.

## Promotion is currently BLOCKED

The block is intentional. GitHub branch `database-production` is currently unprotected. The workflow currently installed on that branch still triggers automatically on qualifying pushes and can apply migrations to Neon after preflight.

A hardened release workflow has been merged to `main`: manual dispatch only, exact `database-production` ref, explicit `MIGRATE` confirmation, pre-migration fingerprint and `database-production` environment boundary. It is not yet active on the actual production branch.

Before any `0020+` promotion:

1. establish and record a recoverable Neon checkpoint/branch or equivalent restore point;
2. deliberately install/activate the hardened production release workflow on `database-production` without promoting the Club Operations migrations in that same uncontrolled step;
3. protect `database-production` or establish an equivalent enforced repository rule;
4. re-run production fingerprint/health and verify it still reports `0019` immediately before migration;
5. promote only reviewed database/release files, not an indiscriminate merge of the heavily diverged `main` branch;
6. dispatch the migration manually and verify migration registry, runtime principals and operational health afterward.

Even after DB promotion, AuthIdentity/object-level authorization, actor/audit context, controlled Person import and Knowledge visibility/ingestion remain required before Member API, Club Window or Bridge Coach access is opened to real members.

The following behaviors remain explicit policy decisions and are not database-guessed: `ContactPreference=unknown`, event capacity/waitlist rules, discount/override amount rules, and any recurring-subscription billing semantics.
