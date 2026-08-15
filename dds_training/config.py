from __future__ import annotations

PROJECT_SEED = 20260815
DDS3_TAG = "v3.0.0"
ENDPLAY_VERSION = "0.5.12"
ALGORITHM_VERSION = "dds-learning-v2.1"

STAGES = {
    "pilot": 10_000,
    "main": 30_000,
    "targeted": 10_000,
}

SPLIT_RATIOS = {
    "train": 0.70,
    "validation": 0.15,
    "sealed_test": 0.15,
}

# Only these splits may change the skill/rule memory. Validation and sealed test
# are evaluation-only so the system cannot learn from its benchmark answers.
LEARNING_SPLITS = frozenset({"train", "derived"})
EVALUATION_ONLY_SPLITS = frozenset({"validation", "sealed_test"})

# Skills are deliberately hard to promote: raw repetition is not enough.
# Confirmation requires successful transfer to unseen deals; stability also
# requires a clean recent regression streak and successful counterexamples.
# A previously stable skill can recover from a later regression only after a new
# clean streak, rather than being weakened forever by one historical failure.
SKILL_LIFECYCLE = {
    "testing_evidence": 3,
    "confirmed_transfer": 3,
    "confirmed_rate": 0.80,
    "stable_transfer": 10,
    "stable_rate": 0.90,
    "stable_regression_passes": 3,
    "stable_counterexamples": 2,
    "recovery_regression_passes": 3,
}

SEATS = "NESW"
STRAINS = ("S", "H", "D", "C", "NT")

# Standard duplicate-board vulnerability cycle for boards 1..16.
VUL_CYCLE = (
    "None", "NS", "EW", "All",
    "NS", "EW", "All", "None",
    "EW", "All", "None", "NS",
    "All", "None", "NS", "EW",
)

# DDS3 v3.0.0 CalcAllTablesPBN can return at most 40 complete five-strain
# tables in one call (MAXNOOFTABLES=40). Larger corpora are chunked by the
# runner. Keeping this at the actual solver boundary prevents a failure on the
# first 100-deal batch of a real pilot run.
BATCH_SIZE_DD_TABLE = 40
