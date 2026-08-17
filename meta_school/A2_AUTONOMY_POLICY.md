# META A2 Autonomy Policy

Status: ACTIVE — owner authorized 2026-08-17

A2 permits autonomous Stable promotion only for deterministic, bounded R1 technical changes.

Mandatory gates before every A2 promotion:
1. defect is objective/reproducible with HIGH confidence;
2. Candidate scope is frozen and minimal;
3. no bridge bidding, teaching methodology, canonical pedagogical semantics or owner canon changes;
4. current Stable revision/version is re-read immediately before write;
5. independent deterministic validation passes;
6. dependency impact is bounded and passes;
7. recovery copy/rollback reference exists before write;
8. cost is inside authorized envelope;
9. write uses concurrency/version control when platform supports it;
10. exact post-write read-back passes all acceptance criteria;
11. Evidence is recorded; failed read-back triggers rollback/freeze, never silent acceptance.

Automatic A2 promotion is forbidden when any evidence is UNKNOWN, STALE or CONFLICTED; risk is R2/R3/R4; rollback cannot be demonstrated; scope expands after validation; or the change alters meaning rather than correcting a deterministic technical defect.

A2 may escalate risk but may not automatically downgrade risk to obtain authority.

A2 rollback: preserve the pre-change recovery artifact/reference; if a deterministic acceptance/guardrail check fails after write, stop propagation and restore the pre-change state when the rollback action is itself verified safe. One rollback terminates that promotion attempt.

A2 does not authorize production database schema migrations, identity/permission changes, broad infrastructure changes, or canonical bridge/teaching changes. Those remain R2/R3/R4 gates.