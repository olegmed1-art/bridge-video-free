# Autopilot Phase 3B — bounded GitHub draft repair

Status: `PREPARED_OWNER_GATE`, not installed on Oracle.

## Purpose

Phase 3B lets Autopilot prepare one small repair in its own branch and open a
draft pull request. It does not merge, write to `main`, change production,
launch Video or TRAIN, or receive an unrestricted user token.

## Confirmed external boundary

As reconciled on 2026-08-30, GitHub reports `main.protected=false` and no active
repository rulesets. No separate GitHub App identity for Oracle is present in
the repository or the staged Autopilot contract. Therefore no GitHub write
credential may be installed until both owner gates below are complete.

1. Protect `main` with a repository ruleset that requires a pull request and
   blocks force pushes and branch deletion. The Autopilot App must not bypass
   that ruleset.
2. Create and install a dedicated GitHub App only on
   `olegmed1-art/bridge-video-free`. Grant Metadata read, Contents read/write,
   Pull requests read/write, and Checks read. Do not grant Administration,
   Actions write, Workflows write, Deployments, Secrets, or Members.

## Pilot policy

- repository: exactly `olegmed1-art/bridge-video-free`;
- base: exactly `main`, bound to a fresh 40-character commit SHA;
- branch: deterministic `autopilot/repair/<fingerprint>` only;
- maximum three UTF-8 regular files, 16 KiB each and 32 KiB total;
- allowed paths: Autopilot Python, its tests, and bounded evidence only;
- forbidden paths: `.github`, `database`, `deploy`, `ops`, and all other paths;
- updates require the exact previous blob SHA;
- create Git objects, one new branch, and one draft PR only;
- no update/delete ref, force push, merge endpoint, production mutation, model
  call, or credential in the task/evidence payload.

## Rollback and failure behavior

Before creating the branch, the executor reads `main` twice and stops if its
SHA changes. Git objects created before the branch are unreferenced and harmless.
If draft-PR creation fails after branch creation, the branch is retained for
inspection; Phase 3B does not delete it automatically. Every missing permission,
stale SHA, unexpected path, oversized change, or identity mismatch fails closed.

## Canary

The first live canary creates only
`docs/evidence/autopilot/phase3b-canary.md` in its namespaced branch and opens a
draft PR. It costs no model tokens. The canary is forbidden until both owner
gates are verified from GitHub primary state.
