# ChatGPT role-dispatch mailbox v2

This draft pull request is a bounded, non-mergeable event mailbox for School Autopilot.

It replaces PR #1150 for new dispatch commands because that retained evidence thread has grown too large for reliable Codex task context. PR #1150 remains immutable and authoritative for its historical receipts.

Only bounded public control envelopes may be posted here. Goal payloads, student data, copyrighted source text, credentials, logs, and private URLs are forbidden.

The canonical task state remains in Neon. A dispatch comment is only a wake signal. A result comment is only a task-bound callback signal; retained evidence remains in canonical storage.

Capacity policy:

- hard limit: 40 dispatches;
- begin rotation at 32 dispatches;
- never delete comments or retained receipts during routine cleanup;
- after zero live dispatches and a seven-day late-delivery window, close without merging and retain this PR and branch as an audit archive.

This pull request must remain draft and must never be merged.
