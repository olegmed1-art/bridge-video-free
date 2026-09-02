# ResearchJob DDS3 acceptance gate

The production acceptance command is `/research-job canary-dds3-idempotency`.

It is intentionally bounded to one known DDS3 `dd_table` request. The gate requires:

- the same ResearchJob and child Assistant Lab job on immediate replay and post-completion replay;
- exactly one child execution attempt;
- Oracle resident worker provenance (`oracle-assistant-lab-1`, `oracle_local_dds3`);
- `engine=DDS3` and `fallback_used=false`;
- the known golden double-dummy table and `par_score_ns=-110`;
- a checksum-valid artifact bound to the methodical derivative;
- `canonical_promotion=false` throughout.

The workflow uses the protected `database-production` environment and temporarily assumes `bridge_school_app` only for the bounded enqueue/finalize path, revoking that membership in an `always()` cleanup step.
