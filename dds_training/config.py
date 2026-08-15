from __future__ import annotations

PROJECT_SEED = 20260815
DDS3_TAG = "v3.0.0"
ENDPLAY_VERSION = "0.5.12"

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
