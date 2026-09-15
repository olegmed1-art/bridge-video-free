# Slavik bounded publication — guarded draft

Date: 2026-09-15. Governance: ASSURED.
Base: `c13a08515fbaf79493a47b9cf1e8daa0e584051a`.
Status: `DRAFT_VERIFIED / ACTIVATION_BLOCKED_PROVENANCE_ISSUER_MISSING`.

## Authority and outcome

The Director explicitly authorized modifying the existing workflow and granting
`Contents: write` for `olegmed1-art/bridge-video-free`. This change adds a guarded
`publish` job to `autopilot-codex-event-callback.yml`; it does not create another
workflow, executor, automation, server or credential. Existing ACK and terminal
jobs retain repository read-only permissions. Publication alone is not CI,
verification, merge, deployment or production acceptance.

**Nothing here has been activated in production.** Migration 0336 is draft code,
not an applied Neon migration. The repository flag remains unchanged. No permits
have been issued. No live event-to-publication canary has been run. No PAT was
read, copied into an environment, or used. No main/production/Canon/media write,
merge, force push or branch-protection change is included.

## Receiver contract

An authenticated `chatgpt-codex-connector[bot]` issue comment on the target PR has
exactly two lines, optionally followed by the native `View task` link:

1. `AUTOPILOT_CODEX_PUBLICATION_V1`
2. One JSON object with `dispatch_id`, integer `dispatch_epoch`, `role`,
   `task_fingerprint`, integer `target_pr`, `expected_head_sha`, integer
   `command_comment_id`, and `files`.

Each file has only `path`, `base_blob_sha` (Git blob SHA-1 at the expected head),
`content` (UTF-8 text), and `sha256` (replacement UTF-8 bytes). At most eight
existing regular files, 32 KiB total replacement bytes and 60,000 envelope bytes
are accepted. No deletion, rename, new files, binary, mode change or symlink is
supported. The request must not also contain a terminal-result envelope.

All paths must be exact members of the canonical assignment's
`expected_changed_files`. Missing/wildcard/oversize focus is BLOCKED. The initial
publisher supports narrowly constrained Python, SQL and evidence paths; it
cannot modify its receiver/import closure, workflows, orchestration, secrets,
governance, Canon or deployment configuration. This intentionally does not cover
every repair, including large files and new test files.

The receiver:

- checks immutable repository, owner, app and bot IDs;
- fetches the actual owner command and matches every dispatch binding and mode;
- obtains a mandatory SQL authorization with outbox/task/proof/permit/role/work
  locks retained through publication and terminal ingestion;
- compares the complete current `get_dispatch_assignment` against the command;
- requires an ACTIVE current work item, enabled repair-capable repository role,
  accepted ACK, SENT task, unexpired callback and exact provenance permit;
- re-reads the owner command, bot request, PR and unprotected existing branch;
- uses GitHub `createCommitOnBranch(expectedHeadOid=...)` for atomic exact-head
  compare-and-swap; the only allowed API write creates one commit on that branch;
- checks actual single parent, commit identity, exact modified-file set, tree
  modes, parent trees, every blob SHA and fresh live PR head;
- records `BOUNDED_REPAIR_PUBLISHED` through the existing terminal RPC only after
  readback; it does not claim CI has passed.

The privileged job always runs immutable default-branch receiver code at
`github.workflow_sha`. It never checks out, imports, installs or runs the proposed
tree. Tokens are passed only to that trusted receiver step.

## Mandatory provenance barrier

Public dispatch identifiers plus the pinned bot's identity **do not prove** the
output was produced by the acknowledged owner task. A different invocation of
the same bot could copy the public identifiers. The draft therefore requires an
exact permit keyed by dispatch, owner command ID, publication comment ID and
canonical payload fingerprint (which includes the comment identity).

Migration 0336 creates an **empty** permit ledger, exposes only the locking
authorization RPC to `autopilot_callback`, and grants no runtime/callback permit
writer. `provenance_evidence_sha256` is a reserved opaque reference, **not a SQL
proof that retained evidence exists or that task origin was verified**. There is
no permit issuer in this change. Do not populate it with inferred/self-asserted
proof, grant the bot a writer, or enable the flag as a shortcut.

