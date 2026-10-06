# Generic Linux synthetic CI candidate — not published, not run

Exact candidate scope: the files listed by SOURCE_MANIFEST.json plus the manifest itself. This package contains no owner contract, real hostname/IP/config path, HBA contents, credentials, production data or connection configuration. It adapts the separately reviewed private v2 algorithm to generic fixture names. Generic adaptation and new supervisor still require independent AST/source review and native execution; v2 review is not a generic package PASS.

Concrete proposed runner: the standard GitHub-hosted ubuntu-24.04 runner, non-root enforced. No self-hosted/production runner is used. Python and unshare/mount must already exist; no sudo, installs, services, host permission/policy changes, database access, replay or jobs. Ordinary tests use only temporary /tmp/hba-synthetic-* fixtures and standard-library code. Interpreter socket operations are refused, and subprocesses receive only PATH/LANG. Checkout is the only intended network bootstrap; test code does not access network. This is not a claim of host firewall isolation.

58 methods: 54 ordinary, 4 optional namespace methods. Namespace tests require already permitted user+mount+PID namespaces and --kill-child=KILL; missing/denied capability is separately SKIP/NOT_RUN. Flags are GETFLAGS-only; unsupported flags/inspection refuse. Native capability skips do not prove those capabilities.

Descendant drain proof is implemented in the dedicated single-threaded test process, with no preexisting child processes allowed. It sets its own per-process subreaper attribute, launches only fixture commands, kills its owned group on timeout, reaps the direct child, terminates only kernel-proven adopted child PIDFDs, waits/reaps all adopted descendants and requires ECHILD. The escaped-process-group regression requires at least one actual adopted/reaped grandchild. Namespace timeout separately requires all-owned-descendants drain evidence; neither wrapper exit nor process death alone is accepted. Deadline/failure is UNKNOWN/FAIL. This changes no host security permissions.

Workflow publication proposal:
- Target repository olegmed1-art/bridge-video-free, NEW branch test/hba-generic-linux-ci-20261006 only.
- No main/default merge, PR, workflow dispatch, repository settings or secrets changes.
- Workflow responds only to pushes on that dedicated branch; the job runs only for the repository owner as both actor and triggering_actor, a public repository, run_attempt == '1', and this commit-message marker compared case-insensitively by GitHub ==: [owner-approved] generic hba synthetic validation.
- First publication must use another commit message, so its job is skipped before runner allocation. This still creates a skipped workflow record; publication is not authorized yet.
- An eventual owner-authorized empty commit using the marker (case-insensitive comparison) is a separate native-run action. It is not performed here.
- This branch-push design avoids workflow_dispatch's requirement that a workflow exist in the default branch.
- Public-repository guard prevents spending private-repository runner minutes. Visibility, Actions availability and policy still require read-only verification before publication/run. No free-resource promise is made.
- The workflow has contents:read only, a pinned checkout action, sparse generic-only checkout and persist-credentials:false. No environment, secret mapping, deployment, artifact upload, custom runner creation or provisioning.

Required future review: exact byte hashes, all six Python ASTs plus embedded scripts, 58-method inventory, privacy scan, scope/trigger guard, real timeout/adoption/drain behavior and source pin verification. Local process infrastructure could not execute checks; current generic AST/native/hash-readback status is NOT_RUN. Manifest pins are calculated from intended UTF-8 source, not local disk readback.

Live gates remain closed: directory coherence pre/post, continuous coordination/all-writer exclusion, production mount/config and policy verification, ACL/SELinux/native flags/durability, database admission/rollback and separate live owner authorization. CI success does not open Stage2B or authorize actual HBA execution.

Before any publication, inventory EVERY existing repository push/PR/workflow_run workflow and confirm that creating this branch cannot start unrelated jobs or production actions. The available web channel failed to fetch the workflow tree; current existing-workflow inventory and public visibility are UNVERIFIED. The marker only gates THIS candidate job, not other existing workflows. If that inventory cannot establish safe publication, do not push; report the concrete blocker rather than change settings or disable workflows.

Reference documentation checked during preparation:
- https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow (workflow_dispatch default-branch requirement)
- https://docs.github.com/en/actions/reference/runners/github-hosted-runners (standard ubuntu-24.04 and public/private charging distinction)
- https://github.com/actions/checkout/commit/11bd71901bbe5b1630ceea73d27597364c9af683 (fixed checkout v4.2.2 source)

Workflow repair revision 2: reruns are refused by run_attempt == '1'. Both actor and triggering_actor must match the repository owner. GitHub string == is case-insensitive; the marker is not a case-sensitive/exact authorization token. No whitespace normalization is added. A future approved rerun requires a separately reviewed change, explicit owner authorization and triggering_actor checks; this revision permits no rerun.

Review revision 3 hardening: exact eight-file package-directory allowlist is checked before any local/test import. Symlinks, directories and multiple links refuse. Manifest must have the exact eight pinned paths, exactly two expected test classes and the exact AST-derived 58-method inventory before imports. Unrestricted discover is removed; only those two class names are loaded.

The socket audit hook is only an interpreter-level guard and does not propagate across exec; it is not a network sandbox. Reviewed child commands contain no network calls, but this does not establish host-level network isolation.

Publication/run clearance is owned by the existing recognizer review and remains pending. It must cover existing workflows AND Vercel previews, webhooks and other integrations. Skipping this candidate job cannot suppress those integrations. This agent will not duplicate that broad scan, change integrations/settings or publish before clearance.
