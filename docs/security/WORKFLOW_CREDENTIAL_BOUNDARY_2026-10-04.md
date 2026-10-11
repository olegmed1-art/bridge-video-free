# First Critical/High workflow credential block

Status: **DRAFT — PARTIAL CODE CONTAINMENT — EXTERNAL MIGRATION STILL REQUIRED**.
Governance: ASSURED; production acceptance remains INCONCLUSIVE.

Base: `f513afc4a1a4cae325b7baeeef5f4848dd29a7a5`.
Base tree: `c22bdfdb89421671ee8072a5f6e8224649afa174`.
This candidate was reconstructed from immutable GitHub source through the authorized
GitHub connector after local Windows execution failed. It does not claim to include
or revalidate an unread local patch. Applicable AGENTS.md is unchanged at blob
`b7e3f3cfb3cf1cb9a845d4c1629970753b8e3c48`; no additional tracked AGENTS.md,
SKILL.md or .agents instructions exist in this base.

## Code changes and operational effect

| Workflow | Existing exposure | Draft behavior |
| --- | --- | --- |
| oracle-light-native-cli-install-preflight | Repository Python sent to remote sudo using Oracle SSH; main/actor guard already existed | Offline contract retained; privileged probe held; immutable event SHA checkout |
| diana-longitudinal-v42-regression | Same-repository PR Python with Drive OAuth | Offline PR regression retained; field job held and limited to reviewed main/SHA dispatch |
| bridge-ai-pilot100 | Production DSN throughout PR/manual job | Credential-free PR compilation; production pilot held; main/SHA dispatch; DSN step-scoped |
| oracle-universal-video-oci-auth-probe | Nineteen OCI aliases in job-wide env | Probe held; explicit main restriction; aliases exposed only to presence-check step |
| oci-object-storage-readonly-inventory | Job-wide OCI credentials and GitHub token | Inventory held; immutable checkout; dependency install before credentials; step-scoped env |
| ibm-vpc-power-probe | Arbitrary dispatch branch with IBM and optional Oracle credentials | Both probes held; main/SHA dispatch; immutable checkout; IBM secret step-scoped |

Seven privileged jobs contain a literal `false && ...` quarantine. The proposed
`production-operations` binding is a future migration target, not a claim that this
environment exists or is protected. These jobs must remain held until separately
approved settings migration and evidence review. A merge of this draft as-is would
pause the listed live capabilities; offline tests do not replace field evidence or
the production Pilot-100 run.

No existing token permissions, credentials, environment settings or network policies
are changed here. Step scoping reduces accidental exposure but does not isolate
processes sharing a runner; the pilot API retains its credential in its own process.
The Oracle sudo payload and existing credential capabilities need separate review.

## Current settings evidence and its limits

The coordinating task supplied a read-only browser inspection from 2026-10-04
19:49–19:54 UTC and a fresh owner-approved policy confirmation at **20:09:33 UTC**:

- `database-production` now uses **Selected branches and tags**, exactly branch
  `main`: **1 branch, 0 tags**. This replaces the earlier unrestricted state.
- Production credentials remain at repository scope and are outside that environment
  boundary. Their private names/scopes inventory stays in the owner handoff, not in
  this public change record. Names already referenced in source are listed below.
- Additional review and bypass settings were not changed by the approved branch-policy
  action. Independent review of main changes and of privileged jobs remains an owner
  acceptance requirement.

These are attributed observations from the coordinating task, not fresh API settings
reads by this worker. The connector refused the workflow-list metadata endpoint;
this worker did not bypass that denial. Secret values were never requested.
Reconcile settings again before any activation; do not treat this dated record as
standing execution permission.

## Security boundary and residual risk

A same-repository branch writer can change guards, tests and environment references,
or add another workflow. The quarantine, main/actor checks and required tests are
code containment, not an external barrier. This is not a claim of default secret
leakage to external forks.

The approved `database-production` main-only rule protects secrets in that
environment. It does **not** protect the repository production secrets. A writer can
still request accessible repository credentials from modified branch code.
Neither absence of same-name duplicates nor the new main-only environment rule
closes this systemic class. Other current workflows and retained refs remain in scope
for later work. Missing historical workflow files in main do not prove old refs safe.
No Codex Security scan was launched and no findings were closed.

The supplied historical triage was 356 findings: 15 Critical, 5 High, 132 Medium,
74 Low and 130 Informational; among the top twenty, fifteen constructions current,
three files absent and two retired. This PR addresses only six selected workflows.

