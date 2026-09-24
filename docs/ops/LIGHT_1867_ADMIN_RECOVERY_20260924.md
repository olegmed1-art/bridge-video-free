# One-shot existing-dispatch recovery

Scoped coordinator decision: PR1769 comment5806406211, under the director's
explicit instruction to complete this recovery and one delivery. This supersedes
the historical-input/diagnostic restriction only for the existing PR1867 operation.
Permanent automation prompt, triggers, permissions and enabled state are unchanged.
The one-shot controller is the uninterrupted authorized coordinator execution;
it receives the existing verified PR1867 directly, not a fabricated webhook.

## Administrative transaction

`ops/light_1867_admin.py` derives a separate owner-only namespace from the exact
SHA-pinned rehearsal candidate. The original candidate and its branch fence remain
unchanged. Both installation and execution permit only the named production branch
or existing disposable rehearsal child, as neondb_owner in neondb. Evidence must
match that actual branch and explicitly say PRODUCTION or REHEARSAL respectively.

Before APPLY, read and retain: current main and exact reviewed wrapper; successful
installed-HOLD attestation on that main (within five minutes); original task/head;
PR1867 author/repository/draft/open/head/body hash; two complete target comment
inventories via PR1874's exact diagnostic checker; current database snapshot hash;
zero receipts/intents and no competing live tasks; renderer/policy and callback
readiness. Evidence expires after 120 seconds. Reconcile again immediately before
the transaction. Never start a deadline while sender readiness is unresolved.

Use the connected owner Neon transaction tool for one atomic transaction containing
the emitted installer and `call_sql('APPLY', evidence)`. A failed transaction rolls
back DDL and changes. An unknown result requires journal/state readback, never blind
APPLY retry. The immutable journal retains complete before/after rows and evidence.
No task, target SHA, attempts, send-intent ledger or history is erased or repinned.

The wrapper uses exact full-snapshot CAS under row/advisory locks. It preserves the
candidate's seven receipt checks and duplicate-action rejection, calls the existing
publication RPC for PR1867 and produces the existing protected-send binding.

## One-shot controller

After committed APPLY and exact readback, use unchanged renderer/policy from pinned
source b78e629ca2b04cf41d924e346f0b9dea6b45092d. Require fresh matching binding,
original target head, ready role/work/task and >180 seconds remaining. Re-read full
target comments, applying only the five exact diagnostic-ID/body-digest exceptions
from LIGHT_1867_DELIVERY_GATE_20260924.md; any new/edited/command-shaped dispatch
mention rejects the send. Generate one UUIDv4 attempt, then one single-statement
committed claim transaction. Only a fresh returned true in this uninterrupted
execution plus exact durable intent readback gives one POST attempt via the pinned
owner GitHub connector. Recheck binding/time/head after claim and before POST.

False/error/unknown COMMIT, interruption, expired deadline or any POST outcome
consumes the send opportunity; do not clear, repeat or transfer it. Existing intent
is never proof of an unused right to send. Readback must match full command text,
owner/app identity, target URL and dispatch binding. Verify genuine bot eyes ACK
and genuine bound terminal result through the existing callback workflow/receipts.
Never substitute the coordinator's own answer for Codex's terminal result.

## Rollback and containment

Before any send intent, guarded ROLLBACK requires fresh evidence and exact equality
with the journal's after-image; it restores business fields and retains audit
timestamps/events. After any intent/receipt it refuses rollback. Keep HOLD and use
read-only diagnosis. No service start, automation edit, new grants or second broker
request is part of this wrapper. GitHub/host evidence cannot be made atomic with
the database by SQL; minimize gaps and fail closed on drift.

## Verification

Run `python -m pytest -q tests/test_light_1867_admin.py tests/test_light_1867_comment_review.py`.
`python ops/light_1867_admin.py rehearsal` emits a single child-only DO statement
that installs the EXACT wrapper, rejects seven invalid evidence/state cases,
exercises APPLY/duplicate refusal/one synthetic claim/second-claim refusal/
post-intent rollback refusal/clean rollback/immutable journal/runtime ACL denial,
and rolls back every object, event, timestamp and synthetic intent. Independent
PostgreSQL readback must show unchanged original snapshot, no admin journal,
zero send intents and the original FAILED_CLOSED state. Fixture evidence in this
rehearsal is explicitly synthetic and cannot authorize production.

Executed on the existing child at 2026-09-24 02:40–02:41 UTC: exact wrapper
rehearsal PASS. Independent readback at 02:41:02.081 UTC: full snapshot exactly
equals the pre-test snapshot including timestamps; admin journal absent; intents
zero; original rehearsal journal remains two rows. Seventeen local tests PASS.
The PostgreSQL checker caught a CASE-expression parsing error in the new evidence
guard before successful rehearsal; parentheses corrected it. All failed and
successful trials rolled back. Production was not mutated during these tests.
