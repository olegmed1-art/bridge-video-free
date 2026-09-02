# Data Lifecycle Policy v1.0 — implementation status

Date: 2026-08-18

## Authority

`data_lifecycle_policy_v1.md` is the active project-wide policy for storage, mutation and deletion.

It explicitly supersedes the old blanket rule «ничего не удалять без команды пользователя» for technical/transient/duplicate data. The owner-only deletion rule remains mandatory for P0 originals and protected unique/canonical data.

## Implemented now

- P0–P7 classification defined.
- Deletion Gate defined as fail-closed.
- Drive-first durable storage rule defined for large P0/P2/P3 files.
- Neon metadata/provenance/verification role defined.
- GitHub Actions temporary-storage role defined.
- Recovery replacement gate defined.
- Failed-experiment evidence-retention rule defined.
- DDS milestone-specific retention rule defined.
- Audit requirements for automatic deletion defined.
- Operational checklist added.

## Existing system behavior already compatible

- applied database migrations are immutable and corrected by forward migration;
- checkpoint/regression/recovery histories are append-only where implemented;
- user media/transcripts are prohibited as GitHub Actions artifacts by the security/deployment algorithm;
- tested and operational status are distinguished;
- failed candidates may be quarantined rather than silently erased.

## Remaining implementation work

The policy is authoritative immediately, but not every workflow currently enforces all lifecycle checks in code. Workflow-by-workflow hardening should add machine-readable class/retention metadata, durable-copy verification and deletion audit before enabling automatic artifact deletion.

Until a workflow has that enforcement, automatic deletion remains fail-closed and objects are retained when proof is incomplete.
