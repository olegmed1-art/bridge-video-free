# One-shot book source identity operator

Status: local candidate; no production execution is admitted by this document.

Run `python -m database.book_source_operator` from the reviewed checkout on an existing Linux operator host. The command only reconciles source, PDF asset and source_asset identity. It does not stage/publish WORLD, activate canon, update existing sources, deliver outbox messages, create credentials or change grants.

The existing `BRIDGE_WORKER_DATABASE_URL` must already belong to `bridge_school_worker_principal` and target the pinned pooled production endpoint. There is no DSN command-line argument and no DATABASE_URL or Light fallback. Any inherited environment variable whose name starts with PG is rejected by presence alone, including an empty value; values are neither read nor logged. The one-shot process must be launched without inherited libpq overrides. This CLI does not edit the parent environment, service files or credentials. `autopilot_light_worker_login` cannot be used or converted into a writer by this command. Actual operator access and grants remain a live readback requirement; knowing the expected role is not proof that access exists.

Expected configuration is Neon project `misty-poetry-18012774` (bridge-school-core), branch `br-aged-mud-b1i64914`, endpoint `ep-noisy-pine-b1pe30sf`, pooled host `ep-noisy-pine-b1pe30sf-pooler.c-5.eu-central-1.aws.neon.tech`, database `neondb`. The CLI checks explicit expected IDs and a strict existing URI without rewriting endpoints. The connection explicitly binds port 5432. On the connection, it checks host/port/db/user plus server current_database, session_user/current_user, inherited worker capability and absence of public CREATE/superuser/BYPASSRLS. It repeats server identity and effective-capability checks immediately before commit. Project/branch IDs are pinned endpoint metadata, not an independently fetched Neon control-plane attestation; their current binding must be read back before live admission.

Supply three regular local JSON files, their exact raw-byte SHA256 hashes, and canonical school/source UUIDs. Symlinks, FIFOs, files larger than 2 MiB, duplicate JSON keys, non-object JSON, NaN/Infinity and contract mismatches fail before connecting. These hashes bind execution to reviewed bytes; they do not establish who reviewed or approved a receipt. Obtain receipts through the existing trusted independent review process. Do not generate them from book content or an unauthenticated request.

Example with placeholders for existing reviewed files and IDs:

```sh
python -m database.book_source_operator \
  --target-project-id misty-poetry-18012774 \
  --target-branch-id br-aged-mud-b1i64914 \
  --target-endpoint-id ep-noisy-pine-b1pe30sf \
  --principal bridge_school_worker_principal \
  --school-id SCHOOL_UUID --source-id SOURCE_UUID \
  --bundle /private/review/bundle.json --bundle-sha256 BUNDLE_SHA256 \
  --receipt /private/review/source-review.json --receipt-sha256 REVIEW_SHA256 \
  --identity-review /private/review/identity-review.json --identity-review-sha256 IDENTITY_SHA256
```

This is a dry run: the real inserts and validations execute in a forced-rollback transaction. Allowing a missing source requires the separate `--register-missing-source` flag. The maximum is then one source, one PDF asset and one embodies link; exact replay adds none. Without that flag, the source must already exist and source insertion budget is zero. Existing source UUIDs, locators, types and meanings are preserved; conflicts fail closed. The shared conflict detector also checks exact native file keys in source_identity; this CLI requires only SELECT on that table and does not insert or update its rows. Broader conflict detection does not widen the existing book receipt/citation alias contract.

Before invoking reconciliation, the CLI uses read-only privilege catalog functions to require effective SELECT on source/asset/source_asset/source_identity, INSERT on asset/source_asset and table UPDATE on source/asset/source_asset for the shared table/row locking contract. With `--register-missing-source` it additionally requires INSERT on source, SELECT on school and UPDATE on at least one school column for the existing school FOR SHARE lock. Existing-source mode does not require those three extra capabilities. A missing capability yields BOOK_SOURCE_OPERATOR_CAPABILITY_MISSING before any identity writes. The CLI never changes grants or weakens required locks; an existing worker role suitable for video/tournament writes may still be ineligible for missing-source book registration.

Commit requires the explicit `--commit` flag after current target/role, writer compatibility/exclusion, schema effects, receipts, recovery and budget evidence have passed release admission. This CLI does not discover or drain other writers and its existence does not prove that older writers are absent. Preserve current jobs and checkpoints under the release cutover plan. There are no retries or batch loops.

The POSIX main-thread wall-clock budget defaults to 30 seconds and can be explicitly reduced or increased within 1–60 seconds. It covers input validation, connection, identity checks and reconciliation/commit. A pre-existing process alarm, unsupported platform or non-main thread fails closed. Connect timeout is at most 10 seconds; session statement, lock and idle-in-transaction limits are 15, 5 and 15 seconds. The reconciler also sets its own transaction-local statement/lock limits. No background service or schedule is installed.

Successful output contains target identifiers, UUIDs, the three input hashes, publication hash, fixed row budgets, resulting counts and dry_run/committed flags. It contains no DSN, password, book text, title or locator. Error output contains a fixed code, phase and commit outcome only; parser errors also avoid echoing pasted argument values.

On ordinary failure or interruption inside reconciliation, the existing transaction context attempts rollback. On wall-clock expiry the CLI closes the connection before unwinding, so cleanup cannot keep waiting on the same broken network socket; disconnect aborts any open transaction. If COMMIT acknowledgement or connectivity is lost, `commit_outcome=not_confirmed` is deliberately ambiguous: do not retry blindly. Use a fresh, scoped readback of the exact source UUID, all three canonical file forms (https://drive.google.com/file/d/ID/view, drive:ID and gdrive:file:ID), both exact native source_identity keys (google-drive:ID and google-drive:file:ID), asset UUID/SHA and all PDF/embodies bindings before deciding whether an exact replay is safe. A confirmed committed identity must be retained for provenance. Do not delete source/asset/link rows as code rollback. Keep incompatible old writers excluded when rolling back code. This operation does not publish knowledge, so WORLD visibility retirement is a separate bounded operation if publication is later admitted.
