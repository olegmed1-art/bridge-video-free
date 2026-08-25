# FinOps runtime policy

This policy defines the first production-safe optimization layer for Oracle compute jobs.

- Local runtime definitively absent (for example connection refused): terminal infrastructure failure; do not retry the same job.
- HTTP 429, HTTP 5xx, timeout, and ambiguous transport failure: retryable within the bounded job retry budget.
- Invalid payload, unsupported operation, invalid configuration, or contract violation: terminal failure.
- Heavy compute remains on Oracle; GitHub Actions is orchestration/CI only.
- Every future compute path must expose enough provenance for FinOps accounting: job kind, attempts, claimed/completed timestamps, worker/execution path and terminal error class.

The first enforced implementation covers BEN localhost runtime availability. DDS3/video/research paths should use the same classification before being promoted to the shared scheduler.
