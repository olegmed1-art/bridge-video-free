# Autopilot mailbox pre-rotation bootstrap

- Source active mailbox: #1703
- Observed dispatch usage: 34 / 40
- Trigger: PREPARE_ROTATION (80% capacity threshold)
- Scope: repository-side preparation only
- Production cutover: OWNER-GATED
- Merge: OWNER-GATED

This bootstrap intentionally contains no production migration and no credentials.
The PR number created from this branch becomes the candidate mailbox identity for
the subsequent bounded rotation migration.
