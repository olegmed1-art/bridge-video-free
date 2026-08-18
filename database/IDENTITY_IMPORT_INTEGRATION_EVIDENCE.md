# Identity Import — isolated Neon integration evidence

Date: 2026-08-18

## Status

`0100_identity_import_staging` and `0101_identity_import_schema_and_readiness_hardening` are **IMPLEMENTED + TESTED**. They are not yet promoted to production Neon and therefore are not OPERATIONAL for real club members.

## Test environment

- Neon project: `bridge-school-core`
- Temporary branch: `identity-import-pilot-20260818`
- Branch ID: `br-tiny-hat-b1wqfdlt`
- Parent production state at test start: 52 migrations, latest `0052_deal_stable_key_nonempty_constraint`
- Production was not modified by this test.

The temporary Neon branch was deleted after successful verification.

## What was verified

The isolated branch was advanced to the Identity Import schema and then exercised with synthetic identities only.

Verified behavior:

1. Raw/unverified import evidence lives under owner-only schema `identity_staging`; no `public.identity_import_item` exists.
2. Database-generated SHA-256 evidence hash is derived from the stored raw payload rather than trusted from the importer.
3. A staged item can link to an existing Person only through an active canonical `EntityResolutionDecision`.
4. `create_new_person` is only an explicit reviewed intent in staging; it does not create Person, Student, ClubMembership or AuthIdentity by itself.
5. An item cannot become `ready` without a current resolvable action.
6. A batch cannot become `ready` while any item is unresolved or unsafe.
7. A later append-only `defer` action invalidates future-apply eligibility without rewriting the prior ready state or earlier decision history.
8. Staging rows and workflow history are append-only; direct mutation is rejected.
9. Reader/app/worker/health/finance/member/member-principal/auth-gateway roles have no `USAGE` on `identity_staging` and cannot read or insert staging items.
10. No Identity Import apply function exists in this layer. Real operational identity creation remains a separate future gate.

## Synthetic end-to-end observations

The successful synthetic batch exercised two paths:

- existing source identity -> canonical resolution -> `link_existing_person` -> item `ready`;
- new source record -> explicit `create_new_person` intent -> item `ready`.

The batch became eligible for a future apply step only after both items were safe. A later `defer` appended to the new-person item immediately changed the batch projection back to ineligible. A second unresolved synthetic batch remained `staged` and rejected an attempted `ready` transition.

## Production safety read-back

After the integration test, production Neon was re-read and remained at:

- migration count: 52;
- latest migration: `0052_deal_stable_key_nonempty_constraint`;
- `identity_staging.identity_import_item`: absent;
- `public.identity_import_item`: absent.

No real Person/Student/ClubMembership/AuthIdentity was imported.

## Process defect found and corrected

Temporary PR #142 attempted to reuse the production `NEON_DATABASE_URL` credential while replacing only the hostname with the child-branch endpoint. Neon correctly rejected that assumption because child-branch connection credentials are branch-specific.

This failed attempt changed neither production nor the temporary database schema and exposed no password in logs.

**Permanent process rule:** never derive a Neon child-branch DSN by swapping the hostname of a production DSN. Integration tests must obtain a branch-specific connection credential from Neon for that exact branch, or use the Neon connector directly.

PR #142 is retained closed as failure evidence and was never merged.

## Next Evidence Gate

The next identity milestone is **controlled apply**, not more staging hardening.

Before any real import, the system still needs a separately reviewed apply contract that defines exactly which approved staging action may materialize which operational objects. It must remain fail-closed, auditable and idempotent, and it must not infer Student, ClubMembership or AuthIdentity merely from a name or from `create_new_person` intent.
