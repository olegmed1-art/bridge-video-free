# Stage 2 DDS — deterministic SolverContext checkpoint

Date: 2026-08-17
Status: TESTED candidate, not production OPERATIONAL

## Goal

Before any mass DDS training, prove on live code that repeated solving of the exact same DealID is deterministic and that the production-oriented DDS3 path actually reuses one `SolverContext` and its transposition-table state. Do not infer reuse from timing alone.

## Primary upstream basis

The gate builds the project against upstream `dds-bridge/dds` v3.0.0 plus pinned SolverContext commit:

`cdd13cf5b700788ac8c1391501b42445b3129b45`

The modern API under test is:

`solve_board(SolverContext&, Deal const&, int target, int solutions, int mode, FutureTricks*)`

Upstream SolverContext owns the per-context search state and lazy transposition-table access. The school gate verifies the behavior directly in its own CI rather than treating upstream documentation alone as operational evidence.

## Deterministic DealID

Gate PBN:

`N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3`

The gate defines DealID as SHA-256 of that normalized PBN text:

`f5db5311219ab908027f07c32f0e48c94d58b1eb47c72ebf33c2217bae4e2bbd`

This is a content identity for the engine regression, not a tournament board number.

## First experiment and failure intelligence

The first live Stage-2 run (`32003749573`) deliberately checked several context/TT hypotheses. It failed because one hypothesis was too strong and was not the actual production requirement: a sibling `SolverContext` constructed after the solve did not expose the same TT pointer.

The failed experiment was retained as negative knowledge. In that same run the actual production invariants were already visible:

- TT was lazy before the first solve;
- the first solve created a non-null TT;
- the same SolverContext retained the same TT object across the repeated solve;
- semantic `FutureTricks` result was identical;
- searched nodes changed from `168200` on the first solve to `109` on the second solve.

The revised gate removed the unrelated sibling-context requirement and strengthened the direct same-context requirement instead.

## Final Stage-2 gate

Files:

- `dds/dds_stage2_context_gate.cpp`
- `dds/run_stage2_context_gate.sh`
- `.github/workflows/dds-stage2-context-gate.yml`

For each process the gate requires:

1. the exact deterministic DealID;
2. one `SolverContext`;
3. first solve through modern `solve_board(SolverContext&)`;
4. TT lazy before first solve and non-null after it;
5. second solve of the exact same DealID through the same context;
6. identical TT object identity before/after the repeated solve;
7. identical semantic `FutureTricks` result (cards/suit/rank/equals/score; node count is instrumentation, not semantic output);
8. fewer searched nodes on the repeated solve as supporting reuse evidence.

The complete executable is then run in two fresh processes. Both replicas must reproduce the same semantic result contract and the same semantic SHA-256 digest.

## Candidate CI evidence

Final PR #111 Stage-2 context run:

- GitHub Actions run: `32003880995`
- conclusion: `success`
- DealID: `f5db5311219ab908027f07c32f0e48c94d58b1eb47c72ebf33c2217bae4e2bbd`
- semantic SHA-256: `0aad096d7cd73923480b7dde609ef117255968656813ccd050090d9f14efa058`
- fresh-process replica 1 nodes: `168200 -> 109`
- fresh-process replica 2 nodes: `168200 -> 109`

The same candidate commit also passed the independent DDS golden smoke:

- run `32003880896`
- conclusion: `success`

## Post-merge confirmation

Merged Stage-2 commit: `1d9fb513a6ec1f9fc454195a330482591a7fd0e5`.

Main-branch confirmation after merge:

- DDS Stage-2 Context Gate run `32004015525`: `success`;
- independent DDS Golden Smoke run `32004015430`: `success`.

Thus the result is not only PR-merge-ref evidence; the same two gates pass on the merged main commit.

## Evidence interpretation

The conclusion is intentionally narrow:

- deterministic semantic replay of this golden DealID is proven;
- the same modern SolverContext reuses the same TT object across repeated solve;
- repeated solve performs dramatically less search in both fresh-process replicas;
- node reduction is supporting evidence only and is not accepted by itself as proof of reuse;
- this does not yet prove every branch/deal pattern, long-run memory behavior, thread scaling, or production service orchestration.

## Stage-2 exit decision

Deterministic replay/context-reuse gate: **PASS**.

`dds_core` remains `TESTED`, not `OPERATIONAL`, because production orchestration, corpus-scale persistence/checkpointing and repeated operational runs are separate gates.

Mass DDS training is no longer blocked by the deterministic engine gate itself, but it must still start only through the staged corpus process with checkpoints and stage reports. The first mass stage remains the 10,000-deal pilot/debug corpus, not the full corpus.

## Next ordered work

1. preserve the Stage-2 gate as a required regression for DDS changes;
2. wire the modern SolverContext path into the school's mass DDS runner rather than using a fresh process/context per repeated decision;
3. attach stable DealID and run/checkpoint identity to every corpus solve;
4. persist all legal-card evaluations / equal-optimal moves / regret and trajectory data according to the accepted DDS-learning specification;
5. run only the 10,000-deal pilot first, review errors/performance/reuse metrics, and do not expand to the 30,000/40,000 stages until that pilot passes its own evidence gate.
