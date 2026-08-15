from __future__ import annotations

PROJECT_SEED = 20260815
DDS3_TAG = "v3.0.0"
ENDPLAY_VERSION = "0.5.12"
ALGORITHM_VERSION = "dds-learning-v2.0"

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

# Skills are deliberately hard to promote: raw repetition is not enough.
# Confirmation requires successful transfer to unseen deals; stability also
# requires regression passes and counterexamples that show discrimination.
SKILL_LIFECYCLE = {
    "testing_evidence": 3,
    "confirmed_transfer": 3,
    "confirmed_rate": 0.80,
    "stable_transfer": 10,
    "stable_rate": 0.90,
    "stable_regression_passes": 3,
    "stable_counterexamples": 2,
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

BATCH_SIZE_DD_TABLE = 100
