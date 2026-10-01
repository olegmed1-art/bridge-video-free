# One-object synthetic OCI write/readback probe

2026-10-01. ASSURED / I2. Code preparation; the parent owner session dispatches once.

## Owner scope and evidence

Parent personally inspected exact bucket Policies screenshots: lifecycle No items
at 17:39:24 UTC; retention and replication No items at 17:40:35 UTC. This is UI
evidence at those times, not an interpretation of the earlier API lifecycle404.
At 17:41:27 UTC the owner approved one nonpersonal synthetic upload <=1 KiB and
readback into existing `bridge-light-autopilot-backups`, preserving the new object
and all old objects/policies. No production DB/Neon dump is authorized or used.

The existing OCI_CLI_* GitHub secrets remain confined to the selected final step;
the parser compatibility and explicit in-memory key_content SDK fixes are retained.
No new keys, IAM, permissions, buckets, schedules or services.

## Bounded operation

Fixed tenancy, eu-frankfurt-1 and bucket. Namespace comes from API. Exact bucket
metadata is rechecked before writing: matching name/namespace/root compartment,
Private/Standard, Oracle-managed encryption, disabled versioning/auto-tiering.
The script uses the separately confirmed UI policy review as an explicit gate;
it does not convert a lifecycle API404 into an empty policy.

Successful run: GET namespace, GET exact bucket, **one conditional PUT**, GET only
the newly created key and compare bytes/SHA256. The unique object lives under
`neon-backups/probe-v1/`; body is generated synthetic text <=1024 bytes, never
student data, a receipt from production, or a database backup. No listing, deletes,
overwrite, multipart upload, replication, policy changes or automatic retries.
Collision/412 stops. Ambiguous write outcome stops without a second PUT or an
automatic recovery attempt. Preserve the object and report the key for reconciliation.

One job <=5 minutes; script <=120 seconds. The workflow uses the same concurrency
group `oracle-light-backup-mutation` as the historical writer. GET metadata mode
and write-probe mode are mutually exclusive. All checks/tests precede secrets.

Future owner-session dispatch (do not rerun automatically):

```text
workflow: oracle-epoch-readonly-probe.yml
ref: review/oci-readonly-inventory-20261001
source_run_id: oci-backup-write-probe-v1:<reviewed full SHA>:policies-reviewed:one-write-approved
```

The marker is accepted only with exact repository/branch/SHA/owner identity and
workflow identity. The existing read-only marker cannot select the write step.
Review publication does not dispatch either mode. No schedule exists.

## Incremental resource/cost estimate

Maximum successful footprint: one object of <=1024 bytes kept after the test,
one PUT plus one GET readback, and two metadata GETs. Up to 1 KiB of payload egress
plus normal metadata/protocol overhead. GitHub job runtime is separately bounded
to five minutes; account billing/minutes are not inspected.

Reference rates, not an account bill: Oracle's published price list lists object
storage at $0.0255 per GB-month and $0.0034 per 10,000 requests. Ignoring any free
allowance, 1024 bytes at the decimal-GB rate is about $0.000000026 per month; four
requests at that rate about $0.00000136. These are proportional estimates, not a
guarantee of invoice rounding, account entitlement or free quota remaining.
Existing 11.48 MiB does not establish account-wide quota/usage. The owner's actual
plan, credits, traffic allowances and billing remain UNKNOWN.
The current fixed synthetic payload is exactly **80 bytes**; the 1 KiB bound is
enforced before PUT. At the reference storage rate its proportional storage cost
is about $0.00000000204 per month before allowances.

Source: Oracle public price list, August 4 2026, page 54 (PDF page index53):
https://www.oracle.com/ke/a/ocom/docs/corporate/pricing/us-public-sector-3904395.pdf

## Success does not mean production backup readiness

A passing probe proves only conditional creation and byte-verified readback for
this synthetic object through this principal at that time. It does not prove Neon
backup consistency, encryption of future archives, retention automation, restoration,
free-tier headroom or readiness to upload production data. No production work follows
automatically. Rollback is to stop; the small test object is deliberately retained.

## Offline verification

52 tests PASS without skips. Real OCI SDK 2.187.1 with synthetic credentials and
blocked socket/HTTP transport confirms the protocol GET, GET, PUT, GET, conditional
If-None-Match header, exact payload and identical readback key. YAML structure PASS.
Independent I2 review repeated all 52 tests: PASS, no blockers. It also checked the
SDK source honors NoneRetryStrategy for PUT. Interrupted/ambiguous PUT is reported
as WRITE_OUTCOME_UNKNOWN with no automatic retry. Run attempts after the first are
rejected. A separate new dispatch remains technically possible, so the parent
coordinator must dispatch the single authorized run only once.
No live upload was performed during preparation.
