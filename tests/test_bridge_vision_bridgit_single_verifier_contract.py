import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "docs/evidence/bridgit_rank_layout_holdout_manifest_v1.schema.json"
PROTOCOL = ROOT / "docs/evidence/bridgit_rank_layout_frozen_unseen_holdout_v1.md"
PREFLIGHT = ROOT / "docs/evidence/bridgit_rank_layout_holdout_preflight_v1.md"


def test_holdout_manifest_requires_one_human_verifier() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    case_schema = schema["$defs"]["case"]
    required = set(case_schema["required"])
    properties = case_schema["properties"]

    assert {"gold_label_origin", "gold_verifier_channel_id"} <= required
    assert "gold_author_channel_id" not in required
    assert "gold_reviewer_channel_id" not in required
    assert properties["gold_label_origin"]["enum"] == [
        "HUMAN_DIRECT",
        "SOURCE_TRUTH_WITH_HUMAN_VERIFICATION",
    ]


def test_holdout_docs_do_not_require_two_human_channels() -> None:
    protocol = PROTOCOL.read_text(encoding="utf-8")
    preflight = PREFLIGHT.read_text(encoding="utf-8")

    assert "checked by one human verifier" in protocol
    assert "a second human channel is not required" in preflight
    assert "must be different identifiers" not in preflight
