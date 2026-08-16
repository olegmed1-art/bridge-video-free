# DDS Training — local, zero paid DDS API

This directory contains the sports-bridge DDS learning loop, its reproducible
corpora and its durable experience memory.

Current methodology revision: **`dds-learning-v2.2`**.

See [METHODOLOGY_V2_2.md](METHODOLOGY_V2_2.md) for the 10,000-deal pilot
postmortem, corrected evidence semantics and Stage 2 requirements.

## Fixed architecture

- Mathematical engine: **DDS3 v3.0.0**, built locally from the official source.
- Runtime baseline: Linux/WSL2 + Python 3.14.
- Storage: local files + SQLite.
- Raw PBN never contains answers exposed before prediction.
- No paid DDS API and no per-request DDS charge.
- Evaluation is fail-closed: both `--start` and
  `DDS_TRAINING_CONFIRM=YES` are required.
- Sealed test is opened explicitly, alone and only once per benchmark scope.

## Stages

1. `pilot`: 10,000 deals — completed.
2. `main`: expand the same reproducible corpus to 30,000 total deals.
3. `targeted`: approximately 10,000 focused positions selected from demonstrated
   weaknesses after the main report.

A stage report is produced immediately after completion. Advancing requires:

- full fresh-task coverage;
- no unresolved better-than-DDS investigations;
- database/provenance audit `ok`;
- methodology audit `ok`;
- report present;
- explicit user approval.

The code never starts a new mass stage merely because the previous one finished.

## Blind-first rule

```text
RAW PBN
  -> blind tasks
  -> locked prediction
  -> DDS
  -> error/regret analysis
  -> durable evidence
  -> regression/reinforcement/transfer
  -> report
```

DDS evaluation refuses to score any requested task without a locked prediction.

## Holdout isolation

The corpus is split by deal family, not by an individual task:

- `train` may update learning state;
- `validation` measures model choice only;
- `sealed_test` is final evaluation only;
- `derived` may learn only when its root source is `train`.

Validation and sealed results never create skill evidence, rules, follow-ups,
spaced-review work or regression cases. The database audit treats any such leak
as an error.

## Evidence roles in v2.2

Evidence types have different meanings and may not be combined silently:

- `direct` — ordinary TRAIN result;
- `error_pattern` — specific repeated error family;
- `regression` — a fresh blind attempt on the exact known problem position;
- `reinforcement` — same-source symmetry, suit renaming or nearby perturbation;
- `transfer` — genuinely unseen or cross-fitted source;
- `real_world` — independent tournament/real-play transfer;
- `counterexample` — similar-looking position where the action/rule must change.

A same-source symmetry or perturbation is useful for testing invariance and
brittleness, but it is **not independent transfer evidence**. Only `transfer`
and `real_world` may promote a skill to `confirmed` or `stable`.

## Balanced follow-up generation

Pilot v2.1 selected follow-up sources by one global severity ranking and could
collapse to one task family. v2.2 selects sources deterministically, round-robin
across:

1. task type;
2. error code;
3. strain.

For each selected source it can create:

- exact regression retest;
- seat rotations;
- suit permutation;
- legal same-suit rank-swap perturbations.

Every derived row stores source type, error code, strain, selection group,
variant kind, root split and transfer eligibility. The output is marked as a
**targeted/adversarial sample**; its raw percentage must not be compared with a
random validation sample without a matched baseline.

Seat rotation keeps Dealer and vulnerability metadata consistent with the
rotated position.

## Model selection

Contract-trick estimation and opening-lead selection are different competencies.
They are selected separately using paired per-task loss:

- contract loss: absolute DDS trick error;
- opening-lead loss: DD-regret.

A candidate replaces the baseline for a family only when:

- mean improvement reaches the configured practical minimum; and
- the paired bootstrap 95% upper bound remains below zero.

The selected analyzer may be a family ensemble: one model for trick estimation
and another for opening leads.

## Durable experience and versioning

Immutable/append-only evidence includes:

- locked predictions;
- DDS results;
- error events;
- run/task provenance;
- corrections;
- investigations;
- counterexamples;
- state-transition history;
- audits and checkpoints.

A discovered mistake creates a correction event. Original facts are never
silently rewritten or deleted.

