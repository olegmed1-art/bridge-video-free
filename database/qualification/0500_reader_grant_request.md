# Books authority reader access: separate, unexecuted request

The 0500 core migration creates five tables, three invoker functions and five triggers. It grants no new runtime access. A future Books worker would need the existing `bridge_school_worker_principal` to read four exact tables in `public`:

| Table | Proposed privilege |
| --- | --- |
| `books_authority_object` | `SELECT` |
| `books_authority_state` | `SELECT` |
| `books_authority_attempt` | `SELECT` |
| `books_authority_phase` | `SELECT` |

The proposal excludes `books_authority_state_audit`. No `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, `TRIGGER`, `REFERENCES`, `EXECUTE`, role membership or grant option is requested. It also requires an already authorised way for that principal to use schema `public`; the 0500 migration does not change schema access.

Only after separately confirming the live database, owner, principal identity, table definitions, current grants and need for these reads, the exact reversible delta would be:

```sql
BEGIN;
GRANT SELECT ON TABLE public.books_authority_object,
                      public.books_authority_state,
                      public.books_authority_attempt,
                      public.books_authority_phase
  TO bridge_school_worker_principal;
COMMIT;
```

The logical rollback is a transaction containing the corresponding `REVOKE SELECT ON TABLE` for the same four tables and principal, after checking that the grants being removed were introduced by this operation. It retains tables, data, audit and receipts. The read-only 0500 postcondition accepts either zero or all four exact table grants; partial grants, column grants and any audit access are a hold. This document is a proposal, not a migration or an authorisation to apply these grants.

The underlying source contract permits state insertion with a nonzero revision or `revoked` status, and its `audit_actor` is caller-supplied text. Immutable table triggers prohibit `UPDATE` and `DELETE`; they do not prohibit `INSERT` or `TRUNCATE`. These limits must be addressed in any future writer admission contract before granting write access.
