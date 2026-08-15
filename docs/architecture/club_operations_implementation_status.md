# Club Operations implementation status

Status as of 2026-08-15 after second-pass review:

- Architecture v0.3 is documented and remains an extension of the existing school core, not a parallel member database.
- Google Drive area `Управление клубом` exists as a document/file layer; it is not the operational source of truth.
- Candidate PostgreSQL migrations `0020–0026` are implemented on `main`.
- The second-pass audit found and corrected additional integrity gaps in entitlement usage/reversals, member balance semantics, adjustment reversals, contact/delivery routing, commercial-version overlap, package entitlement semantics and price/service consistency.
- Database verification covers legacy invariants plus Club Operations tests `011–013`, clean PostgreSQL 18 migration, idempotence, checksum protection and registry verification.
- Final recheck run `31905952353`, job `95063657542`, completed with `success`; every database CI step passed.
- Production Neon was rechecked directly and remains on migrations `0001–0019`; Club Operations tables `0020–0026` have not been promoted there yet.
- Production operational health is `ok` with 0 critical, 0 warning and 15 ok signals at the repeat check.
- Production currently has no real `Person` or `Student` records, so no member or student data migration has occurred.
- Machine Knowledge/Canon remains unpopulated and must be ingested under the existing authority/review rules before autonomous Bridge Coach use.
- Member authentication, object-level authorization, Member API and Club Window remain separate subsequent stages. Public/member access must not be opened before those controls are implemented and verified.

Important semantic distinction introduced by the review:

- `person_financial_balance` is the member account balance and includes all received payments, including not-yet-allocated payments.
- `person_allocated_receivable_balance` is a reconciliation view showing what charges remain uncovered by explicit allocations.
- `PaymentAllocation` therefore explains application of cash to charges; it is not allowed to redefine whether the club actually received the payment.

Policy intentionally left unresolved rather than invented:

- `ContactPreference=denied` is a hard delivery stop in the candidate schema.
- The required behavior for `ContactPreference=unknown` depends on the club's approved business/legal policy and is not inferred by the implementation.
