# DDS Training — local, zero paid DDS API

This directory contains the sports-bridge DDS learning loop, reproducible
corpora, full-play diagnostics and durable experience memory.

Current algorithm revision: **`dds-learning-v2.3`**.

Canonical Russian specification:
[ALGORITHM_DDS_LEARNING_V2_3_RU.md](ALGORITHM_DDS_LEARNING_V2_3_RU.md).

Pilot postmortem and the v2.2 evidence correction are preserved in
[METHODOLOGY_V2_2.md](METHODOLOGY_V2_2.md).

## Fixed architecture

- Mathematical engine: **DDS3 v3.0.0**, built locally from official source.
- Runtime: Linux/WSL2 + Python 3.14.
- Storage: local PBN/JSONL + SQLite.
- Raw PBN never exposes DDS answers before prediction.
- No paid DDS API and no per-request DDS charge.
- Evaluation is fail-closed: `--start` and `DDS_TRAINING_CONFIRM=YES` are both
  required.
- Sealed tests are explicit, isolated and opened once per benchmark scope.
- A new mass stage always requires a separate user command.

## Stages

1. `pilot`: 10,000 deals — completed.
2. `main`: expand the same deterministic corpus to 30,000 total deals; only
   fresh boards 10,001–30,000 belong to the main evaluation scope.
3. `targeted`: approximately 10,000 positions chosen from demonstrated weak
   skills rather than another random corpus.

Completing calculations is not enough to claim skill acquisition. Database,
methodology, transfer and real-play evidence are checked separately.

## Blind-first pipeline

```text
RAW PBN
  -> family-safe split/fold
  -> blind task
  -> locked prediction + legal line
  -> DDS / AnalysePlay
  -> DD regret and value trajectory
  -> immutable evidence
  -> regression / reinforcement / transfer / counterexample
  -> versioned skill and rule
  -> report and explicit next-stage decision
```

## Evidence roles

- `direct`: ordinary TRAIN result;
- `error_pattern`: recurring error family;
- `regression`: fresh blind attempt on the exact known problem;
- `reinforcement`: same-source rotation, suit rename or nearby perturbation;
- `transfer`: genuinely unseen or family-excluded cross-fit source;
- `real_world`: independent tournament/real-play evidence;
- `counterexample`: similar position where the action or rule must change.

Same-source symmetry and perturbation may expose brittleness, but cannot by
themselves promote a skill. Only independent `transfer` and `real_world`
evidence count toward `confirmed` / `stable`.

## Implemented v2.3 modules

### Legal line-bearing analysis

`playline.py` validates:

- card ownership;
- player order;
- follow-suit obligations;
- trick winners;
- remaining position after every card;
- stable line and position hashes.

`line_predictor.py` provides a deterministic non-DDS baseline that emits a legal
multi-card principal line before solver exposure. It is intentionally simple;
its purpose is to make every claim refutable at a concrete card prefix.

### Full-play DDS trajectory

`dds_play.py` uses `analyse_play_pbn` after legal validation. DDS values are
normalized to one constant scale: projected final declarer tricks. The system
can then record:

- first declarer loss;
- first defensive gift;
- later restoration or squandering;
- gross and unrecovered damage;
- integration/value-definition invariant violations;
- the first prefix that refutes a claimed result.

### Mid-play declarer and defense tasks

`continuation_tasks.py` creates blind tasks after selected prefixes for both:

- `declarer_continuation`;
- `defense_continuation`.

This expands defense beyond the opening lead to switches, continuation, timing,
unblocking, entries, force and trump control.

### Family-safe cross-fit

`crossfit.py` assigns every base deal and all descendants to the same
`root_deal_id` and deterministic fold. A model evaluating a transformed
position must exclude the whole family from training.

### Restartable shards

`shard_plan.py` creates deterministic family-safe shards with:

- task SHA-256;
- resume key;
- expected artifact name;
- split/type/fold counts;
- exact coverage checks.

One family is never split between shards.

### Confidence calibration and abstention

`confidence_calibration.py` fits monotonic calibration from **out-of-fold TRAIN**
losses. Predictions receive:

