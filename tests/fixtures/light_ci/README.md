# Public runtime source fixture

`ORIGINAL_RUNTIME.json` retains public sources and two derived non-secret HOLD
configuration fragments from the established offline bundle builder. Tests read
the fixture as data; they do not import or execute its embedded source strings.

- Public source revision: `5eb0e1bb2c2932bd8d02ff187b9cf24f6bc09c7c`.
- Raw file SHA-256: `82f00df903a86a30d9d4c88fcf14e03124b9b14314a9e84ed27b5344f901d8f2`.
- Canonical bundle SHA-256 checked by `original_runtime()`:
  `3f7736a4fa7a6796eb9fa7be10e1d01e6cd04cb803d426991c942a91f7eb1c07`.
- Serialization: sorted JSON keys, compact separators, default ASCII escaping;
  hash covers `revision` and `files`, excluding the `sha256` field.

No production receipts, student records, registration, task plan, database
credentials or reusable authorization are included. The fixture is not evidence
of current service state. It avoids requiring historical Git objects in a shallow
CI checkout. `LIGHT_CI_RUNTIME_MANIFEST` remains an explicit local test override.

Run `python -I -B -S scripts/run_light_ci_offline.py` from the repository root.
No installation, network, Git history, database or systemd service is needed.
Temporary test directories are created under `.light-ci-test-tmp` in the checkout,
or under an explicit `LIGHT_CI_TEST_TMP` override, and are cleaned up after each test.
The historical owner-evidence regression is explicitly skipped when its separate
local archive is absent; that archive is not part of this publication.
