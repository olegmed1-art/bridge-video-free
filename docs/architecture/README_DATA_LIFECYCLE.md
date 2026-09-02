# Active data lifecycle authority

The active project-wide storage/mutation/deletion authority is:

- `data_lifecycle_policy_v1.md` — normative policy;
- `data_lifecycle_policy_v1_operational_checklist.md` — operational checklist;
- `data_lifecycle_policy_v1_status.md` — implementation status and remaining enforcement work.

This policy supersedes older blanket «do not delete anything without owner command» wording for technical/transient/duplicate data while preserving owner-only deletion protection for originals and unique/canonical data.

Deletion is fail-closed when evidence is incomplete.