- calibrated probability of exact success;
- support sufficiency;
- `requires_human_or_deeper_review` when probability or evidence is inadequate.

### Counterexample candidates

`counterexample_candidates.py` detects nearby legal perturbations that change:

- the DDS trick target; or
- the equal-optimal opening-lead set.

They remain unverified candidates until solved as a fresh blind discrimination
task. The system never calls them learned counterexamples automatically.

## Balanced follow-ups

Current follow-up source policy is deterministic round-robin across:

1. task type;
2. error code;
3. strain.

For each source the system can create exact regression, rotations, suit
permutation and legal rank-swap perturbations. Derived metrics are marked as a
targeted/adversarial sample and require a matched baseline.

## Model selection

Families are selected separately:

- contract-trick estimation;
- opening lead;
- declarer continuation;
- defense continuation.

Selection uses paired per-task loss and bootstrap 95% intervals. A candidate
replaces a baseline only when improvement is practically and statistically
credible. A mixed family ensemble is allowed.

## Durable versioned memory

Immutable or append-only evidence includes locked predictions, DDS results,
errors, run/task provenance, investigations, corrections, audits and
checkpoints.

Learning interpretation is versioned separately through:

- `skill_profile_versions`;
- skill-state history;
- rule versions;
- regression cases and multi-skill links;
- counterexamples;
- bounded spaced-review queues.

A new algorithm revision may reinterpret old facts, but it never rewrites them.

## Three phased gates

### Main TRAIN gate

Before mass evaluation of boards 10,001–30,000:

- corpus count must be 30,000;
- cross-fit must cover all tasks without family leakage;
- shards must cover exactly the fresh main scope;
- legal line preflight must pass;
- DDS full-play normalization must pass;
- both declarer and defense continuation tasks must exist;
- no paid DDS API may be required.

### Holdout gate

Before opening main validation:

- all TRAIN shards must be durable;
- confidence must be calibrated on out-of-fold TRAIN residuals;
- family-specific paired-bootstrap policy must be present;
- no validation/sealed leakage may exist.

### Stable-skill claim gate

Before calling a skill stable:

- blind counterexamples must be passed;
- a versioned rule must be confirmed;
- real-world transfer must succeed;
- regression streak and transfer thresholds must pass.

A stage may finish technically while this claim gate remains closed.

## Stage 2 preparation without mass training

After an explicitly authorized corpus expansion to 30,000 deals:

```bash
python prepare_stage2.py --work work/pilot
```

This command creates cross-fit metadata, fresh-scope shard manifests, legal line
preflights, one DDS Play preflight, continuation tasks and readiness reports. It
does **not** launch mass DDS evaluation.

Check gates:

```bash
python stage2_readiness.py --work work/pilot --require main_train
python stage2_readiness.py --work work/pilot --require holdout
python stage2_readiness.py --work work/pilot --require skill_claim
```

## Core commands

Prepare the pilot corpus without DDS exposure:

```bash
python run_stage.py prepare --stage pilot --out work/pilot
```

Expand the deterministic corpus after explicit approval:

```bash
python run_stage.py prepare --stage main --out work/pilot
```

Evaluate already locked predictions:

```bash
DDS_TRAINING_CONFIRM=YES python run_stage.py evaluate \
  --stage main \
  --work work/pilot \
  --predictions locked_predictions.jsonl \
  --splits train \
  --start
```

Audit database and current methodology:

```bash
python run_stage.py audit --work work/pilot --fail-on-error
python methodology_audit.py --work work/pilot --fail-on-error
python stage_gate.py --work work/pilot --stage pilot
```

## Remaining evidence, not software claims

The machinery for lines, trajectories, cross-fit, continuation tasks, shards,
calibration and counterexample candidates is implemented. The following cannot
be fabricated by code and must be earned during Stage 2:

- out-of-fold calibration fitted on real Stage-2 TRAIN predictions;
- verified blind counterexamples;
- confirmed versioned bridge rules;
- successful tournament/real-play transfer;
- stable skill status.

The goal is not to memorize a DDS card. It is to recognize and explain the
bridge mechanism on a new position before DDS is revealed.
