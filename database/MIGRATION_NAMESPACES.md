# Database migration namespaces

To prevent repeated sequence collisions between parallel Bridge School workstreams, migration and SQL-test number ranges are reserved.

## Reserved ranges

- `0001–0099`: legacy/shared/core history. Existing files are immutable once promoted.
- `0100–0199`: **Club / Member / Identity / Auth** only. New files in this range must start with `identity_`, `club_`, `member_`, or `auth_` after the numeric prefix.
- `090–099` SQL tests: **Club / Member / Identity / Auth** regression tests only, with the same allowed name prefixes.
- Other future ranges must be reserved here before first use if a parallel workstream needs collision-free numbering.

The GitHub workflow `migration-namespace-guard.yml` enforces these reservations on pull requests and pushes to `main`. This reservation is organizational only; it does not promote migrations to production. Production remains controlled by the separate manual `database-production` workflow and its explicit `MIGRATE` confirmation gate.
