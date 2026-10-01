# Exact-ID original-OAuth reconciliation

ASSURED, preparation only; parent dispatches after reviewed publication. No rerun of the copy workflow.

The single authorized copy run `36939347618` at SHA `cbadb3a10c1f608ca3f6f54e8b58049a20c7ae92` failed with `phase=create`, `create_outcome=UNKNOWN`, source hash verified, and allocated ID `1hrg64wNgiRuxF4tCRj52YDLVWHSrhQKF`. Existing Drive connector returned 404 for that ID while the same owner and destination folder were accessible. Different OAuth contexts make that insufficient to prove absence. No second copy is currently verified. The OCI backup remains untouched.

## Read-only harness

Separate branch `test/drive-copy-reconcile-20261001`, registered workflow path `.github/workflows/native-registry-credential-probe.yml`. Inputs: exact main `1440920191e1778fb9a9ba24e6701937a1a7459c`, published review SHA and `DRIVE_EXACT_COPY_RECONCILE_READONLY_V1`. Exact owner actor/triggering actor, attempt 1, workflow SHA/ref and checkout identity enforced. Main checked before and after.

Two-minute job, 90-second alarm, 95-second process timeout plus five-second termination grace. No installation, database environment, OCI credentials or passphrase. Only existing `GOOGLE_DRIVE_OAUTH_JSON` enters the final step, is removed from environment before subprocesses, and is refreshed without a scope parameter or new grant.

Allowed HTTP operations: existing OAuth refresh POST; current-user identity GET; GitHub main GET; exact file metadata GET, exact file permissions GET, and exact file media GET. No file listing, folder listing, generateIds, session initiation/resume/status, create, PUT/PATCH/DELETE, broad searches, or permission edits. Ciphertext is streamed only into a bounded hash calculation, never written to disk or decrypted. Redirects/proxies are refused; token/session URLs and raw failures are never output.

The file must match exact allocated ID, expected ciphertext name, application/octet-stream MIME, 8,238,960 bytes, exact destination parent, expected sole owner and owner-only ACL, not shared/trashed/shared-drive. Independent media download must match SHA-256 `5878d3eef5f63753b39be3b247057c7694e1b381b3b5854832fbf14c085a48af`; metadata and ACL checked again afterward.

- `PASS` with `second_copy_verified=true` means exact readable copy and privacy checks passed.
- `OBSERVED` with `NOT_FOUND_OR_NOT_VISIBLE` / 404 means diagnosis completed, not successful backup, definitive absence, session expiry, or permission to retry. This can also mean permissions are inaccessible after metadata access. Exit 0 for this observation must not be described as a backup PASS.
- Other failures remain `FAIL` with fixed failure class and numeric HTTP status only.

## Offline-only creation diagnostics

The original copy module now records fixed `create_stage`, numeric `http_status`, fixed `failure_class`, and booleans `initiation_accepted`, `session_validated`, `put_attempted`, `final_response_accepted`. It preserves `UNKNOWN` before mutation and does not infer a confirmed write from a partial reply. No raw exception, body, Location/session URL, token or header is retained. This branch workflow cannot execute the create entrypoint. Its old workflow is retained only as an offline test fixture.

The session URL security validator is unchanged. Tests cover Google's [documented resumable Location](https://developers.google.com/workspace/drive/api/guides/manage-uploads) and reordered query parameters, plus negative host/scheme/path/query/duplicate/fragment cases. Existing evidence cannot distinguish POST rejection, locally refused Location, PUT failure or rejected final reply; no cause is presumed and no security gate is relaxed.

## Evidence and boundaries

Offline tests cover exact endpoint/method restrictions, original OAuth reuse, owner check, conservative 404 outcome, metadata/ACL refusal, bounded independent readback, context bindings, sanitized failures, single-secret workflow boundaries and distinct create diagnostics. Existing source-copy and OCI regression suites remain applicable. Independent I2 required before publication. No live calls are part of these tests; no main/PR/schedule/secret changes. If reconciliation observes 404 or failure, stop and report; do not automatically create a new session or reissue content PUT.
