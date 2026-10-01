# One private OCI backup with verified readback (review only)

2026-10-01; ASSURED / I2. Preparation and test-branch publication only. No live
production dump/upload, new DB connection, merge, schedule or activation is
authorized by this document. Prior validation-only approval cannot authorize PUT.

## Evidence and reuse

- Local encrypted roundtrip: commit383db1c1cc4d76abeef54abe1f210edcfa239011,
  run36879969934 PASS, no retained copy/artifacts.
- Existing OCI route: commit6279d2e1b2d2155e13c4c4c0b554cbfabdd7e445,
  run36902300516 verified80byte synthetic conditional PUT/GET/SHA.
- `ops/oci_backup_bucket_metadata.py` and `ops/oci_readonly_inventory.py`, with
  their unit tests, are copied verbatim from that reviewed OCI commit. Runtime
  reuses only historical field=value credential normalization, in-memory signer,
  fixed identity constants and retry-disabled ObjectStorageClient factory.
- Parent inspected bucket policy UI: lifecycle/retention/replication empty,
  versioning disabled. This is dated evidence, not permanent policy assurance.

## Compact execution plan

1. One owner-dispatched20min job verifies repo, branch, workflow, exact review SHA,
   checkout, unchanged main, attempt1, explicit new PUT authorization and deadline.
2. Use existing OCI_CLI_* only in the final step and in memory. Recheck exact
   namespace/bucket, Private/Standard/Oracle-managed/disabled versioning and
   auto-tiering before any production query. No lists or policy changes.
3. Reuse production identity/ACL/RLS/read-only preflight, bounded dump<=200MiB,
   and encryption with unchanged NEON_BACKUP_PASSPHRASE. No common DSN changes.
4. Exactly one conditional PUT of ciphertext to the fixed private destination.
   Flush key, ciphertext SHA256 and size to job summary BEFORE PUT. If that local
   intent write fails, do not PUT. No retry,
   multipart upload, overwrite, delete, rename, public link, PAR or ACL mutation.
5. Delete the original local ciphertext. GET only that same object key; stream
   bounded bytes, require exact length/encoding/SHA, then decrypt downloaded bytes
   and restore in the existing network-none bounded PostgreSQL18 container.
6. Verify restored catalogs/critical rows, recheck main and remove local files,
   containers/volumes. Retain the OCI object, including after a later failure.
   Only sanitized job summary; no GitHub artifacts or raw manifest.

The deadline includes SDK setup and transfer: first-step timestamp, job20min,
supervisor1080s work maximum, first-step+1140s and approved deadline-40s cutoffs,
with cleanup reserve. POSIX signal caps SDK calls/stream reads as well as Docker.
SDK connect/read timeouts3/8s and NoneRetryStrategy are additional limits, not
substitutes for the shared deadline. File/DB/resource caps are unchanged. A slow
upload/download fails closed; never increase caps or retry automatically.

## Exact storage destination and retention meaning

Namespace `frzcdnwzijyf`; bucket `bridge-light-autopilot-backups`;
region `eu-frankfurt-1`; tenancy fixed by reviewed credential factory.
Object name:
`neon-backups/v1/<run-id>-1-<exact-review-SHA>.dump.enc`.
Run IDs make keys unique; the owner can reconstruct the exact key without listing
even if the runner is lost before GitHub retains the summary. Actual creation
time is OCI metadata, never backdated.
Only one ciphertext object, at most200MiB. `candidate/`, `native-journal/`,
`neon-backups/probe-v1/` and all existing objects are untouched.

If-None-Match:* makes this harness create-only;412 stops. This is NOT WORM or
locked immutable retention: authorized bucket principals may still alter/delete
objects outside this harness. Bucket-wide retention/lifecycle/versioning stays
unchanged. The object is retained until a separately approved retention decision;
no35/90day retention or monthly generation is claimed or silently installed.

An interrupted PUT is WRITE_OUTCOME_UNKNOWN: preserve key/hash/size and stop.
Do not infer absence, repeat PUT or delete. After confirmed PUT, readback/restore
failure leaves the object present but NOT verified as a restorable backup.
Only complete PASS plus cleanup and readback/restore gates proves this attempt.
The public GitHub summary exposes only operational key/hash/size/gates, not data
or credentials. Bucket privacy follows OCI IAM, not the GitHub repository ACL.

## Future dispatch, separately authorized

Workflow URL:
https://github.com/olegmed1-art/bridge-video-free/actions/workflows/native-registry-credential-probe.yml

