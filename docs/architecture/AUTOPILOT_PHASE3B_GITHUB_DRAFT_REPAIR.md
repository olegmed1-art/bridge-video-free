# Autopilot Phase 3B — bounded GitHub draft repair

Status: `BOUNDED_BROKER_SOURCE_READY / PREVIEW_REDEPLOY_PENDING`.

## Purpose

Phase 3B lets Autopilot prepare one small repair in its own branch and open a
draft pull request. It does not merge, write to `main`, change production,
launch Video or TRAIN, or receive an unrestricted user token.

## Confirmed external boundary

The original 2026-08-30 owner gates are now complete.  Fresh primary-state
readback on 2026-08-31 confirmed:

- active repository ruleset `21895987` protects the default branch, requires a
  pull request, and blocks deletion and non-fast-forward updates with no bypass;
- GitHub App `Bridge School Oracle Autopilot` (`app_id=4776443`) exists with
  Metadata read, Contents read/write, Pull requests read/write, and Checks read;
- isolated Vercel project `bridge-school-autopilot`
  (`prj_KvQo3rPnwNs488hyDiMZ9hMU9d5R`) has no Git connection and contains one
  protected Preview deployment, `dpl_AcsA2EbMhCW3Y2iJmrG6keVzRSzH`; project
  `live` is false and no production domain or alias exists.

The App installation is limited to `olegmed1-art/bridge-video-free`. Its RSA
private key and the separate high-entropy broker ingress secret exist only as
Preview-scoped Vercel environment variables. The currently deployed Preview
passed `/healthz`, but it is pinned to legacy source `a204ba9d`, whose response
still exposes the installation token to its caller. The replacement source
keeps that token inside the broker and is not deployed yet. Neither secret has
been transferred to Oracle, and no credentialed Phase 3B adapter is installed
there. Therefore the live draft-PR canary remains blocked until the bounded
source is independently reviewed, redeployed to Preview, and the separate
Oracle credential boundary is explicitly authorized and verified.

1. Protect `main` with a repository ruleset that requires a pull request and
   blocks force pushes and branch deletion. The Autopilot App must not bypass
   that ruleset. **Verified 2026-08-31.**
2. Create and install a dedicated GitHub App only on
   `olegmed1-art/bridge-video-free`. Grant Metadata read, Contents read/write,
   Pull requests read/write, and Checks read. Do not grant Administration,
   Actions write, Workflows write, Deployments, Secrets, or Members. **App and
   permissions and repository-limited installation verified 2026-08-31.**

## Credential broker boundary

`autopilot_token_broker_service/` is the isolated Vercel source root.  It may:

- keep the GitHub App RSA private key only in a Preview-scoped Vercel secret;
- authenticate the Oracle caller with a separate high-entropy ingress secret;
- mint and retain an internal installation token for exactly
  `olegmed1-art/bridge-video-free`;
- request only Checks read, Contents write, and Pull requests write;
- accept the complete repair request and recompute its canonical action
  fingerprint before minting a credential;
- validate the repository, permissions, and expiry no more than 65 minutes
  away, execute only the exact manifest sequence, and return safe PR evidence;
- never return the installation token or its expiry to Oracle.

It has no merge, ref-update, ref-delete, Actions, Deployments, or production
endpoint. Runtime execution additionally requires `VERCEL_ENV=preview`.
Redirects, unexpected permissions, invalid repository identity,
oversized responses, weak configuration, and stale/long-lived tokens fail
closed. Oracle never receives the App private key or installation token.

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
gates are verified from GitHub primary state, the bounded broker source is
reviewed and deployed to Preview, and the separately authorized Oracle broker
consumer is installed with fail-closed secret handling. The owner gates are
verified, but bounded Preview redeployment and the Oracle consumer gate remain
open, so the canary has not run.
