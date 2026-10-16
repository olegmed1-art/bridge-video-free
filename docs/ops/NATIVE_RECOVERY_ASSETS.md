# Private candidate recovery assets

2026-09-26 — ASSURED, tracking #1946. This step preserves the source and
permission manifest needed by later recovery. It neither approves a baseline nor
executes the maintenance session or pilot.

The manual owner-only workflow is bound to exact current main and holds the
existing Oracle workload and backup mutation groups. Its SSH child uses the
already verified private ARM driver and independent 100-second PID1 supervisor.
The child checks HOLD before and after, reads the actual owner connection in a
read-only transaction, checks dormant state and existing runtime denials, then
returns a **candidate** before/expected-after manifest through captured SSH stdout.
The credential travels only through SSH stdin. The runner never prints private
stdout, the manifest, source envelope or exception text. It does not call
`engine.prepare` with its own observation as an approved baseline.

The data-only envelope binds exact source SHA, source bundle digest, manifest
digest, baseline digest and the fixed complete production target. Validation
uses the trusted checkout's decoder and permission engine, rejects duplicate
keys/noncanonical encodings/oversize input, requires dormant baseline and the
exact six-function expected delta. It never executes downloaded source. A
recovery controller must itself be independently trusted; the package is not a
self-authenticating or standalone executable recovery environment.

Retention uses the existing private bucket under the separate content-addressed
prefix `native-journal/recovery-assets-v1/`. The existing compartment, object
budget, private/no-public-link/no-lifecycle controls remain enforced. Creation is
conditional and has no retry or overwrite. An existing object is accepted only
if its complete bytes match. After a lost reply the controller fails; any object
already written is retained. No head or journal is created or advanced.

The runner downloads and validates the retained bytes, reconstructs both files
inside a fresh private directory, fsyncs and rereads them, and reports only
digests, byte count and fixed status. Restoration cannot overwrite an existing
directory and preserves partial output after failure. Temporary runner material
is disposable; the OCI object is the retained copy. No public Actions artifact,
presigned URL, database credential backup or production journal backup is made.

A successful live report proves candidate retention and byte restoration only.
Independent acceptance must retain the exact five identifiers and review the
target/baseline; the candidate's own hashes cannot approve themselves. Before a
future permission transaction, fresh live BEFORE/HOLD/source and coordinated
writer/drain checks remain mandatory. The synchronous production journal path,
timed executor and one-item admission are separate unfinished implementation.

Verification: corrupted/mismatched identifiers, duplicate keys, noncanonical
base64/JSON, wrong target/baseline, active queue/config, unauthorized helper/table
delta, symlink/public parent, interrupted restore, immutable object creation,
lost acknowledgement, private-bucket refusal and redacted errors. OCI requests in
unit tests are simulated; live success must be recorded separately at its SHA.

Rollback: leave the optional workflow unused or revert the code. Preserve any
retained evidence; no production ACL, service or admission change needs undoing.
