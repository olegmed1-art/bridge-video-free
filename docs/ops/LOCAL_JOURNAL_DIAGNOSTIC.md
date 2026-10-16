# Local journal refusal diagnostic

Change date: 2026-10-04. Assurance: ASSURED; independent Linux-root regression required before promotion.

The existing read-only observation can refuse during LOCAL_JOURNALS without identifying which local guard failed. This mode instruments the actual local() implementation, preserving predicates, evaluation order and short-circuit behavior. LOCAL_JOURNAL_CHECKPOINTS.json maps fixed checkpoint IDs to the original public expressions and statements. An AST-erasure regression binds the implementation to its pre-instrumentation AST.

The separate diagnose-local-reference action requires an independently accepted OWNER_ACCEPTED_LOCAL_JOURNAL_DIAGNOSTIC Agreement and READ_ONLY_DIAGNOSE_LOCAL_RETIREMENT_JOURNALS scope. Existing create or observe Agreements cannot authorize it. The root operation uses the existing bounded supervisor, shared locks and Snapshot reader, with no DB, provider, service or journal-write calls. It does not load the retained runtime or use its credential argument.

The public result is strict and under 4096 bytes: fixed checkpoint, reason category, role and helper indices, a fixed 26-row trace, and the accepted record SHA256. No paths, journal content or exception text are serialized. Trace columns are read, metadata, hash, schema; values are 0 not established, 1 PASS, 2 FAIL, 3 not applicable. Successful parsing alone does not certify semantic schema. A role is last-read context, not attribution of every predicate failure. JSON_KEYS/JSON_TYPE/JSON_SYNTAX are exception categories, not independent proof of a specific malformed field. Unknown trusted historical-prefix reads remain subject to the original Snapshot checks without exporting their paths.

LOCAL_CHECKED and schema PASS certify only the existing local() guards; they do not certify full downstream observer schema. The original guards accept baseline/before arrays, and the equivalence tests intentionally preserve that behavior. Proposal observation, HOLD/DB/provider verification, incident closure, execution ACK and new-task authority always remain false. Early proposal ABSENT from a refused older observation is not final absence proof. Neither the original unknown CREATE nor a previous closed observation Agreement may be replayed.

## Deployment and rollback

Review the exact candidate and Linux x86/ARM evidence in a draft PR. No main merge or host dispatch is included in this change. After independently coordinated ordinary promotion, recompute the exact main controller/helper pins and obtain a new single-use diagnostic Agreement and quiet window before any diagnostic dispatch. Stop on refusal and reconcile the reported checkpoint against its fixed public map. Do not infer historical cause or automatically CREATE/replay.

Before promotion, rollback is abandonment of the draft/test branch. After separately authorized promotion, code rollback is an ordinary reviewed revert of this PR, with controller pins refreshed; it is not journal cleanup or service rollback. Existing journals must remain untouched. No incident closure or production readiness is claimed by this patch.