## Precise owner handoff — proposed, not executed or newly authorized

1. Inventory names, scopes, consumers and equivalent aliases for all repository production
   credentials and any accessible organization credentials. Values must
   stay in owner-controlled secret entry; do not copy them into issues, logs or PRs.
2. Approve a coordinated migration of confirmed production credentials to protected
   environments. For this block, the proposed target is `production-operations`;
   service-specific environments are preferable if consumer mapping permits narrower
   access. Before use, require an exact Branch `main` rule, no tag/PR/wildcard rule,
   a trusted reviewer, prevention of self-review, and disabled admin bypass where
   supported. Select actual reviewer identities explicitly.
3. Migrate every approved consumer before removing repository-level access. Remove
   repository copies and organization access to equivalent production credentials.
   Keeping those copies leaves the class exploitable. Plan continuity and rollback
   with each consumer; do not silently break services or restore unsafe copies.
   The owner has **not** authorized this migration or further settings changes.
4. Require trusted review of code entering main, with stale-approval dismissal and
   approval of the latest push by another reviewer. Cover workflows, scripts and
   dependencies they execute, including mutable downloads/artifacts. Review active
   bypass actors and other routes to main. Zero required approvals is not an
   independent review boundary.
5. Review legacy workflow enabled states and retained refs without deleting history
   or closing findings automatically. A disabled workflow alone cannot protect
   repository secrets from a newly added workflow.
6. Once separately approved, validate environment enforcement using a dummy
   non-production credential: a different branch, including modified guards, must
   be denied access. No production key or SSH/cloud/database call is needed.
   Keep the seven jobs held until policy evidence, consumer migration and independent
   assurance are accepted in a separate re-enablement PR.

Names referenced by the six workflows (references are not proof of actual existence):
`ORACLE_SSH_PRIVATE_KEY`, `GOOGLE_DRIVE_OAUTH_JSON`, `BRIDGE_APP_DATABASE_URL`,
`IBM_CLOUD_API_KEY`; OCI families `OCI_CLI_{USER,TENANCY,FINGERPRINT,KEY_CONTENT,REGION}`,
`OCI_{USER_OCID,TENANCY_OCID,FINGERPRINT,PRIVATE_KEY,REGION}`,
`OCI_API_{USER,TENANCY,FINGERPRINT,PRIVATE_KEY,REGION}`; bundles `OCI_CONFIG`,
`OCI_CONFIG_B64`, `OCI_CLI_CONFIG`, `OCI_CREDENTIALS_JSON`.
Do not create unused aliases merely because the presence probe references them.
Do not change the database-production policy to admit feature branches.

## Synthetic validation and safe publication

The new workflow has no production secret references, environment or cloud identity.
It runs YAML/Actions syntax checking with checksum-verified actionlint v1.7.12,
bash syntax checks without execution, negative structural tests, existing mocked
IBM/Oracle contracts, preservation of privileged scripts, and a witness that
the six immutable baseline workflows fail the new contract. Runtime tests clear the
environment and block socket connections. These checks do not attest live settings.

Ordinary Issue 881 PR checks can trigger `autopilot-paused-reconcile.yml` via
`workflow_run`, whose diagnostics use Oracle/DB credentials. Therefore the draft
head uses `[skip ci]`. A narrowly gated `create` event runs only the new synthetic
workflow on the exact security branch head; no existing workflow subscribes to
`create`, and no workflow_run subscriber matches the new workflow name.
All 352 baseline workflow trigger headers were examined. None of the changed paths
matches a pull_request_target workflow. No secret-bearing workflow is dispatched or
rerun. Ordinary required CI may remain pending; that is not a PASS or merge approval.

Record the exact candidate SHA, tree and CI run in the PR after completion. Keep
schema validation, synthetic regression results and external policy acceptance
separate. Windows local results from earlier candidates are not evidence for this
reconstructed tree.

Before merge, rollback is closing the draft while retaining its evidence. After a
future approved merge, reverting restores unsafe code paths and needs review.
Preserve external protections; repair availability without restoring repository
production secrets. No merge, deployment or additional settings action is authorized.

Sources:
- [GitHub environment protection and secret access](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)
- [Deployment branch rules and reviewers](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
- [Skip instructions apply to push and pull_request](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/skip-workflow-runs)
- [Create event uses the created branch commit](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#create)
