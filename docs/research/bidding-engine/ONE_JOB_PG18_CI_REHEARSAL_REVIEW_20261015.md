# Proposed synthetic PG18 rehearsal for PR #2175

Status: local review patch only. Do not treat this document, the existing 13
green CI checks, or the blocked Windows attempt as a SQL pass. No CI run has
been requested for this patch.

The one authorized local attempt at commit
`0a4a3daa48b5553407cf060674c82a01f0c67731` stopped before migration
0001. Its first psycopg TCP connection to its disposable server returned
Windows `10013` (`Permission denied`). The temporary cluster and its PG log
were removed by the script's cleanup. The receipt and stderr are in
`../one-job-review-0a4a3daa-20261015/` relative to this checkout,
outside the Git worktree. A Python preflight bound port 55443 and `pg_ctl start`
returned success; this makes a pre-existing listener or reserved port less
likely, but does not prove the origin of the TCP denial. No SQL result follows
from this attempt.

The revised local patch adds `--ci-service` to the same synthetic rehearsal
script and a separate path-limited PR workflow,
`.github/workflows/one-job-pg18-rehearsal.yml`. Its `postgres:18` service is
an isolated cluster: it does not share cluster-global roles with the normal
`database-ci.yml` service. The workflow installs only the same pinned psycopg
and PostgreSQL client dependency already used by database CI; nothing has
been installed or run for this local patch. It replays the exact reviewed
baseline inventory 0001-0059, the single approved 0060 file (whether still
under `migrations/` or later moved unchanged to `candidates/`), proposed SQL
0061/0062, and the existing
stage/read/finish, deadline, replay, role, and acknowledgement-loss cases.
There is no production DSN, external provider call, new dependency, IBM
compute, deploy, merge, or ACL change. The SQL step has a four-minute cap
inside a ten-minute job cap; its eventual runner use is a separate decision
before publication.

`database/fixtures/one_job_pg18_approved_inventory.json` pins every baseline
migration name and SHA-256, all included SQL component paths and SHA-256, plus
0060's name and SHA-256 for synthetic rehearsal only; this manifest does not
authorize production migration. The rehearsal rejects
missing, added or changed baseline SQL, an altered 0060, or 0060 present in
both candidate and migration directories. Moving the same 0060 bytes from
`migrations/` to `candidates/` remains valid. A changed approved baseline
requires a reviewed manifest update; file count alone is never evidence.

This rehearsal currently pins 0060 SHA-256
`27932f2b5f22f383d9f84341c0ae6e88dc346c7940ec92490edc0a3d37358dfe`,
the bytes in this checkout and main `a314d0d6`. A separate review reports a
different 0060 variant in PR #2168, beginning `cfd5bf`; that source could not
be fetched in this local environment and is not silently substituted. The
present workflow supports only the pinned variant. After any sync with #2168,
compare the exact upstream file and its semantics, choose the intended test
input through review, update the manifest explicitly, and repeat the static
and PG18 checks. Until then, a hash mismatch must block the rehearsal.

The script rejects non-PR, non-Linux, wrong-repository, wrong-head, wrong-port,
wrong-service-DSN, wrong-server-version, and non-pristine-schema contexts
before applying SQL. It emits the tested checkout SHA and SHA-256 of its own
source and 0061/0062 on successful completion. A future GitHub run is a pass
only if the final JSON contains `SCHEMA_INSTALLED`, `migrations: 60`, both
proposal IDs, the expected head SHA and all scenario `PASS_*` results, and the
job concludes successfully. A canceled, timed-out, skipped, or failed run
remains inconclusive or failed; the 13 earlier green checks do not substitute.

Before publishing, review the patch against the current PR head, verify the
inventory against the approved main history, and determine whether the Windows
TCP refusal represents an execution sandbox denial or a system port rule.
Publishing this workflow will trigger a new CI job on the PR and must be a
deliberate separate action.
