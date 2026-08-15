# DDS Training — local, zero paid DDS API

This directory contains the bridge DDS-learning loop and its durable experience memory.

## Fixed architecture

- Primary mathematical engine: **DDS3 v3.0.0**, built locally from the official `dds-bridge/dds` source.
- Runtime baseline: **Linux/WSL2 + Python 3.14**.
- Storage: local files + SQLite. No paid DDS API and no per-request DDS charge.
- Raw PBN is kept free of DDS answers. DDS results are stored separately.
- Training cannot start accidentally: evaluation requires both `--start` and `DDS_TRAINING_CONFIRM=YES`.
- Sealed-test data has an additional guard.

## Stages

1. `pilot`: 10,000 deals; 70% train / 15% validation / 15% sealed test.
2. `main`: expand to 30,000 total.
3. `targeted`: add approximately 10,000 positions selected from demonstrated weaknesses. Do not grow mechanically toward 100,000 random deals when targeted transfer is more useful.

A report is produced after a completed stage and must tell the user what decision is required before the next stage.

## Blind-first rule

DDS is never exposed before the assistant's prediction is locked:

`RAW PBN -> blind tasks -> locked prediction -> DDS -> error analysis -> durable experience -> transfer/regression -> report`

`evaluate` refuses to score a task without a locked prediction.

## Durable experience memory

The database is split conceptually into **immutable evidence** and **evolving interpretation**.

Immutable / append-only evidence includes locked predictions, DDS results, error events and skill evidence. SQLite triggers prevent silent UPDATE/DELETE of these rows. If import, classification or interpretation later proves wrong, the system writes a `correction_event`; it does not erase the original fact.

Evolving learning state includes:

- `skill_profiles` — candidate / testing / confirmed / stable / weakened skills;
- `skill_evidence` — direct, transfer, regression, counterexample, symmetry, perturbation and real-world evidence;
- `skill_state_history` — every lifecycle transition;
- `rule_versions` — versioned candidate/confirmed rules rather than silent replacement;
- `regression_cases` — permanent tests created from meaningful failures;
- `counterexamples` — similar-looking positions where a rule must not be applied;
- `experience_events` — auditable learning timeline;
- `learning_queue` — targeted future work ranked by weakness;
- `correction_events` — append-only database corrections;
- `audit_events` — integrity/provenance audits.

A rule is not promoted merely because DDS contradicted one deal. Confirmation requires successful transfer to unseen positions. Stability additionally requires regression passes and counterexamples. A stable skill that later fails regression is weakened and returns to active training.

## What receives extra training

The planner ranks weaknesses using error rate, DD-regret, high-confidence errors and lack of evidence. High-confidence errors get extra priority because they are more likely to expose a wrong internal heuristic than simple uncertainty.

Targeted follow-up can use:

- unseen transfer deals;
- counterexamples;
- old regression failures;
- rotational/suit symmetry checks;
- one-card perturbations;
- later, real tournament deals as a transfer test.

The goal is to learn the bridge mechanism, not memorize the DDS card.

## Defense

Defense is a first-class target. Pilot tasks include opening leads; later targeted tasks can include continuations and shifts. Equal-optimal defensive moves are preserved as equal-optimal. A defensive claim above DDS is flagged for replay to identify the declarer error implicitly assumed by the proposed defense.

## Better-than-DDS claims

If a prediction claims more tricks than DDS, or claims the defense can take more than DDS allows, the case is marked `investigation_required`. It must be replayed against optimal opposition to find the first point where the proposed line relies on an opponent error.

## Local bootstrap

```bash
cd dds_training
bash bootstrap_linux.sh
source .venv/bin/activate
python preflight.py --quick
```

The bootstrap builds DDS3 locally and runs a non-training technical preflight.

## Preparation only

```bash
python run_stage.py prepare --stage pilot --out work/pilot
```

This creates the deterministic raw corpus, split manifest and blind task queue. It does **not** start DDS learning.

## Launch guard

Actual DDS evaluation requires:

```bash
export DDS_TRAINING_CONFIRM=YES
python run_stage.py evaluate --stage pilot --work work/pilot --predictions locked_predictions.jsonl --start
```

No `--start`, no confirmation token, or missing locked predictions => no DDS training.

## Audit, planning and corrections

These commands do not start DDS evaluation:

```bash
python run_stage.py audit --work work/pilot --fail-on-error
python run_stage.py plan --work work/pilot
python run_stage.py correct --work work/pilot --target-table skill_evidence --target-key TASK_ID \
  --correction-type classification --reason "reason for correction"
```

## Reproducibility

The corpus generator uses a fixed project seed and persistent DealID. Reports preserve corpus hash, algorithm version, solver information and run identifier. Old evidence is never silently rewritten, so improvements and regressions can be compared across versions.