Learning state includes:

- `skill_evidence`;
- `skill_profile_versions` — one preserved profile per analyzer revision;
- current compatibility profile `skill_profiles`;
- `rule_versions`;
- `regression_cases` plus multi-skill links;
- `counterexamples`;
- `experience_events`;
- bounded `learning_queue`.

Before a new analyzer revision reuses a current skill row, the previous profile
is snapshotted under its own `algorithm_version`. Thus v2.2 metrics cannot erase
v2.1 skill state.

## Skill lifecycle

```text
candidate -> testing -> confirmed -> stable
                         \-> weakened -> stable after recovery
```

Raw repetition is insufficient. Promotion requires independent transfer.
Stability additionally requires:

- high transfer rate;
- clean recent regression streak;
- successful counterexamples.

A fresh regression or counterexample failure can weaken a confirmed/stable
skill. Recovery requires a new clean streak.

## Bounded spaced review

Exact source errors remain in immutable error and regression tables. Operational
spaced review is aggregated by:

- skill;
- review offset;
- due-evaluation bucket.

Requested work per bucket is capped. Source provenance is retained in append-only
experience events. This prevents queue explosion at 30k/50k scale.

## Better-than-DDS claims

A prediction above the DDS optimum, or a defensive claim above the DDS maximum,
creates a mandatory investigation.

Resolution requires:

- cause;
- first refutation;
- bridge lesson;
- supporting evidence.

When the blind prediction did not include a legal card line, the system may
honestly classify the initial estimate as unsupported, but must not invent a
card-level refutation. Stage 2 therefore requires line-bearing tasks.

## DD value trajectory

For full Play, value is projected final declarer tricks under optimal
continuation. After each card:

- declarer error lowers the value;
- defense error raises it;
- the first swing is retained;
- later compensation is recorded separately;
- impossible-direction movements are integration/value-definition warnings.

The database stores gross losses/gifts, restored losses, squandered earlier
gifts and unrecovered damage.

## Methodology audit

Database integrity and methodological validity are separate.

`methodology_audit.py` checks, among other things:

- missing task/error families in follow-ups;
- same-source probes counted as transfer;
- adversarial-sample interpretation;
- lack of counterexamples/rule versions;
- confidence calibration problems;
- structural-only investigations;
- absence of full-play trajectories;
- legacy single-score model selection;
- spaced-review expansion.

`stage_gate.py` blocks expansion on methodological errors even when every DDS
calculation completed successfully.

## Reinterpreting immutable facts after an analyzer upgrade

A new analyzer version may build a new interpretation from stored predictions
and DDS results without rerunning DDS:

```bash
python run_stage.py reinterpret --work work/pilot --apply
```

Reinterpretation:

- is explicit;
- skips validation/sealed learning;
- never changes locked facts;
- records current-version evidence separately;
- preserves earlier versioned skill profiles.

## Technical commands

Prepare a pilot corpus without DDS exposure:

```bash
python run_stage.py prepare --stage pilot --out work/pilot
```

Evaluate locked TRAIN predictions:

```bash
DDS_TRAINING_CONFIRM=YES python run_stage.py evaluate \
  --stage pilot \
  --work work/pilot \
  --predictions work/pilot/locked_predictions_train.jsonl \
  --splits train \
  --start
```

Audit database/provenance:

```bash
python run_stage.py audit --work work/pilot --fail-on-error
```

Audit learning methodology:

```bash
python methodology_audit.py --work work/pilot --fail-on-error
```

Check stage completion/readiness:

```bash
python stage_gate.py --work work/pilot --stage pilot
```

## Stage 2 requirements

Before claiming acquired declarer/defense skill, the main stage must add:

- legal blind card lines and candidate trees;
- defense continuation/switch tasks after the opening lead;
- full Play DD trajectories;
- cross-fitted and fresh-corpus transfer;
- verified counterexamples;
- versioned bridge-rule synthesis;
- out-of-fold confidence calibration;
- separate human-information mode;
- real tournament transfer tests;
- restartable sharded execution with durable artifact per shard.

The goal is not to memorize a DDS card. It is to recognize the bridge mechanism
on a new position before DDS is revealed.
