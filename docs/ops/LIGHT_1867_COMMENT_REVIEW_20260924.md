# Executable diagnostic review for existing PR1867

ASSURED preparation only. This tool does not authorize or perform recovery,
claim a send intent, POST a command, start Light, or change the live automation.
The production administrative recovery wrapper remains unimplemented; the
isolated candidate's branch fence remains intact.

Run from this reviewed checkout with authenticated GitHub CLI:

```sh
python ops/light_1867_comment_review.py
```

The only external calls are explicit GitHub GETs. It checks the original target
head before and after two full paginated scans. It requires the exact five
diagnostic IDs and UTF-8 body digests recorded in
`LIGHT_1867_DELIVERY_GATE_20260924.md`, rejects duplicates, edited/deleted
diagnostics, additional dispatch mentions and command-shaped evidence, and
rejects any inventory change between scans. Failed requests never become an
empty successful response. Reports older than the operation are not a permit;
GitHub scans cannot provide an atomic lock against later comments.

Even a matching report explicitly returns `send_authorized:false` and
`live_bridge_gate:BLOCKED`. Do not wire this tool into the active bridge as an
exception without the scoped contract decision. The actual bridge currently
forbids historical-event recovery and any dispatch mention, including these
diagnostics. Its stricter policy remains effective.

Local validation: 13 tests PASS, including edited/deleted diagnostic, duplicate
ID, extra/case-varied mention, command marker, invalid body, concurrent comment
change, target drift and transport failure. Fresh connector all-page snapshot
contained 22 comments and exactly the five expected diagnostics; inspection
matched inventory SHA256
`a0bb6b67cdae051e2cc285d2a762c4ba9763dbddbc0614016fed3b25b0822882`.
This is a snapshot inspection, not a claim that the CLI collector ran against
production or that delivery was authorized. No I2 production promotion decision
is claimed by these unit tests.

Dispatcher decision still needed: adopt or reject the exact one-shot input and
diagnostic exception described in the delivery-gate document. After adoption,
implement/review the production recovery wrapper with owner-only execution,
immutable before/after journal, fresh installed-HOLD proof, matching current
state, original task/head, no receipts/intents, and rollback refusal after any
send intent. Rehearse that exact wrapper before production APPLY. Do not first
open a PUBLISHED deadline while the controller cannot consume it.

Rollback for this preparation: close/revert this PR; it has no runtime hooks or
production effects. Light remains on installed 5eb0e1bb in stopped HOLD.
