# Native permission maintenance: workflow inventory

2026-09-26; ASSURED; repository preparation only; tracking #1946.
Baseline commit: `9fe64089ae745399754a5e43a406e20ff08a0c37`.
Git source tree: `f72aff5454e0b676db79bd2336275e96e478fcfc`.

## Finding and decision

The complete baseline contains 320 workflow files. Of these, 167 contain literal
secret-name references matching NEON, DATABASE, POSTGRES, ORACLE, OCI or SSH.
These are review candidates, **not 167 proven production writers**. A workflow
can be disabled, read-only, target a disposable database or use a low-privilege
identity. Conversely, a workflow without such a name may still have write access
through scripts, actions, reusable workflows, credentials or external services.

The two-group proposal in ORACLE_LIGHT_NATIVE_PERMISSION_CHANGE_PLAN.md does
not establish complete writer exclusion. Do not wire it to permission-engine
apply as though all privileged channels were already covered. Extend the map
and resolve capabilities against the intended production target first.

| Additional candidate | Observed concurrency | Required review |
| --- | --- | --- |
| oracle-light-fresh-candidate.yml | oracle-light-database-mutation | Owner DB and SSH references; review source dump versus remote restore scope. |
| recovery-registry-population.yml | recovery-registry-population | Owner DB reference and metadata-writing script; push and manual triggers. |
| autopilot-temp-neon-owner-preflight-once.yml and autopilot-temp-neon-0308-structured-artifact-once.yml | autopilot-temporary-neon-owner-ingress | Intended temporary endpoints; prove current target isolation, including old branch revisions. |
| autopilot-source-migration-preflight.yml | No workflow-level group | Multiple DB credential references; establish actual read/write capability. |
| neon-independent-backup-restore.yml | No workflow-level group | Owner DB reference; distinguish source reads and restore destinations. |
| autopilot-codex-event-callback.yml | Per-comment and per-publication groups | Runtime callbacks outside the two proposed owner groups. |

This table is a triage subset, not a complete allowlist or evidence that these
workflows are currently running. No workflow, schedule or credential was changed
by this inventory. No host command, database write or workflow dispatch is needed.

## Reproduce from immutable Git objects

Install the CI-pinned PyYAML 6.0.3 dependency, then run:

```sh
python ops/native_permission_writer_inventory.py --revision <40-character-commit-SHA>
```

The CLI reads committed Git blobs with Git replacement objects disabled, not
mutable working-tree files or local replacement refs. It records
every YAML workflow, its SHA256, trigger specification, workflow/job concurrency,
job conditions, environments, reusable-workflow references and literal secret
names. Dynamic/opaque secret references are flagged. It outputs metadata only:
no secret values, DSNs or run bodies. The full source-tree ID binds downstream
repository code changes even when workflow text is unchanged; this is a pointer
for review, not an automatic transitive-capability analysis.

Every entry stays UNREVIEWED and maintenance_exclusion is NOT_ESTABLISHED.
Successful execution means the inventory was generated, not that applying grants
is safe. Conditions are preserved as text, never treated as an authorization
decision. Lexical secret matches may include comments. Duplicate YAML keys,
invalid shapes, symbolic-link workflows and mutable revisions are refused.

Before a real window, reconcile current and queued/running historical revisions,
credential consumers, local/reusable actions and scripts, direct owner tools,
host processes and other operators. Establish an actual shared exclusion or
verified scope exclusion for each relevant writer. Provider administration stays
a trust boundary. An idle run/session list and a self-issued freeze flag cannot
establish exclusion. The permission engine's default guard remains closed.

## Verification and remaining work

Tests use a real disposable Git repository to prove dirty-tree/replacement-ref independence,
whole-tree changes when a called script changes and symlink refusal. Policy cases
cover nested groups, literal/dynamic/inherited secrets, disabled job conditions,
duplicate keys and unknown capabilities remaining unreviewed. CI inventories the
entire checked-out commit without production secrets or mutation concurrency.

#1967/#1968 supply the transaction engine and Neon identity checks. This change
supplies the reproducible coverage inventory needed before finalizing its
maintenance coordinator. It does not supply a production freeze, active-run
reservation, live HOLD attestation or a grant workflow. Production permission
apply and the bounded provider pilot remain separate outstanding work.
