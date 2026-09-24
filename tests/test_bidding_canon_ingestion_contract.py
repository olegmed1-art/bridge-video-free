from __future__ import annotations

import json
from pathlib import Path


ROOT = Path("docs/research/bidding-engine/canon-ingestion/natural-system-v1")


def _load_json(relative_path: str) -> dict:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def _candidate_calls(relative_path: str) -> list[str]:
    data = _load_json(relative_path)
    return [candidate["call"] for candidate in data["candidates"]]


def test_source_manifest_is_director_approved_non_runtime_source() -> None:
    manifest = _load_json("SOURCE_MANIFEST.json")

    assert manifest["authority_class"] == "school_canon_source"
    assert manifest["status"] == "director_approved_source"
    assert manifest["document"]["drive_file_id"] == "1HkVff4iH2e3HT5kwblvd3mY8TUQPR6jf"
    assert (
        manifest["document"]["sha256"]
        == "dc9678da5ab19a897c3f1fbd785cc6f7b0ddd9d70d90d895743db615a2fdd3d6"
    )
    assert manifest["authority_boundaries"]["external_bridge_knowledge_may_fill_gaps"] is False
    assert manifest["authority_boundaries"]["activation_requires_structured_provenance_and_tests"] is True
    assert manifest["ingestion"]["production_database_effect"] == "none"


def test_block_inventory_is_not_activated() -> None:
    inventory = _load_json("BLOCK_INVENTORY.json")

    assert inventory["block_count"] == len(inventory["blocks"]) == 34
    assert inventory["inventory_status"] == "complete_at_heading_level"
    assert {block["activation_status"] for block in inventory["blocks"]} == {"not_created"}
    transcribed = {"NSV1-P1-R1-C1", "NSV1-P1-R1-C2"}
    for block in inventory["blocks"]:
        expected = "transcribed_verified" if block["block_id"] in transcribed else "pending_verified"
        assert block["transcription_status"] == expected


def test_block_ledger_matches_snapshot_and_candidate_provenance() -> None:
    ledger = _load_json("BLOCK_INVENTORY.json")
    snapshot = json.loads(Path(
        "docs/research/bidding-engine/content-intake/content-intake-snapshot.json"
    ).read_text(encoding="utf-8"))
    lane = next(item for item in snapshot["lanes"] if item["lane_id"] == "SCHOOL_CANON_BIDDING")
    inventory = lane["inventory"]
    blocks = {block["block_id"]: block for block in ledger["blocks"]}
    assert len(blocks) == len(ledger["blocks"]) == inventory["visual_blocks_total"] == 34
    candidate_sets = [json.loads(path.read_text(encoding="utf-8"))
                      for path in sorted((ROOT / "candidates").glob("*.candidates.json"))]
    by_block = {item["source_block_id"]: item for item in candidate_sets}
    transcribed = {key for key, block in blocks.items()
                   if block["transcription_status"] == "transcribed_verified"}
    assert len(by_block) == len(candidate_sets) == inventory["blocks_with_git_transcription"] == 2
    assert set(by_block) == transcribed == {"NSV1-P1-R1-C1", "NSV1-P1-R1-C2"}
    assert sum(block["transcription_status"] == "pending_verified" for block in blocks.values()) == inventory["blocks_pending_semantic_transcription"] == 32
    assert sum(len(item["candidates"]) for item in candidate_sets) == inventory["candidate_rules_total"] == 33
    assert {entry["block_id"] for entry in inventory["candidate_rules_by_block"]} == transcribed
    for entry in inventory["candidate_rules_by_block"]:
        candidate_set = by_block[entry["block_id"]]
        assert len(candidate_set["candidates"]) == entry["count"]
        assert candidate_set["source_key"] == ledger["source_key"] == lane["source"]["source_key"]
        assert candidate_set["set_status"] == entry["status"] == "transcribed_not_activated"
        assert candidate_set["authority_class"] == "school_canon_candidate"
        assert candidate_set["external_knowledge_used"] is False
        assert all(c["activation_status"] == "not_eligible" and not c.get("runtime_rule_id")
                   for c in candidate_set["candidates"])
    assert inventory["production_active_rules"] == inventory["production_executable_candidates"] == 0
    assert all(block["activation_status"] == "not_created" for block in blocks.values())


def test_opening_candidates_match_verified_source_decomposition() -> None:
    assert _candidate_calls("candidates/NSV1-P1-R1-C1_OPENINGS.candidates.json") == [
        "1C",
        "1D",
        "1H",
        "1S",
        "1NT",
        "2C",
        "2D",
        "2H",
        "2S",
        "2NT",
        "3C",
        "3D",
        "3H",
        "3S",
        "3NT",
        "4C",
        "4D",
        "4H",
        "4S",
    ]


def test_1c_first_response_candidates_match_verified_source_decomposition() -> None:
    assert _candidate_calls("candidates/NSV1-P1-R1-C2_1C_FIRST_RESPONSES.candidates.json") == [
        "1D",
        "1H",
        "1S",
        "1NT",
        "2C",
        "2D",
        "2H",
        "2S",
        "2NT",
        "3C",
        "3D",
        "3H",
        "3S",
        "3NT",
    ]


def test_candidate_sets_remain_non_executable_and_source_bound() -> None:
    for relative_path in [
        "candidates/NSV1-P1-R1-C1_OPENINGS.candidates.json",
        "candidates/NSV1-P1-R1-C2_1C_FIRST_RESPONSES.candidates.json",
    ]:
        data = _load_json(relative_path)

        assert data["authority_class"] == "school_canon_candidate"
        assert data["external_knowledge_used"] is False
        assert data["set_status"] == "transcribed_not_activated"

        for candidate in data["candidates"]:
            assert candidate["activation_status"] == "not_eligible"
            assert candidate["source_text"]
            assert not candidate.get("runtime_rule_id")


if __name__ == "__main__":
    for test in [
        test_source_manifest_is_director_approved_non_runtime_source,
        test_block_inventory_is_not_activated,
        test_opening_candidates_match_verified_source_decomposition,
        test_1c_first_response_candidates_match_verified_source_decomposition,
        test_candidate_sets_remain_non_executable_and_source_bound,
    ]:
        test()