Branch: `test/neon-backup-maintenance-review-20261001`.

Inputs:
- expected_main_sha=`1440920191e1778fb9a9ba24e6701937a1a7459c`
- expected_review_sha=`<exact final published reviewed SHA>`
- operation=`OCI_BACKUP_ONE_CREATE_READBACK_V1`
- oci_write_approval=`OCI_BACKUP_ONE_CREATE_READBACK_V1:<same SHA>:policies-reviewed:one-create-approved`
- deadline_epoch=`<fresh approved UTC Unix deadline within20min of first job step>`

Only owner/triggering ownerolegmed1-art, attempt1. The operator enforces one new
dispatch; run_attempt cannot prohibit a second fresh dispatch. The concurrency
group is the existing `oracle-light-backup-mutation`, cancel-in-progress:false,
so this operation queues rather than cancels a historical writer. No workflow on
main, environment policy, writer secret or runtime schedule is changed. This
temporary harness MUST NOT be merged as the permanent scheduled backup workflow.

## Drive second-copy gates: existing path found, not live-validated

Target folder `1mS9eoLAIX4yMsKI_Ale_TM6Xl478ULqc`, parent
`1uzpcT49YcV34gcv_Jo2U86a9qHUuqPpj`; parent reported owner-only
olegmed1@gmail.com. No Drive upload is included in this harness.

Repository evidence: `.github/workflows/drive-media-oci-recovery-acceptance.yml`
already references GOOGLE_DRIVE_OAUTH_JSON with existing OCI secrets;
`run_drive_3_1_free_oidc.py:user_oauth_token` uses existing user OAuth refresh
credentials, requests auth/drive and forbids ADC/service-account fallback.
`tools/drive_acl_audit_readonly.py` contains permission inspection. The diagnostic
and acceptance workflows also WRITE; do not run them as read-only probes.

These code references do not prove secret availability, actual granted scopes,
token identity/refresh validity or access to the new exact folder. Before a
second-copy implementation/live run, separately authorize a bounded read-only
runner probe of existing OAuth: expected account, untrashed exact folder ID/type,
parent, canAddChildren and complete owner-only permissions including inheritance.
Unknown/truncated grants, other principals or missing capabilities => stop;
do not repair ACLs or request additional scopes automatically.

If that passes, prefer same-runner authenticated OCI exact-key GET -> checksum ->
private Drive create using that existing OAuth. Ciphertext and OCI keys then
never cross into Windows/Light or connector infrastructure. New OAuth, service
accounts, PAR/public links and exported credentials remain outside scope.

Alternative parent Drive connector handoff is currently BLOCKED: no authenticated
OCI-to-connector binary transfer channel is established. A private object URL is
not a file reference and cannot bypass IAM. Minimal manual alternative requires
separate permission: owner downloads exact ciphertext via authenticated OCI
Console and supplies a private file attachment accessible to the connector;
verify size/SHA before upload to the exact owner-only folder. Do not use GitHub
artifacts or copy keys as a transport workaround. Folder creation alone does not
complete any transfer gate or prove two copies exist.

## Cost and approval boundary

One new retained object<=200MiB, two metadata GETs, one PUT and one readback GET;
no new infrastructure, storage policy or subscription. Existing account/free
quota is not proven by small bucket size. Oracle storage persists after the run;
actual charges, retention and future repetition require explicit agreement.
Neon read compute/egress and private Drive capacity are separate cost gates.
The earlier<$2 estimate covered one local-only validation, not this new operation.

Before live: fresh exact refs/Neon identity/bucket policy evidence, explicit one
production encrypted upload/readback permission, cost/retention acceptance and
deadline. Final activation, recurring backups and Drive second-copy execution
need separate approvals. Until then, stop at tested test-branch publication.

## Preparation evidence

70 offline tests pass:36 backup tests and34 imported OCI tests. Actual SDK2.187.1
constructs a synthetic RSA signer/client; socket and SDK HTTP transport are
blocked while checking GET/GET/conditional PUT/exact GET construction. Mock cases
cover denied metadata, collisions, ambiguous writes, failed intent recording,
truncated/oversized/encoded/corrupt readback, deadline and later restore/cleanup
failure. Imported helper/test contents match the exact6279d2 source blobs.
Workflow YAML/Bash parsing, unchanged operational backup contract and diff-check
pass. No production access or actual OCI/Drive operation is part of these tests.
Independent I2 review repeated all36 backup tests including the real-SDK test:
PASS, no remaining blockers for preparation-only publication.
