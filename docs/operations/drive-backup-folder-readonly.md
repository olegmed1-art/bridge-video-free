# Exact private backup folder: existing OAuth read-only probe

2026-10-01. ASSURED / I2. Separate branch from main so the approved OCI backup
branch/SHA86ebfda8545e23dc497cd4fa375deadd7f8e368e remains unchanged.
Publication does not dispatch. The parent performs the separately approved run.

## Dispatch contract

Workflow URL:
https://github.com/olegmed1-art/bridge-video-free/actions/workflows/native-registry-credential-probe.yml

Select branch `test/drive-backup-folder-readonly-20261001`, NOT the backup branch.
Inputs:
- expected_main_sha: `1440920191e1778fb9a9ba24e6701937a1a7459c`
- expected_review_sha: exact final published Drive-probe SHA
- operation: `DRIVE_BACKUP_FOLDER_READONLY_V1`

Owner and triggering ownerolegmed1-art; dispatch only; attempt1; exact workflow
ref/SHA and checkout. Main is checked before and after. One2min job,90s script
alarm and95s external timeout with5s kill grace. No retries. Missing summary or
cancellation is inconclusive. Rerun is refused; a second fresh dispatch still
requires new operator authorization. This temporary workflow must not merge.

## Scope and secret handling

Use only existing repo-secret GOOGLE_DRIVE_OAUTH_JSON. No environment file,
legacy-variable, ADC, service-account, workload-identity or connector fallback.
No OCI/Neon secrets enter this job. Offline tests run before the secret step.
The secret is popped before git subprocesses; client_id/client_secret/refresh_token
are parsed in memory and sent only in an HTTPS POST body to Google's token
endpoint. No scope parameter is requested: only existing grants are reused.
Access token remains in memory and is sent only in Authorization headers to
fixed Google Drive metadata endpoints. No tokens in URLs/logs/files. Redirects
and environment proxies are disabled; TLS verification remains enabled.

One existing-token refresh, GET about.user, GET exact folder, GET its permissions,
GET exact parent, GET parent permissions. No file contents, broad file listing,
upload/download of data, permission change, token revocation, new grant or OAuth
client creation. Main metadata contributes two public GitHub GETs.

Fixed expected owner: olegmed1@gmail.com.
Folder: `1mS9eoLAIX4yMsKI_Ale_TM6Xl478ULqc`.
Parent: `1uzpcT49YcV34gcv_Jo2U86a9qHUuqPpj`.
Require My Drive, untrashed folders, ownedByMe, shared=false, one matching owner
bound by both email and permissionId, exact child parent and canAddChildren=true.
Both effective ACLs must contain only the same owner. Pagination, omitted ACL,
extra principals, shared drive, pending transfer or ambiguous metadata => stop;
no permission repair. Metadata responses bounded64KiB; requests<=10s each.

Only fixed sanitized phase/gates in job summary. Actual returned account data,
credential JSON, token, API errors, permission records and response bodies are
not printed. Existing write scope is PRESENT only if refresh response explicitly
reports drive or drive.file; absent scope remains NOT_PROVEN. A successful read
probe never proves that a future upload will succeed and never authorizes it.

## Follow-on gate

If identity/folder/ACL/canAddChildren pass, same-runner OCI exact-key GET and
checksum verification followed by separately approved private Drive create can
avoid exporting credentials or relaying data through another environment.
Unknown write scope is a remaining gate; no new scope/grant is requested here.
The parent Drive connector is a different credential context and cannot validate
this repo-secret. Do not execute old diagnostic workflows that create files.

No paid resource, subscription, new credential store or retained data is created.
The probe performs metadata/authentication requests only. Drive upload, backup
schedule, main merge and recurring access remain outside this probe.

Offline verification:8 synthetic tests PASS; YAML/Bash and diff checks PASS.
Independent I2 review repeated all8 tests and checked the API field semantics:
PASS, no blockers for preparation-only publication. No live Drive calls made.
