# Autopilot mailbox rotation v1

## Purpose

GitHub mailbox pull requests are bounded transport and audit surfaces, not the
canonical task store. Canonical work, task, dispatch, receipt, and retained
evidence state lives in Neon. GitHub comments prove external delivery and make
the provider interaction inspectable.

PR #1150 accumulated 421 dispatch records and 421 discussion comments (about
1,009,504 body characters). With no capacity gate, every role and generation
shared the same permanent thread. Codex then began returning provider-generic
failures while equivalent reviews on small pull requests succeeded.

## Capacity and rotation

- One mailbox accepts at most 40 dispatches.
- The database rejects the next dispatch with
  `AUTOPILOT_MAILBOX_ROTATION_REQUIRED` before publication.
- Operators should start rotation at 32 dispatches (80% capacity), leaving
  headroom for terminal replies and incident handling.
- A replacement mailbox is a draft, non-mergeable pull request with one small
  immutable file and a pinned exact head.
- Rotation is atomic: pause the planner, verify zero non-terminal dispatches,
  register the replacement, deploy receiver/runtime support, switch new work,
  read back invariants, then resume with one READ_ONLY canary.

## Retention and cleanup

Never delete mailbox comments automatically. They contain three evidence
classes:

1. owner commands and exact dispatch envelopes;
2. provider-authenticated ACK/result comments;
3. diagnostic failures needed to explain fail-closed decisions.

After all dispatches are terminal and exactly-once receipts are present:

- mark the old mailbox `RETAINED` in the registry;
- stop publishing new commands to it;
- keep callback ingestion enabled during a seven-day late-delivery window;
- then close the PR without merging and remove it from the live webhook filter;
- retain the PR, branch, comments, Neon receipts, and registry row;
- never delete retained receipts or evidence during routine cleanup.

Closing is archival, not deletion. Reopen only for verified late-delivery
recovery. If GitHub evidence ever needs external preservation, export a
read-only JSON snapshot and store its SHA-256 in the operational change record;
do not copy the full discussion into the next mailbox.

## Recovery

Before the first v2 dispatch, migration 0344 has a clean rollback. Once v2 has
retained dispatch evidence, rollback fails closed and recovery is forward-only:
register another mailbox, preserve both older mailboxes, and repoint new work.
