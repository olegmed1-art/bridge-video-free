# Native maintenance workflow API integration

2026-09-26; ASSURED preparation; #1946. Baseline main is
`9fa8c222a808c96807c812dceba059d7907d2b72` (journalled pause #1985).

The journalled pause library now has a real HTTP adapter. It accepts only the
fixed school repository, exact approved workflow IDs and paths, and a pinned
plan digest. It refuses mutation by default. Future writes require the caller's
independent `assert_dispatch(plan_digest, action, workflow_id)` guard, checked
before and after verifying current main. No implementation of that mutation
guard or production controller is supplied by this revision.

HTTP writes are never retried. Redirects, unexpected status/body, oversized or
duplicate-key JSON, unexpected pagination and invalid identities refuse. A
failed adapter instance stays failed. Errors omit raw HTTP bodies and exception
chains. The four-second socket timeout is not a total lifetime guarantee; the
production caller still needs the independently supervised execution bound.

The new main-only workflow has `contents: read` and `actions: read`, and invokes
only a GET inventory command. It contains no SSH, database or production secret,
no `actions: write`, no disable/enable call and no shared mutation group. PR
runs execute local contract tests only. The main run supplies authentic workflow
IDs, paths, states and timestamps needed for a future bounded exclusion plan.

Registry pagination uses constructed same-origin URLs, a fixed maximum of 1,000
entries, stable total counts and unique IDs. Main is checked before and after.
These checks do not make pagination an atomic snapshot. The registry deliberately
preserves unknown/deleted states as observations; those cannot automatically
become approved pause entries. The report always says
`writer_exclusion: NOT_ESTABLISHED` and `production_mutations: false`.

Source API contract:
https://docs.github.com/en/rest/actions/workflows

## Evidence and remaining gate

Local tests cover request scope, authorization headers, unexpected responses,
redirects, duplicate JSON, oversize, lost reply/no retry, missing mutation guard,
main drift, changed workflow identity and partial/overlapping registry pages.
They use a fake HTTP opener/API; they do not prove live disable/rerun behavior.
Only the post-merge main observation can establish live read access and registry
state. A successful read does not prove Actions write access.

Before any live pause/grant: finish the bounded writer allowlist and scope
exclusions, establish actual direct-owner/workflow-administrator coordination,
drain historical attempts and remote/backends, retain the journal outside any
ephemeral runner, and connect the existing supervised session with current
approved DB/HOLD manifests and explicit uncertain-outcome recovery.

Rollback is a source revert. The live probe performs no state mutation; no
workflow, service, credential, DB privilege or task needs restoration for it.

## First live observation and compatibility fix

Main run `36232931023`, source
`c1eb22d92f27eb02e7dbdfe4a79c6e74550c25d8`, successfully read 458 registered
workflows (457 active, one disabled_manually). This is a point-in-time registry
observation, not 457 writers or proof of 457 running jobs. Historical registry
entries can differ from the set of workflow files in current main.

The actual API timestamps included fractional seconds, e.g.
`2026-08-12T21:15:58.000Z`. The pause plan originally accepted only whole UTC
seconds and would safely reject those observations. Its schema now also accepts
one to nine fractional digits before `Z`, preserving the exact string for drift
checks. A regression uses the observed format; malformed fractions, non-UTC
offsets, newline suffixes and excessive precision remain refused. No timestamp
rounding or workflow identity check is relaxed.
