# DDS Stage 2C — Play, Defense and Counterexample Training

## Purpose

This stage converts Stage 2B preparation into a controlled TRAIN-only learning cycle.
It does not open validation or sealed test.
It does not promote skills automatically.

## Inputs

- 2000 continuation tasks:
  - 1000 declarer decisions
  - 1000 defense decisions
- 441 blind counterexample candidates
- 10000 multicontract blueprint predictions

## Required sequence

1. Lock model predictions before DDS exposure.
2. Preserve task family lineage.
3. Run DDS only on TRAIN-owned tasks.
4. Compare prediction against DD truth.
5. Classify errors.
6. Create regression and teaching examples.

## Error classes

- declarer planning
- communication and entries
- safety plays
- trump management
- squeeze/endgame structure
- defensive continuation
- switching
- tempo
- communication destruction
- false rules

## Skill promotion rules

A finding may become a candidate rule only after:

- repeated evidence;
- independent families;
- counterexample testing;
- regression verification.

No automatic stable skill promotion is allowed.

## Gates

Closed:
- validation
- sealed test
- automatic model promotion

Required before validation:
- card-level investigations;
- blind counterexample results;
- regression audit;
- comparison against v2.5 benchmark.
