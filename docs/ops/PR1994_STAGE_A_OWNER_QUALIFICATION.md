# Definition-only StageA diagnostic

This isolated draft contains a generic parameterized catalog reader and synthetic TLS PostgreSQL tests. It contains no production endpoint, owner credential, case identifier, provider task ID, host route or deployment entry point.

The exact catalog expression is extracted only after SHA256-verifying the already public database/scripts/reconcile_pr1994_audit.sql against a80ffb474c78dec2a01573426e952d2050cfebd1fa994942cfe775df74ad8209.

Inputs are an exclusively owned connection, the client's pq API, an independently accepted Binding and pinned SQL bytes. The same connection must be IDLE/OK, autocommit and pipeline-off with matching login/endpoint/TLS before any SQL. It then establishes READ COMMITTED READ ONLY; its first query verifies transaction mode and its identity query uses psycopg value parameters. It reads only the nine published protected relation definitions, defaults/constraints/ACL/RLS metadata, work-item/ledger trigger definitions and the five published function metadata/hashes. Dependency bodies are returned only if explicitly requested. It does not invoke application functions or read application rows.

All results are buffered until ROLLBACK and close succeed. Non-IDLE caller connections are refused without SQL or close. A five-second statement budget, two-second lock budget, checked45-second overall deadline and262144-byte output cap apply; any transport using this module must supply an external hard watchdog for network stalls.

The implementation is a new reviewed candidate based on the conveyed StageA scope and pinned public catalog expression. The previous private psql template has not been supplied to this task, so statement-for-statement equivalence is not claimed.

Synthetic tests are not production qualification. Catalog snapshots do not exclude DDL. StageB, closure, activation and live execution are outside this draft. Any future private owner-route adapter, source-bundle allowlist integration and private response capture require their own exact-source review; no production workflow is added here.

Source rollback: omit or revert these isolated new files.
