# DDS Training — local, zero paid DDS API

This directory contains the bridge DDS-learning loop and its durable experience memory.

## Fixed architecture

- Primary mathematical engine: **DDS3 v3.0.0**, built locally from the official `dds-bridge/dds` source.
- Runtime baseline: **Linux/WSL2 + Python 3.14**.
- Storage: local files + SQLite. No paid DDS API and no per-request DDS charge.
- Raw PBN is kept free of DDS answers. DDS results are stored separately.
- Training cannot start accidentally: evaluation requires both `--start` and `DDS_TRAINING_CONFIRM=YES`.
- Sealed-test data has an additional fail-closed guard and must be evaluated alone.

Current analyzer revision: **dds-learning-v2.1**.

## Stages

1. `pilot`: 10,000 deals; 70% train / 15% validation / 15% sealed test.
2. `main`: expand to 30,000 total.
3. `targeted`: add approximately 10,000 positions selected from demonstrated weaknesses. Do not grow mechanically toward 100,000 random deals when targeted transfer is more useful.

A report is produced after a completed stage and must tell the user what decision is required before the next stage.

## Blind-first rule

DDS is never exposed before the assistant's prediction is locked:

`RAW PBN -> blind tasks -> locked prediction -> DDS -> error analysis -> durable experience -> transfer/regression -> report`

`evaluate` refuses to score a task without a locked prediction.

## Strict holdout isolation

The three corpus splits have different jobs:

- `train` — may change skills, rules, spaced-review queues and regression memory;
- `validation` — **evaluation only**; its answers may not change skills/rules or generate training follow-ups;
- `sealed_test` — **final evaluation only**; it never changes learning state and can only be opened explicitly in a sealed-only run.

Derived symmetry/perturbation tasks keep their full ancestry (`source_split`, `source_root_split`). They can update learning only when the root split is `train`. This prevents a validation or sealed-test error from leaking back into training through a derived task.

The database audit treats any validation/sealed skill evidence as an error.

## Durable experience memory

The database is split conceptually into **immutable evidence** and **evolving interpretation**.

Immutable / append-only evidence includes locked predictions, DDS results and error events. Their immutable task metadata (`deal_id`, `task_type`, `split`) is verified on every reuse. SQLite triggers also protect experience/correction/counterexample/history/audit/checkpoint records from silent UPDATE/DELETE.

If import, classification or interpretation later proves wrong, the system writes a `correction_event`; it does not erase the original fact.

Evolving learning state includes:

- `skill_profiles` — candidate / testing / confirmed / stable / weakened skills;
- `skill_evidence` — direct, transfer, regression, counterexample, symmetry, perturbation and real-world evidence;
- `skill_state_history` — lifecycle transitions;
- `rule_versions` — versioned candidate/confirmed rules rather than silent replacement;
- `regression_cases` — permanent tests created from meaningful failures;
- `counterexamples` — similar-looking positions where a rule must not be applied;
- `experience_events` — auditable learning timeline;
- `learning_queue` — targeted future work ranked by weakness;
- `correction_events` — append-only database corrections;
- `audit_events` — integrity/provenance audits.

Skill interpretation is analyzer-version aware. Historical evidence remains auditable, but current skill metrics use the current analyzer revision. A stable skill can be weakened by a fresh regression or counterexample failure and later recover only after a new clean success streak plus the full transfer criteria.

## Reinterpreting old experience after an analyzer upgrade

DDS mathematical facts are deliberately separated from the analyzer revision. A new analyzer version can therefore rebuild its own skill interpretation from already locked predictions/results **without rerunning DDS and without rewriting history**:

```bash
python run_stage.py reinterpret --work work/pilot --apply
```

`reinterpret` is explicit, skips validation/sealed tasks, does not call DDS, does not change the locked prediction/result, and adds only current-version experience evidence.

## DD value trajectory and first-error accounting

For a played line, value is defined on a constant scale as **projected final declarer tricks under optimal continuation**.

- a declarer error lowers that value;
- a defense error raises it;
- zero change may still require reasoning review;
- the first value-changing error is retained even if the opponent later gives the trick back.

Recovery is temporal: a later defensive gift can restore an earlier declarer loss, but an earlier defensive gift cannot retroactively repair a later declarer error. The system separately stores gross declarer loss, gross defensive gift, restored losses, squandered prior gifts and unrecovered loss/gift. Opposite-direction value changes are flagged as trajectory invariant violations, usually indicating a position/parsing/value-definition error.

## What receives extra training

The planner ranks weaknesses using error rate, DD-regret, high-confidence errors and lack of evidence. High-confidence errors get extra priority because they are more likely to expose a wrong internal heuristic than simple uncertainty.

Targeted follow-up can use:

- unseen transfer deals;
- counterexamples;
- old regression failures;
- rotational/suit symmetry checks;
- minimal legal rank-swap perturbations;
- spaced review;
- later, real tournament deals as a transfer test.

Follow-up generation uses **train errors only** and deduplicates mathematical task fingerprints so identical derived positions are not counted twice under different IDs.

The goal is to learn the bridge mechanism, not memorize the DDS card.

## Defense

Defense is a first-class target. Pilot tasks include opening leads; later targeted tasks can include continuations and shifts. Equal-optimal defensive moves are preserved as equal-optimal. A defensive claim above DDS is flagged for replay to identify the declarer error implicitly assumed by the proposed defense.

## Better-than-DDS claims

If a prediction claims more tricks than DDS, or claims the defense can take more than DDS allows, the case is marked `investigation_required`. It must be replayed against optimal opposition to find the first point where the proposed line relies on an opponent error.

## Crash-safe continuation

Evaluation writes lightweight checkpoints regularly and full SQLite snapshots at larger intervals. Progress after resume is cumulative over the requested task set rather than restarting counters from zero. Each run records stage, seed, corpus hash, solver information, analyzer version, selected splits, task file, prediction-file SHA-256 and whether sealed data was explicitly opened.

Reusing the same `run_id` with different provenance is rejected.

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

Sealed evaluation additionally requires `--open-sealed`, and `sealed_test` must be the only requested split in that run.

## Audit, planning and corrections

These commands do not start DDS evaluation:

```bash
python run_stage.py audit --work work/pilot --fail-on-error
python run_stage.py plan --work work/pilot
python run_stage.py correct --work work/pilot --target-table skill_evidence --target-key TASK_ID \
  --correction-type classification --reason "reason for correction"
```

## Reports and generalization

Reports separate train / derived / validation / sealed metrics and explicitly show train-to-validation gaps. Validation and sealed learning-leak counters must remain zero. This makes it possible to distinguish genuine generalization from improvement caused by learning the benchmark itself.

## Reproducibility

The corpus generator uses a fixed project seed and persistent DealID. Reports preserve corpus hash, algorithm version, solver information and run identifier. Old evidence is never silently rewritten, so improvements and regressions can be compared across versions.
