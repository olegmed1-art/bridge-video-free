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
  -> DDS prefix solving
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

`playline.py` validates card ownership, player order, follow-suit obligations,
trick winners and the remaining position after every card. It produces stable
line and position hashes.

`line_predictor.py` provides a deterministic non-DDS baseline that emits a legal
multi-card principal line before solver exposure. Its purpose is experimental:
every claim can now be refuted at a concrete prefix instead of being dismissed
only as a structural estimate.

### Full-play DDS trajectory

The DDS3 v3.0.0 Python wheel does not export a top-level
`analyse_play_pbn`. `dds_play.py` therefore reconstructs every legal prefix and
uses the supported `solve_board_pbn`, matching the approach used by upstream
DDS consistency tests.

The first pass requests only the optimal score at each prefix and reuses a
`SolverContext`. The more expensive all-card comparison runs only at real value
swings. Each such error stores:

- chosen card;
- DD-regret;
- all equal-optimal cards;
- score of every represented legal alternative;
- whether candidate regret equals the observed value swing.

All prefix scores are normalized to projected final declarer tricks, allowing
one consistent first-error account for both declarer and defense.

### Mid-play declarer and defense tasks

`continuation_tasks.py` creates blind tasks after selected prefixes for both
`declarer_continuation` and `defense_continuation`. Defense is no longer reduced
to the opening lead.

### Human-information mode

`human_view.py` builds a single-dummy view that physically excludes hidden exact
hands. The player sees only their own remaining cards, exposed dummy, auction,
public play and other public metadata. DDS answers remain prohibited.

### Family-safe cross-fit

`crossfit.py` keeps every base deal and all descendants under one
`root_deal_id` and deterministic fold. A model evaluating a transformed
position must exclude that whole family from training.

### Restartable shards

`shard_plan.py` creates deterministic family-safe shards with task SHA-256,
resume keys, expected artifact names and exact coverage checks. A family is
never split across shards.

### Confidence calibration and abstention

`confidence_calibration.py` fits monotonic calibration from out-of-fold TRAIN
losses. Predictions receive calibrated exact probability, support sufficiency
and `requires_human_or_deeper_review` when evidence is inadequate.

### Counterexample candidates and rule versions

`counterexample_candidates.py` detects nearby legal positions whose DDS target
or equal-optimal action set changes. They remain unverified until solved as a
fresh blind discrimination task.

`rule_synthesis.py` stores analyst-supplied technical rule text as a candidate or
confirmed version only when independent transfer, regression and counterexample
evidence support it. It does not invent or change the school's bidding system.

## Balanced follow-ups

Current follow-up source policy is deterministic round-robin across task type,
error code and strain. Derived metrics are marked as a targeted/adversarial
sample and require a matched baseline.

## Model selection

Contract tricks, opening leads, declarer continuations and defense
continuations are selected separately. Selection uses paired per-task loss and
bootstrap 95% intervals. A mixed family ensemble is allowed.

## Durable versioned memory

Locked predictions, DDS facts, errors, provenance, investigations, corrections,
audits and checkpoints are immutable or append-only. Skill profiles, state
history, rule versions, regressions and counterexamples are stored separately by
analyzer revision. A new revision may reinterpret old facts but never rewrite
them.

## Three phased gates

### Main TRAIN gate

Before mass evaluation of boards 10,001–30,000:

- the corpus must really contain 30,000 deals;
- cross-fit must cover all tasks without family leakage;
- shards must cover exactly the fresh main scope;
- legal line and DDS full-play preflights must pass;
- both declarer and defense continuation tasks must exist;
- no paid DDS API may be required.

### Holdout gate

Before opening main validation:

- all TRAIN shards must be durable;
- confidence must be calibrated on out-of-fold TRAIN residuals;
- family-specific paired-bootstrap policy must exist;
- no validation/sealed leakage may exist.

### Stable-skill claim gate

Before calling a skill stable, current-version evidence must include passed blind
counterexamples, a confirmed versioned rule, successful real-world transfer and
a clean regression streak. A stage can finish technically while this claim gate
remains closed.

## Runtime caching

`bootstrap_linux.sh` first installs a cached DDS3 wheel when available. CI saves
that wheel together with the Bazel disk cache under an OS/Python/version key.
Subsequent shards therefore avoid repeated multi-minute solver builds.

## Stage 2 preparation without mass training

After an explicitly authorized corpus expansion to 30,000 deals:

```bash
python prepare_stage2.py --work work/pilot
```

This creates cross-fit metadata, fresh-scope shard manifests, legal line
preflights, a DDS Play preflight, continuation tasks and readiness reports. It
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

Audit database and methodology:

```bash
python run_stage.py audit --work work/pilot --fail-on-error
python methodology_audit.py --work work/pilot --fail-on-error
python stage_gate.py --work work/pilot --stage pilot
```

## Evidence still to be earned

The software for lines, trajectories, cross-fit, continuation tasks, shards,
calibration, counterexample candidates and rule gating is implemented. The
following cannot be fabricated by code and must be earned during Stage 2:

- out-of-fold calibration fitted on actual Stage-2 TRAIN predictions;
- verified blind counterexamples;
- confirmed technical bridge rules;
- successful tournament/real-play transfer;
- stable skill status.

The goal is not to memorize a DDS card. It is to recognize and explain the
bridge mechanism on a new position before DDS is revealed.
