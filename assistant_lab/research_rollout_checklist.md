# ResearchJob rollout checklist

1. Apply `assistant_lab/schema.sql` and `assistant_lab/research_schema.sql` idempotently on the intended Neon target.
2. Verify `assistant_lab.job` accepts only `DDS3_COMPUTE`, `BEN_COMPUTE`, `NOOP`.
3. Verify `assistant_lab.research_job` exists and `canonical_promotion` is hard-false.
4. Roll the Assistant Lab resident worker to the reviewed commit.
5. Verify localhost DDS3 and BEN readiness separately.
6. Submit one known DDS3 ResearchJob and wait for child completion.
7. Finalize it and verify checksum/provenance/methodical derivative.
8. Repeat the same request and verify the same research/child identities are reused.
9. Submit one bounded BEN policy ResearchJob; verify `evidence_class=POLICY_ONLY` and `dds_search_evidence=false`.
10. Do not broaden production routing or promote any result into curriculum/canon automatically.
