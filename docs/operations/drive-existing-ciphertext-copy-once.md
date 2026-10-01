# One separately approved second copy of existing ciphertext

Status: review preparation only; no live copy authorization. ASSURED / independent I2 review required.

## Fixed evidence and scope

Source: successful OCI run [36908992530](https://github.com/olegmed1-art/bridge-video-free/actions/runs/36908992530), code `86ebfda8545e23dc497cd4fa375deadd7f8e368e`. Parent browser receipt confirmed durable copy, readback, hash, isolated restore and cleanup. GitHub API independently confirmed success and zero artifacts. Destination readonly run [36909259808](https://github.com/olegmed1-art/bridge-video-free/actions/runs/36909259808), code `3ca105654ab7454ccbb84e3aa907d3d627f54805`, passed identity and owner-only folder/parent checks; existing write scope present. These runs are not repeated.

- OCI namespace `frzcdnwzijyf`, bucket `bridge-light-autopilot-backups`, region `eu-frankfurt-1`.
- Exact key `neon-backups/v1/36908992530-1-86ebfda8545e23dc497cd4fa375deadd7f8e368e.dump.enc`.
- Exact size 8,238,960 bytes; SHA-256 `5878d3eef5f63753b39be3b247057c7694e1b381b3b5854832fbf14c085a48af`.
- Drive folder `1mS9eoLAIX4yMsKI_Ale_TM6Xl478ULqc`, parent `1uzpcT49YcV34gcv_Jo2U86a9qHUuqPpj`; existing expected owner only.

## Execution contract

Only workflow_dispatch on `test/drive-backup-copy-review-20261001`, exact reviewed SHA, fixed main `1440920191e1778fb9a9ba24e6701937a1a7459c`, owner actor and triggering actor, attempt 1. Explicit approval string `DRIVE_EXISTING_CIPHERTEXT_COPY_V1:<review SHA>:one-create-approved` and fresh deadline are mandatory. Workflow has no schedule and no database environment; only existing Drive OAuth and five OCI credentials are available in its final step. No new grants, permission changes, source writes, passphrase, decryption, dump, or production database connection.

Job limit five minutes including installation; runtime limit at most 240 seconds and bounded by approved deadline, with cleanup margin. Ciphertext remains in process memory; no data files, artifacts, core dumps, token/session URL outputs or child processes with credentials. Local memory release/process exit is not a secure memory erasure claim.

1. Refresh existing OAuth without requesting scopes; verify identity, explicit existing write scope, exact folder/parent owner-only metadata and ACL.
2. Check exact private OCI bucket and GET only the fixed key. Require byte length, `Salted__` prefix and SHA-256 before any Drive mutation.
3. Refuse an existing same-name destination result or incomplete listing; preallocate one Drive ID. Recheck main and folder/parent identity/privacy immediately before creation.
4. Flush and fsync intent containing exact source identity/hash/size and generated Drive ID, conservatively `UNKNOWN`, before initiating creation. Failure to persist intent prevents create.
5. One resumable initiation POST and one full-content PUT only. Session URL must use exact HTTPS Google host/path and constrained query; no redirects, proxies, retry, status probes or resume. No updates/deletes. A timeout or malformed reply preserves `UNKNOWN`; never automatically try another ID.
6. Require exact created file metadata and owner-only ACL; independently GET Drive content and verify exact length/hash. Recheck file and destination privacy and main, then report PASS.

The >5 MB object uses Google's documented [resumable single-request upload](https://developers.google.com/workspace/drive/api/guides/manage-uploads), with a pre-generated ID. A confirmed create followed by failed verification remains `CONFIRMED` but not `second_copy_verified`. Existing OCI object is always preserved. Generated ID is kept in the receipt for separately authorized read-only reconciliation. A hard runner loss can prevent GitHub from retaining even a fsynced summary: if no ID is recoverable, stop for reconciliation; never blindly redispatch. Duplicate-name listing is a guard, not globally atomic deduplication.

Metadata/ACL observations are not an atomic lock against other actors changing permissions. Any detected drift fails closed without attempting unauthorized ACL repair/deletion. Neither storage location has an immutable retention guarantee; this copy adds no 90-day policy. The original OCI roundtrip tested restoration; this operation proves identical ciphertext, not a new restoration run. Storage/quota/billing availability is not guaranteed by the earlier readonly probe; quota denial stops without retry.

## Approval boundary and recovery

After published SHA verification and independent review, request ONE dispatch, <=5 minutes, existing exact ciphertext only, one new owner-only Drive file, one readback, no retries or subsequent mutations. Parent supplies a fresh Unix deadline just before dispatch (typically now+4 minutes, never beyond job start+300 seconds). Approval of the previous readonly probe is insufficient.

Do not auto-delete a created or ambiguous file. Inspect its recorded ID in a separately approved read-only reconciliation if needed. No merge, schedule, main mutation or credential change is included. Removing this review branch cancels future availability; it does not remove any remotely created copy.

## Offline evidence

Synthetic tests cover source corruption/length/prefix, exact OCI GET, denied URLs/redirects, strict resumable Location, one POST+PUT, ambiguity/no retry, intent flush/failure, duplicate/pagination guards, scope and privacy refusal, exact file metadata/ACL, independent readback, secret removal, sanitized failure, SHA/actor/approval/deadline context and workflow credential boundaries. Imported OCI factory regression tests remain unchanged from reviewed backup code. No tests use live credentials or network.
