# DDS Training — local, zero paid DDS API

This directory contains the preparation layer for the bridge DDS-learning loop.

## Fixed architecture

- Primary mathematical engine: **DDS3 v3.0.0**, built locally from the official `dds-bridge/dds` source.
- Python helper layer: **endplay 0.5.12** for bridge types/PBN utilities when useful.
- DDS3 remains the source of truth; endplay's bundled older DDS is not used as the primary training labeler.
- Storage: local files + SQLite. No paid DDS API and no per-request DDS charge.
- Raw PBN is kept free of DDS answers. DDS results are written to separate result files/database rows.
- Training cannot start accidentally: evaluation requires an explicit start flag and a confirmation token.

## Stages

1. `pilot`: 10,000 deals; 70% train / 15% validation / 15% sealed test.
2. `main`: corpus expanded to 30,000 total; same 70/15/15 split.
3. `targeted`: additional ~10,000 deals focused on weaknesses discovered in reports; expand beyond 50,000 only when metrics justify it.

A report is generated immediately after each completed stage. The report must contain declarer/play metrics, defense metrics, DD-regret, claims that exceed DDS, recurrent error classes, skills/rules changed, regressions, and the recommended user decision before the next stage.

## Blind-first rule

DDS is not exposed before the assistant's prediction is locked. The pipeline therefore separates:

`RAW PBN -> blind task queue -> locked predictions -> DDS evaluation -> error/skill analysis -> report`

`evaluate` refuses to score a task that has no locked prediction.

## Defense

Defense is a first-class training target. Pilot task generation creates both contract-trick tasks and opening-lead tasks. Later reinforcement batches may create critical continuation/shift positions. For defense, a move is measured by how many tricks it allows declarer or secures for defenders, and equal-optimal moves are treated as equal-optimal rather than as errors.

## Better-than-DDS claims

If a prediction claims more tricks than DDS allows, or claims the defense can take more tricks than DDS allows, the result is automatically flagged `investigation_required`. Such cases must be replayed against optimal opposition to find the first point where the proposed line implicitly relies on an opponent error.

## Local bootstrap

Linux / WSL2:

```bash
cd dds_training
bash bootstrap_linux.sh
source .venv/bin/activate
python preflight.py --quick
```

Windows users should use WSL2 or a Linux container for DDS3's Python binding. DDS3's Python documentation currently supports Linux/macOS source builds; the school pipeline is therefore pinned to Linux for reproducibility.

## Preparation only

Prepare a stage without running DDS training:

```bash
python run_stage.py prepare --stage pilot --out work/pilot
```

This creates the deterministic raw corpus, split manifest and blind task queue only.

## Launch guard

Actual DDS evaluation requires both:

```bash
export DDS_TRAINING_CONFIRM=YES
python run_stage.py evaluate --stage pilot --work work/pilot --predictions locked_predictions.jsonl --start
```

The workflow is intentionally fail-closed: no `--start`, no confirmation variable, or missing locked predictions => no DDS training.

## Reproducibility

The corpus generator uses a fixed project seed and persistent DealID. Every report stores seed, solver version, corpus hash and run identifier so any result can be reproduced after an algorithm change.