Before activation, implement/review a trustworthy permit issuer that verifies
retained task-to-owner-command evidence, or establish an enforced and documented
equivalent invocation boundary. This is a remaining technical dependency, not a
request for a broader PAT. The feature flag is a second guard, not a substitute
for the permit gate.

## Failure and replay

`BLOCKED`: rejected before a GitHub write was attempted.
`PUBLICATION_OUTCOME_UNKNOWN`: mutation attempted, actual result not confirmed.
`PUBLISHED_RECEIPT_PENDING`: live publication readback succeeded, but SQL receipt
or commit acknowledgement failed. These are not terminal success receipts.

GitHub and PostgreSQL do not share a transaction. Never automatically undo the
GitHub commit after a SQL failure. A retry checks exact parent, complete changed
paths and content plus the payload marker before recovering a receipt; a changed
unrelated head is never adopted or overwritten. Completed exact replay is
read-only. Expired/revoked incomplete permits require explicit recovery review;
they are not silently renewed. A last-second deadline check and retained locks
bound the mutation window, but do not prove distributed-system atomicity.

## Verification evidence

- Existing Autopilot Python suite, including the new publisher: **18,675 PASS**.
- Focused callback/workflow/publication suite after four additional partial-
  outcome reporting tests: **67 PASS**.
- Independent Git `hash-object --stdin`: four UTF-8/empty/CRLF fixtures agree with
  computed blob IDs.
- Workflow YAML parse and `git diff --check`: PASS.
- PostgreSQL **18.3**, PGlite **0.5.8**, `bridge_ci_owner` **NOSUPERUSER**:
  all 36 Autopilot migrations from 0300 through 0336 (including 0324a, excluding
  unrelated Canon migrations 0329/0330) applied; real SQL regressions 332, 333 and
  336 passed; empty-ledger rollback 0336 passed. Populated-ledger rollback refused
  and retained its evidence. The rollback locks the ledger before checking it.
- SQL 336 verifies the valid fenced REPAIR path, missing permit, missing/null
  command fields, wrong digest/comment, revocation/expiry, disabled role,
  paused/stale work mapping, callback deadline and least-privilege grants.
- Logically independent Red Team identified atomic-CAS, provenance, partial-
  outcome, terminal-state and paused-work risks. The implemented draft addresses
  the first, third, fourth and fifth; the empty permit gate contains the unresolved
  provenance-issuer dependency. Git/PostgreSQL provide independent algorithm/
  engine checks, not a blanket production-security certification.

This is not full repository CI, live Neon, multi-process concurrency proof,
autonomous CI proof or a production canary. Migration 0336 must still undergo the
normal full PostgreSQL CI before any separately authorized application.

## Activation and rollback

1. Resolve provenance issuer and independently review exact current code.
2. Obtain green current-head CI. Review and merge through the protected normal
   process; this draft does not request an automatic merge.
3. Separately authorize/apply 0336 through the established migration process.
4. Run one explicitly bounded existing-branch canary with a genuinely verified
   permit, checking event, publication, SQL receipt and separate verification.
5. Only then enable ordinary traffic and update the existing Slavik sender's
   REPAIR instruction to emit publication requests. Do not change READ_ONLY or
   VERIFY permissions. No new chat/scheduler/polling layer is needed.

Rollback: turn off `AUTOPILOT_BOUNDED_PUBLICATION_ENABLED`, drain the publish job,
then revert this PR through normal review. Existing ACK/terminal jobs are
unaffected. The SQL rollback refuses to erase any populated permit ledger;
retained evidence requires a separate preservation/recovery plan. Never rewind
target branches or revoke unrelated capabilities automatically.

## Official API boundaries checked

- [GitHub atomic commit input](https://docs.github.com/en/graphql/reference/commits#createcommitonbranchinput)
  supplies `expectedHeadOid`, unlike REST non-force update-ref alone.
- [GitHub workflow triggering](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)
  states that `GITHUB_TOKEN` pushes do not start push workflows; generated PR
  opened/synchronize/reopened workflows require approval. Therefore this write
  permission alone does not prove unattended post-publication CI.
- [OpenAI GitHub integration](https://learn.chatgpt.com/docs/third-party/github)
  documents task invocation and branch fixes with permission, but does not by
  itself establish the missing task-origin attestation contract.
