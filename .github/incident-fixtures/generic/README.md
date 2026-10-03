# Generic read-only continuity collector candidate

Local review candidate only. This directory is the complete proposed public
file set. It contains invented configuration and test data. No deployment
configuration, production evidence, owner command or historical source diff
belongs in this directory or a published commit's ancestors.

The collector has no site defaults. The separately reviewed owner wrapper must
inject exact canonical PRIVATE_CONFIG_BYTES and its independently pinned
PRIVATE_CONFIG_SHA256 before module initialization, and exact EXPECTED_PINS
before main. Configuration is a fixed schema of scalar substitutions, not a
file list, command interface or permission to select additional resources.
Identity slots are deliberately opaque. Their deployment mapping is private.
The synthetic configuration is for tests only and is never a deployment fallback.

Missing, changed or malformed configuration refuses with a constant message
before collector host access. A mapping proxy prevents configuration item
mutation. The owner wrapper must pin both collector bytes and private config
bytes; a digest supplied by an untrusted party is not an authorization boundary.

The collector uses bounded reads, existing shared nonblocking locks,
O_NOFOLLOW/O_NOATIME, exact bindings and inventories, repeated metadata/content
checks, service HOLD/process/NNP checks and fixed refusal stages. No writes,
credential use, repository imports, remote calls, replay or acknowledgements.
It compares protected configurations and process environments only in memory.
Success is local evidence; live/database/provider verification and permission
to issue remain false. This code is not independently approved for host use.

Tests: `python -B -m unittest discover -s . -p 'test_*.py' -v`.
Filesystem fixtures require a disposable root Linux container, read-only root,
no network/secrets/host mounts and dedicated root-owned mode-0700 tmpfs at
`/fixture-data`. Set WHITELIST_FIXTURE=1 only in that sandbox. Linux acceptance
requires 42 tests, zero skips and zero failures. Running on Windows exercises
27 tests and explicitly skips 15 Linux tests; this is not Linux acceptance.

No CI workflow or publication is authorized by this file. No production command
is included. Stop on refusal; do not change ownership, modes, locks, journals,
admission or services. No production mutation needs rollback; discard local
candidate artifacts if rejected and preserve original evidence privately.
