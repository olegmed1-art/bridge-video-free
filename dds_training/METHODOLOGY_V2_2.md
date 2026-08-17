# DDS Learning Methodology v2.2

## Status

The 10,000-deal pilot is a completed technical benchmark. Its immutable PBN,
locked predictions, DDS facts, validation/sealed results and append-only history
remain unchanged. Version 2.2 changes how future evidence is generated,
classified and used for stage advancement.

The pilot demonstrated a real improvement of the local adaptive predictor on
fresh holdouts, especially for contract-trick estimation. It did **not** yet
establish stable bridge-play skills, full-play competence or a change to the
base model weights.

## Pilot postmortem

### What was valid

- Predictions were locked before DDS exposure.
- TRAIN, validation and sealed test were isolated.
- The sealed test was opened once with per-task provenance.
- DDS3 was cross-checked against an independent solver.
- The adaptive predictor reduced validation contract MAE from 1.551 to 1.107.
- The corresponding paired validation loss difference was large and robust.
- Opening-lead regret improved only slightly; it must be treated as a separate
  result rather than being hidden inside one combined score.

### What required correction

1. **One-sided follow-up generation.** A global error-severity sort selected only
   opening-lead sources, so the derived set contained no declarer/trick tasks.
2. **Source-contaminated transfer evidence.** Symmetry and perturbation probes
   were generated from TRAIN source deals and then counted like independent
   transfer. They are useful reinforcement, not proof of generalization.
3. **Adversarial sample misinterpretation.** Derived tasks were deliberately
   difficult neighborhoods of errors; their percentages are not comparable with
   random validation percentages without a matched baseline.
4. **Metadata inconsistency.** Seat rotations changed hands/declarer/leader but
   previously left Dealer and vulnerability unchanged.
5. **Queue expansion.** One spaced-review row per error and offset created tens of
   thousands of operational queue rows.
6. **Single-score model selection.** Contract and opening-lead quality were added
   together without paired uncertainty or family-specific decisions.
7. **Insufficient game-line evidence.** Overclaims without a pre-DDS card line
   could be honestly rejected, but not localized to the first card-level
   refutation.
8. **No full-play trajectories.** The pilot tested trick estimates and opening
   leads, not later declarer/defensive decisions.
9. **No verified counterexamples or versioned bridge rules.** Statistical
   improvement alone is not an acquired stable bridge skill.
10. **Uncalibrated confidence.** Confidence labels were heuristic and did not
    consistently predict lower error.

## Corrections implemented in v2.2

### Balanced follow-ups

TRAIN error sources are selected deterministically by round-robin across:

- task type;
- error code;
- strain.

Every selected source produces:

- an exact blind regression retest;
- seat-rotation invariance probes;
- a suit-renaming invariance probe;
- nearby legal rank-swap perturbations.

The output records source group, selection policy, variant kind and evidence
role. The sample is explicitly marked targeted/adversarial.

### Evidence roles

- `regression`: exact fresh attempt on a known problem position;
- `reinforcement`: same-source symmetry/perturbation/discrimination probe;
- `transfer`: genuinely unseen or cross-fitted source;
- `real_world`: independent real-play transfer evidence;
- `counterexample`: similar-looking position requiring a different rule/action.

Only independent `transfer` and `real_world` evidence can promote a skill to
confirmed/stable. Reinforcement can expose brittleness and guide training but
cannot prove generalization.

### Model selection

Models are compared separately for:

- contract-trick estimation;
- opening-lead selection.

The comparison uses paired per-task losses and deterministic bootstrap
confidence intervals. A candidate replaces the baseline for a family only when
its improvement is both practically above a configured minimum and the 95%
paired interval remains below zero. A mixed family ensemble is allowed.

### Bounded spaced review

Exact source errors remain in immutable error/regression tables. Operational
spaced-review requests are aggregated by skill, offset and due-evaluation
bucket, with a capped requested-task count. This preserves provenance while
preventing queue explosion at 30k/50k scale.

### Methodology gate

Database integrity and methodological readiness are now separate checks. A
stage can be technically complete but blocked from expansion when, for example:

- a task/error family has no follow-ups;
- same-source probes are counted as transfer;
- validation/sealed leakage exists;
- required investigations remain open;
- no report exists.

Expansion also continues to require explicit user approval.

## Required Stage 2 additions before claiming play/defense skill

1. **Line-bearing declarer tasks.** The blind answer must include a legal planned
   card sequence or decision tree; DDS then finds the first optimal-defense
   refutation.
2. **Defense continuation tasks.** Add critical decisions after the opening lead:
   continuation, switch, unblock, force, trump promotion, entry destruction and
   timing.
3. **Full Play trajectories.** Store DD position value after every card and
   classify first error, restored losses and later compensating errors.
4. **Cross-fitted transfer.** Train models on folds that exclude the source deal
   and its family before evaluating transformations; use fresh boards 10,001–
   30,000 as the primary independent transfer corpus.
5. **Counterexample generator.** For each candidate rule, generate/locate similar
   positions where the action changes; require successful discrimination before
   rule promotion.
6. **Versioned bridge-rule synthesis.** Convert repeated DDS evidence into a
   candidate bridge principle only after independent support and counterexamples;
   keep every revision and contradiction.
7. **Confidence calibration.** Fit confidence on out-of-fold residuals and report
   calibration curves/coverage, not only labels.
8. **Human-information mode.** Keep double-dummy technical training separate
   from single-dummy decisions using only the player's available information.
9. **Real-play transfer.** Evaluate the final Stage 2 analyzer on tournament PBN
   with Auction/Play, separately from synthetic random deals.
10. **Durable sharding.** Run large stages in restartable shards with artifacts
    and snapshots per shard instead of relying on one long runner job.

## Advancement policy

Stage 2 must not start automatically. After the v2.2 smoke/regression suite is
green, the user reviews the pilot postmortem and explicitly authorizes the main
30,000-deal stage.
