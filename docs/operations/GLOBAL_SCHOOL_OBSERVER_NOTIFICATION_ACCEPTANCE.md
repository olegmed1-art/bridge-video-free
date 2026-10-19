# Global School Observer notification acceptance plan

Prepared 2026-10-19 for [#1157](https://github.com/olegmed1-art/bridge-video-free/issues/1157).
Status: **QUALITY REVIEW READY; EXTERNAL DELIVERY NOT AUTHORIZED OR CONFIGURED**.
This is a proposed acceptance sequence, not an instruction to enable a sender.
The Observer currently has no delivery adapter and all notifications remain disabled.

## 1. Formation evidence already available

- PR #2185, merge `1742ba4ed046e6d8c661341916b982465187087a`.
- [Canary 39295595186](https://github.com/olegmed1-art/bridge-video-free/actions/runs/39295595186)
  and [shadow 39295595183](https://github.com/olegmed1-art/bridge-video-free/actions/runs/39295595183):
  four findings, four local previews each, matching dedupe keys, no delivery.
- 27 Observer tests and 37 full application CI tests passed; independent I2 review
  passed after repairing truncated HTTP response handling.
- Preview includes severity/class, subject, observed time, state, evidence, owner
  and the minimal next action. Tests include ERROR, LOOP_DUPLICATION and STALE.

## 2. Quality review without sending

The Observer owner reviews the four retained examples against their actual scope:

| Example | Expected interpretation | False claim to reject |
| --- | --- | --- |
| Portfolio verification dated 2026-08-30 | The prior partial registry baseline is old; a fresh partial edit does not establish full coverage | All school services are current/healthy |
| Autopilot verification dated 2026-09-01 | The prior shadow verification is old; recent GitHub facts are separately scoped | Legacy stopped, Native in production, or everything remains shadow-only |
| Open audit #620, due 2026-09-25 | Audit closure is blocked on missing evidence/outcome | Governance policy is invalid or all operations must stop |
| Open acceptance #1157 | Notification-quality acceptance remains pending | Successful code/CI authorizes external alerts |

Record the adjudicator, decision time, source revisions, true/false/uncertain label
and reason for every example. Proposed acceptance: all four are evidence-backed,
none implies unobserved runtime readiness, and uncertain cases are withheld.
No population-wide false-positive rate can be inferred from four selected examples.

Replay each identical finding twice in memory and pass prior keys explicitly:
one preview per key within the supplied set, zero new previews for supplied prior
keys. Changing observation time or unrelated main commits must not create a new key.
Persistent suppression between scheduled runs is **not implemented or accepted**;
it must be designed/reviewed before recurring external delivery is considered.
Do not add polling, reruns or a new notification platform for this review.

## 3. Exact proposed recipient and test content

Intended human recipient: **Oleg, repository owner `olegmed1-art`**. No students,
groups, mailing lists, collaborators or public issue subscribers are recipients.

External destination: **NOT PROVIDED / NOT VERIFIED**. No email address, Telegram
chat ID, Slack channel or other endpoint was supplied for this task. GitHub author
metadata and commit email are not destination verification. A public issue comment
or `@mention` cannot guarantee delivery only to Oleg and is not the proposed test.

Before requesting send approval, the owner must identify one private destination
through an already connected service. Resolve and verify its exact endpoint with
that service and retain it only in approved private task evidence. If connection,
new credentials, rights, spending or network changes are needed, stop at that
separate boundary. No new delivery integration is created by this plan.

Exact proposed one-message body (the test ID stays fixed):

```text
ТЕСТ School Observer — проверка доставки, не инцидент.
ID: observer-delivery-canary-20261019-01
Проверяется получение одного тестового сообщения Олегом.
Никаких действий с рабочими службами не требуется.
Если сообщение получено один раз, подтвердите получение в исходном чате Codex, указав этот ID.
```

This body contains no real incident payload, student data, runtime inventory,
secret, host identifier or action link. It must not be silently changed to a live alert.

## 4. Separate approval and bounded delivery test

Only after the quality decision and exact private endpoint are recorded, present
one concrete request: send **one** message to Oleg at that verified endpoint using
the existing connected sender, with exactly the body above; keep recurring alerts
and all other recipients disabled. Identity alone is insufficient: until the
endpoint is known, the send request is **NOT READY FOR APPROVAL**.

After explicit approval, recheck the endpoint and sender, send once through the
existing authorized service, and retain its message ID and timestamp privately.
A timeout is uncertain delivery: inspect the existing message before any retry.
Do not resend automatically. Sending success proves service acceptance only;
Oleg's acknowledgment with the same test ID proves human receipt. Record duplicate
or missing delivery honestly. Do not infer recurring delivery reliability from one test.

The default result after the test is still notifications disabled. Recurring
alerts need a separate accepted delivery/dedupe/recovery contract and authorization.
No transport test or external message has been sent by this task.
