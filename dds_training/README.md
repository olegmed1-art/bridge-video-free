# DDS Training — local solver, zero paid DDS API

This directory contains the sports-bridge DDS learning loop, reproducible
corpora, full-play diagnostics and durable experience memory.

Current algorithm revision: **`dds-learning-v2.3`**.

Canonical Russian specifications:

- [ALGORITHM_DDS_LEARNING_V2_3_RU.md](ALGORITHM_DDS_LEARNING_V2_3_RU.md)
- [TESTING_ALGORITHM_V1_RU.md](TESTING_ALGORITHM_V1_RU.md)
- [METHODOLOGY_V2_2.md](METHODOLOGY_V2_2.md) — preserved pilot postmortem

## Fixed architecture

- Mathematical engine: local **DDS3 v3.0.0** at exact source commit
  `37c8a79f4c67c55d1a309ccb66dd00cb58af464a`.
- Runtime: Linux/WSL2 + Python 3.14.
- Storage: local PBN/JSONL + SQLite.
- Raw PBN never exposes DDS answers before the prediction is locked.
- No paid DDS API and no per-request DDS charge.
- Validation and sealed test cannot update skill/rule memory.
- A new mass stage always requires a separate explicit user decision.

## Authorization boundary

Mass DDS evaluation cannot be started by a committed GitHub workflow or by
`DDS_TRAINING_CONFIRM=YES` alone.

A valid run requires all of the following:

1. `--start`;
2. `DDS_TRAINING_CONFIRM=YES`;
3. an unexpired `dds-run-authorization-v1` manifest;
4. a separate approval token whose plaintext is not stored in the manifest;
5. exact agreement on algorithm version, stage, split set, corpus SHA-256,
   locked-prediction SHA-256, sealed permission and maximum task count.

The manifest states `automatic_issuance_allowed: false`. It is created only
after an explicit user decision. `run_stage.py evaluate` fails before SQLite is
opened when that authorization context is absent. Solver entrypoints repeat the
same check as defense in depth.

The only authorized command facade is:

```bash
DDS_RUN_APPROVAL_TOKEN='separate-expiring-token' \
python authorized_run_stage.py \
  --authorization /secure/path/dds-run-authorization.json \
  -- \
  --stage main \
  --work work/pilot \
  --predictions /secure/path/locked_predictions_main_train.jsonl \
  --splits train \
  --start
```

Sealed evaluation additionally requires an authorization with
`allow_sealed=true`; `sealed_test` must run alone.

## Stages

1. `pilot`: 10,000 deals — completed historical benchmark.
2. `main`: deterministic expansion to 30,000 total deals; only fresh boards
   10,001–30,000 belong to the new main scope.
3. `targeted`: approximately 10,000 positions selected from demonstrated weak
   skills, not another random corpus.

Completing calculations is not enough to claim skill acquisition. Database,
methodology, independent transfer, counterexamples and real-play evidence are
checked separately.

## Blind-first pipeline

