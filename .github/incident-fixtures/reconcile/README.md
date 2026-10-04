# Create-only evidence writer: isolated candidate

This is a library and synthetic tests, not a production recovery entrypoint.
No credentials, incident identifiers or production configuration are included.

`commit(root, request_bytes, approved_request_digest, observe)` creates only
`cycles/<plan_sha256>/reconciliation-v1.json` under an existing approved root.
It records RECONCILIATION_EVIDENCE_ONLY / PREPARE_ROLLBACK_SUPPORTED and keeps
issue/replay/ACK/retirement/incident_closed false. It never calls services, SQL,
provider APIs, subprocesses or network clients. No command-line interface exists.

The approved request binds a 300-second maximum window, exact original-file
hashes, exact directory inventories, root device/inode, an original-object
metadata snapshot digest and six independently reviewed evidence digests.
Hashes are integrity references, not authenticators. The caller's action-time
adapter and the exact request/code must be independently approved. No live
adapter is provided. A caller-created request and lambda are only a test fixture.

Existing issuer then cycle flock files must be root-owned regular0600/nlink1.
Missing locks/directories are never created. Reads use nofollow/noatime. Original
file inode/size/mtime/ctime, root identity and directory identities are rechecked;
directories other than the output parent also retain mtime/ctime. Only the new
output filename and its parent's consequent timestamps are excepted.
All other writer exclusion, fresh provider/DB/host/source/no-execution proofs
remain the trusted adapter's responsibility and must be demonstrated before use.

The output CAS is ABSENT -> exact bounded record via O_CREAT|O_EXCL. The file is
visible during writing: this is NOT atomic rename and no consumer may treat its
presence/JSON prefix as success. Short writes loop, then file and parent fsync,
byte readback and final guards run. Lost reply is idempotent only for identical
complete bytes with fresh guards and repeated fsync. Partial/empty/conflicting
records remain untouched and require separate reconciliation. No unlink,
replacement, cleanup or automatic repair is implemented.

This writer does not declare unconditional absence of historical external effects.
Its no-execution proof reference must specify the reviewed protocol and its
integrity assumptions. A rollback leaves HOLD/admission unchanged and retains
all originals and any new/partial record; it never deletes evidence to allow retry.
Existing strict inventory readers may refuse after this new filename appears;
no reader/admission exception is bundled or implied.

Tests use synthetic files only. Windows can run pure schema tests; Linux-root
filesystem tests must pass without skips in a disposable no-network container
before this candidate is accepted. CI has no secrets, no checkout credentials,
no host mount, read-only image, no-new-privileges, bounded memory/CPU/time.
