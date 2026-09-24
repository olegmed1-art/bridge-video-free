# Delivery gate for existing PR1867 — 2026-09-24

Status: BLOCKED at the live bridge contract, no production recovery or send.

Fresh host attestation succeeded in Actions run35946070266 at 02:10:13 UTC:
installed5eb0e1bb, exact immutable bundle, mailbox1703, HOLD/PID0, Neon epoch0.
PR1872 is merged at c23636b4a8551eedf514560616d2e496092dc074. Desktop Commander
is offline, but the approved administrative SSH channel works.

The actual enabled automation `Autopilot Codex bridge`
(id6ab10b0c69008191be2fd868c7cb369d, sourceb78e629ca2b04cf41d924e346f0b9dea6b45092d)
was read, not inferred from the staged template. Its last run was
2026-09-23T19:31:49.413913Z. Its owner approval for migration0370 and one send-intent
claim is present. That does NOT remove its following active constraints:

- process only the current input PR-event packet, no backlog/history recovery;
- FAILED_CLOSED never re-sent;
- any dispatch-id mention in target conversation comments forbids a new POST.

All-page target comments contain the following five diagnostic references,
none containing SLAVIK_CODEX_DISPATCH_V1. Exact UTF-8 body digests:

| Comment ID | SHA256 |
| --- | --- |
| 5801585590 | `d4f3f7da327f7b9a767b82986cf58f92dbf73ecac7016a267a9f8185683564e8` |
| 5801658871 | `946e3550a8998b39fac5ecf07bb34315685d6ce842269f101bdc42834df761ba` |
| 5801800189 | `8baec372b681f82ace58becf5e1e1056a0917f76b3032c9d530e83bb0940bc23` |
| 5802079929 | `ae9c25f77c7b0021834539ffced27767fa62f628ed6692eb94d5d7c73496ab8e` |
| 5803910509 | `0b69559dff2390dbed6d572f3f18e223f0d9e2f4fd41440a298a1d60d283c455` |

This is a pre-claim policy conflict, not evidence of a successful command. The
renderer/readback module only treats command-shaped records as candidates, but
the live pre-claim prompt is stricter; do not silently substitute the looser
readback rule. Do not delete/edit these reports, fabricate a new PR webhook,
run the bridge without input, clear a send intent, or manually post a command.
No automation prompt, trigger, enabled state or permission was changed.

## Concrete dispatcher decision required

Review a separately scoped one-shot recovery contract for the existing PR1867,
with the original task/dispatch/target SHA unchanged, before production APPLY.
It must explicitly define how an existing verified discovery PR enters the
controller and how the exact five diagnostic references above are distinguished
from command evidence. This document is a proposal, not that authorization.
Any exception must be exact-ID/body-digest bounded, fresh full-pagination checked,
and reject edited, additional, command-shaped or conflicting records. Keep the
permanent send-intent ledger, successful committed true response, durable readback,
final target/binding/time checks, one POST maximum and genuine ACK/terminal rules.

The administrative SQL recovery wrapper must still be implemented and reviewed;
the existing candidate remains rehearsal-only. Its strengthened transition and
rollback were tested again by the rollback-only harness in this PR. Do not APPLY
before delivery-controller readiness: it would create a deadline the blocked
controller cannot use. Keep Light stopped in HOLD meanwhile.

Exact safe recheck command, while c23636b4 is current main:

```sh
gh workflow run oracle-light-recovery-attest.yml \
 --repo olegmed1-art/bridge-video-free --ref main \
 -f expected_main_sha=c23636b4a8551eedf514560616d2e496092dc074
```

After a new merge use its reviewed current-main SHA; installed release stays
5eb0e1bb. This command is read-only and is not the missing recovery/send action.