```text
RAW PBN
  -> family-safe split/fold
  -> blind task
  -> locked prediction + legal line
  -> explicit data-bound authorization
  -> local DDS prefix solving
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

### Legal lines and full-play DDS trajectory

`playline.py` validates card ownership, player order, follow-suit obligations,
trick winners and remaining position after every card.

`line_predictor.py` emits a deterministic legal pre-DDS principal line so an
incorrect claim can be refuted at a concrete prefix.

`dds_play.py` reconstructs legal prefixes and uses the supported DDS3
`solve_board_pbn` API. Prefix values are normalized to projected final declarer
tricks. At a genuine swing it records the chosen card, DD-regret, all
equal-optimal cards, represented alternatives and the first error.

### Declarer and defense continuations

`continuation_tasks.py` creates blind `declarer_continuation` and
`defense_continuation` tasks after selected prefixes. Defense is not reduced to
the opening lead.

### Human-information mode

`human_view.py` physically excludes hidden exact hands. The decision-maker sees
only their own remaining cards, exposed dummy, auction, public play and other
public metadata. DDS answers remain prohibited.

### Family-safe cross-fit and restartable shards

`crossfit.py` keeps each root deal and all descendants in one deterministic fold.
`shard_plan.py` keeps a family in one restartable shard and records exact task
coverage, hashes, resume keys and artifact names.

### Confidence, counterexamples and rules

`confidence_calibration.py` fits monotonic confidence only from out-of-fold TRAIN
losses and marks insufficiently supported answers for deeper review.

`counterexample_candidates.py` finds nearby positions whose optimal action set
changes. `rule_synthesis.py` can promote analyst-supplied technical rule text
only after independent transfer, regression and counterexample evidence. It does
not invent or change the school's bidding system.

## Durable memory

Locked predictions, DDS facts, errors, run provenance, investigations,
corrections, audits and checkpoints are immutable or append-only. Skill
profiles, state history, rule versions, regressions and counterexamples are
stored separately by analyzer revision. A new revision may reinterpret old
facts but never rewrite them.

## Three phased gates

### Main TRAIN gate

Before mass evaluation of boards 10,001–30,000:

- corpus count and hashes must match;
- cross-fit must cover all tasks without family leakage;
- shards must exactly cover the fresh main scope;
- legal-line and DDS full-play preflights must pass;
- declarer and defense continuation tasks must exist;
- no paid DDS API may be required;
- an explicit data-bound authorization must match the locked input.

### Holdout gate

Before opening main validation:

- every TRAIN shard must be durable;
- confidence must be calibrated on out-of-fold TRAIN residuals;
- family-specific paired-bootstrap selection policy must exist;
- no validation/sealed leakage may exist;
- a separate authorization must cover validation.

### Stable-skill claim gate

Before calling a skill stable, current-version evidence must include passed blind
counterexamples, a confirmed versioned rule, successful real-world transfer and
a clean regression streak. A stage may finish technically while this claim gate
remains closed.

## Reproducible bootstrap and caching

`bootstrap_linux.sh` pins:

- DDS source commit;
- Bazelisk version and SHA-256;
- Bazel version;
- Python packaging tool versions.

A cached DDS wheel is installed only after preflight succeeds. A bad or empty
wheel is deleted and rebuilt. The workflow performs a second bootstrap in the
same job and requires verified warm-cache restoration.

## Safe preparation commands

Prepare the pilot corpus without DDS exposure:

```bash
python run_stage.py prepare --stage pilot --out work/pilot
```

Expand the deterministic corpus without mass DDS evaluation:

```bash
python run_stage.py prepare --stage main --out work/pilot
python prepare_stage2.py --work work/pilot
```

Check phased readiness:

```bash
python stage2_readiness.py --work work/pilot --require main_train
python stage2_readiness.py --work work/pilot --require holdout
python stage2_readiness.py --work work/pilot --require skill_claim
```

Audit and reporting commands do not call DDS:

```bash
python run_stage.py audit --work work/pilot --fail-on-error
python methodology_audit.py --work work/pilot --fail-on-error
python stage_gate.py --work work/pilot --stage pilot
python run_stage.py report --stage pilot --work work/pilot
```

## Test contract

`test_matrix.json` is the single inventory. Every root Python production module
has direct test mapping; the current waiver budget is **zero**.

```bash
python test_runner.py --manifest test_matrix.json --check-only
python test_runner.py --manifest test_matrix.json --suite fast \
  --report /tmp/dds-test-report-fast.json
```

After the pinned DDS bootstrap and pinned independent solver build:

```bash
python test_runner.py --manifest test_matrix.json --suite dds \
  --report /tmp/dds-test-report-dds.json
```

CI checks tracked, untracked and ignored repository residue. All GitHub Actions
are pinned to full commit SHAs and run with read-only repository permissions.
No committed workflow is allowed to invoke mass DDS evaluation.

## Evidence still to be earned

Software support does not fabricate bridge skill. Stage 2 must still earn:

- out-of-fold calibration from actual Stage-2 TRAIN predictions;
- verified blind counterexamples;
- confirmed technical bridge rules;
- successful tournament/real-play transfer;
- stable skill status.

The goal is not to memorize a DDS card. It is to recognize and explain the
bridge mechanism on a new position before DDS is revealed.
