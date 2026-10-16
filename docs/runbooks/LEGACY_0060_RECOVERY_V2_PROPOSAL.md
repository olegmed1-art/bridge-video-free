# Restore legacy queue capabilities after the installed 0060

Status: proposed, production recovery not executed. This is a separate transition
for exact historical 0060 SHA-256
`c2960f3b78a34985d1afd78930f896f38eebeae2cbd90f83c44a011fe7d779a7`,
not an installer rerun or a reinterpretation of the newer candidate.

The offline builder `database/recovery/fixed_subset/legacy_installed_packet.py`
requires the original acknowledged installation receipt, reviewed pre-install
catalog and explicit target. It verifies the receipt catalog/data hashes, all
migration history, the exact 24-function installed ACL matrix, an inert subset,
the full source/reservation map and drained queue. Unknown history and catalog
drift stop the transaction. It does not refresh the baseline automatically.

The v2 body restores the captured legacy claim definition and owner, restores
legacy ACLs, removes six added old-table guards and disables subset API EXECUTE.
It retains all rows/events/reservations, the sequence, 0059/0060 registry rows,
four new tables and five NOLOGIN roles. A separate explicit forward change closes
implicit PUBLIC function EXECUTE in the retained owner's default privileges.
The marker `repair-0060-legacy-installed-v2` has body SHA-256
`30ba592da8f2924092df699d7501f704fd020690b135d5a422944b0b76adaa03`.
The ordinary migrator only audits that marker; it never applies the body.

The 041-043 consumers bind each recovery variant to its own installed checksum,
function matrix and repair checksum. Missing, forged or simultaneous markers and
reenabled subset API remain errors. The published new-candidate path keeps its
original behavior and checksum.

Local PostgreSQL 18 evidence on 2026-10-16: legacy v2 passed 16 negative cases,
the independent before/after ACL/owner collector, exact data/registry preservation
and all 041-043 behavior tests. The new-candidate path passed its existing 17
negative cases and 041-043 regression. The 32 offline unit cases passed.
Two earlier local attempts stopped at the historical/current admission-fixture
mismatch and at 043's old marker-only branch; both were corrected before the
successful runs. Synthetic service storage was removed after testing.

Production prerequisites remain action-time work: independently review the exact
proposal/CI, reconcile the target and original receipt again, confirm the current
worker fence and a bounded action window, and obtain the permission that replaces
the current prohibition on production SQL. A status snapshot alone is not a
continuous fence. The whole envelope runs once in one connector transaction with
its existing timeouts, advisory lock and temporary membership cleanup. UNKNOWN
acknowledgement means HOLD and investigation; never replay automatically.

Capture an independent post-commit readback before releasing the fence. Restarting
or powering compute and enabling legacy intake are separate transitions. New
legacy work will not automatically update retained subset reservations, so future
subset reactivation requires a newly reviewed reconciliation.
