# Installed 0060 history routing

The production `schema_migration` row for `0060_fixed_subset_admission` has
SHA-256 `c2960f3b78a34985d1afd78930f896f38eebeae2cbd90f83c44a011fe7d779a7`.
It matches the 0060 file at source commit `744d068a2cddfb8347411c8e4fb2bd69d19c09ab`.
The newer guarded candidate has SHA-256
`cfd5bf6088761dcc1d38a4d0febb6fb24f3d4f396fd67ca08568ff44994c44bc`.
These bytes and behaviors are distinct.

| Installed checksum | Inventory | Connector verifier/recovery |
| --- | --- | --- |
| `c2960f3b…d779a7` (`legacy-744d068a`) | Recognize exact historical row, leave it unchanged | Fail closed before generating SQL; a verifier/recovery pinned to the historical source must be supplied separately |
| `cfd5bf60…94c44bc` (`new-scoped-claim`) | Recognize exact candidate row, leave it unchanged | Use the current guarded verifier/recovery |
| Any other checksum, missing checksum, or trailing bytes | Reject | Reject |

The external-history manifest is an audit list, not an installer. Neither
variant is applied by `migrate.sh`. The local PostgreSQL 18 history fixture
checks both ledger variants and malformed rows. The separate connector fixture
checks the new candidate's full install, verify, recovery, transaction rollback,
data preservation, ACL restoration, and cleanup. It does not establish that
the current recovery SQL is compatible with the historical installed catalog.
