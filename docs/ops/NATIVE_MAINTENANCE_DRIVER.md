# Isolated native maintenance driver preparation

ASSURED / I2 required before live preparation. This is a dependency-only stage,
not authorization for a database maintenance window or a pilot.

The Oracle host is ARM64 with system Python 3.12. The Light virtual environment
is service-owned and is not a trusted root import source. The fixed manual
`native-maintenance-driver-prepare.yml` workflow downloads exactly three pinned,
hash-checked binary wheels on the GitHub runner. No package installer executes
on Oracle. The dedicated supervised SSH root entrypoint uses the existing
PID1 lifetime manager and validates HOLD identity before and after preparation.
No owner database credential is loaded. The driver import makes no database
connection. Existing HOLD attestation separately reads the worker credential
and checks its database binding and empty queue in a read-only session.

The three wheel filenames, sizes and SHA256 values are fixed in
`ops/native_maintenance_driver.py`; pip's independent requirements hash checks
are in `ops/native_driver_requirements.txt`. The wire envelope is bounded and
bound to its SHA256. The source bundle is bound to the exact reviewed main SHA.
Only owner-triggered manual runs on that current main may reach the host.
Other events run local contract tests only, without production secrets.

Files are extracted to `/opt/bridge-native-maintenance-python/<runtime_id>/site`.
The identifier binds the Python executable bytes and version, architecture and
wheel pins. Directories are root-owned 0700 and files root-owned 0600. The
installer rejects links, foreign entries, traversal, oversized archives,
partial installs, changed files, nonpersistent storage and insufficient disk
headroom. An exclusive private lock prevents overlapping installs. It fsyncs
files and directories and verifies the complete tree before and after importing
with `/usr/bin/python3 -I -B -S` and an explicit trusted site path. No pip hooks,
`.pth` files, service restart or global Python environment change occurs.

The first successful ARM64 import is deployment evidence; local fake-wheel
unit tests cannot prove binary compatibility. A success record reports the
runtime identity, filesystem, bytes, file count, psycopg and libpq versions.
Repeated preparation can only reuse a byte-identical complete tree. An incomplete
or changed tree blocks reuse and is preserved. A complete tree after a failed
import or final HOLD check may be reverified in a later run; only a fresh
successful import and HOLD check can produce success. No evidence is overwritten
or deleted.
READY.json describes expected files, not an independent successful-run receipt.

Rollback is to leave this unreferenced private runtime unused. Light does not
reference it and continues using its existing environment. Cleanup is a
separate reviewed operation; this workflow has no deletion interface. An OS
Python upgrade changes the runtime identity and requires a new validation.
The code assumes the existing trusted root/OS boundary; it cannot protect
against concurrent hostile root administration.

Before production use, separately validate credential delivery, retained source
and manifest recovery, synchronous off-host checkpoints, the complete timed
maintenance path, direct-admin coordination and admission of exactly one task.
This preparation does not establish any of those conditions.
