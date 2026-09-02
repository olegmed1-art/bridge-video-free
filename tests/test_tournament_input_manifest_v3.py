from __future__ import annotations

import hashlib
import json
from pathlib import Path

from bridge_school_api.tournament_input_manifest_v3 import build_input_manifest


FACTS = Path("data/tournaments/tournament_30041_round2_diana_facts_v1.json")
RECEIVED_AT = "2026-08-21T11:05:01Z"
RECEIPT_COMMIT = "0158e506c022fd20051898a3161ab8b576d51f9b"
ALGORITHM_REVISION = "AIroW35dhYwaAOQ1dDxMCkYjVKitrJKTII3Zx0IS7RNHuQDBE8iXBw-l2Ux9qF42DTU1gUWmWDu3kn9XVb1ne2cTls8HTLvblELsTAZRRuY"


def _load():
    raw = FACTS.read_bytes()
    return raw, json.loads(raw.decode("utf-8"))


def _build(source=None):
    raw, actual = _load()
    return build_input_manifest(
        actual if source is None else source,
        normalized_facts_sha256=hashlib.sha256(raw).hexdigest(),
        normalized_facts_size_bytes=len(raw),
        normalized_facts_received_at=RECEIVED_AT,
        normalized_facts_commit=RECEIPT_COMMIT,
        algorithm_revision_id=ALGORITHM_REVISION,
    )


def _mutate_row(source, board_number: int, **updates):
    columns = list(source["columns"])
    for index, raw in enumerate(source["rows"]):
        row = dict(zip(columns, raw.split("|"), strict=True))
        if int(row["board"]) == board_number:
            row.update({key: str(value) for key, value in updates.items()})
            source["rows"][index] = "|".join(row[column] for column in columns)
            return
    raise AssertionError(board_number)


def test_real_30041_input_manifest_is_reproducible_and_explicitly_limited():
    manifest = _build()
    assert manifest["schema"] == "tournament-input-manifest-v1"
    assert manifest["normative_boundary"]["algorithm_version"] == "1.4"
    assert manifest["normative_boundary"]["template_version"] == "1.2"
    assert manifest["normative_boundary"]["algorithm_revision_id"] == ALGORITHM_REVISION
    assert manifest["tournament"]["provider_native_key"] == "bridge.co.il:event:30041:round:2"
    assert manifest["tournament"]["scoring_method"] == "MP"
    assert manifest["tournament"]["board_count"] == 24
    assert manifest["coverage_complete"] is True
    assert manifest["immediate_field_locators_complete"] is True
    assert manifest["source_conflict_gate_pass"] is True
    assert manifest["upstream_official_field_provenance_complete"] is False
    assert "OFFICIAL_FIELD_LEVEL_LOCATORS_NOT_PRESERVED_IN_CURRENT_FACTS" in manifest["provenance_limitations"]
    normalized = manifest["inputs"][0]
    assert normalized["received_at"] == RECEIVED_AT
    assert normalized["commit"] == RECEIPT_COMMIT
    assert len(normalized["sha256"]) == 64
    assert normalized["size_bytes"] > 0


def test_service_statuses_do_not_invent_auction_or_full_play():
    manifest = _build()
    played = [record for record in manifest["records"] if record["status"] == "played"]
    admin = [record for record in manifest["records"] if record["status"] != "played"]
    assert len(played) == 21
    assert len(admin) == 3
    assert all(record["source_status"] == "partial" for record in manifest["records"])
    assert all(record["auction_status"] == "absent" for record in manifest["records"])
    assert all(record["play_status"] == "partial" for record in played)
    assert all(record["decision_status"] == "actual" for record in played)
    assert all(record["decision_scope"] == "opening_lead_only" for record in played)
    assert all(record["play_status"] == "absent" for record in admin)
    assert all(record["decision_status"] is None for record in admin)


def test_each_present_fact_is_bound_to_immediate_slide_and_source_sha():
    manifest = _build()
    for record in manifest["records"]:
        assert record["immediate_origin"]["source_slide"] is not None
        assert record["immediate_origin"]["source_drive_id"]
        assert len(record["immediate_origin"]["source_sha256"]) == 64
        assert record["field_origins"]
        for origin in record["field_origins"].values():
            assert origin["slide"] == record["immediate_origin"]["source_slide"]
            assert origin["source_sha256"] == record["immediate_origin"]["source_sha256"]


def test_run_id_is_stable_for_same_evidence_and_changes_with_evidence_identity():
    first = _build()
    second = _build()
    assert first["run_id"] == second["run_id"]

    raw, source = _load()
    changed = build_input_manifest(
        source,
        normalized_facts_sha256="0" * 64,
        normalized_facts_size_bytes=len(raw),
        normalized_facts_received_at=RECEIVED_AT,
        normalized_facts_commit=RECEIPT_COMMIT,
        algorithm_revision_id=ALGORITHM_REVISION,
    )
    assert changed["run_id"] != first["run_id"]


def test_unknown_board_status_is_conflict_not_silently_normalized():
    _, source = _load()
    _mutate_row(source, 2, status="mystery")
    manifest = _build(source)
    assert manifest["source_conflict_gate_pass"] is False
    assert manifest["source_conflicts"]
    board = next(record for record in manifest["records"] if record["board_number"] == 2)
    assert board["source_status"] == "conflict"


def test_missing_immediate_slide_locator_is_reported_not_guessed():
    _, source = _load()
    _mutate_row(source, 2, slide="")
    manifest = _build(source)
    assert manifest["immediate_field_locators_complete"] is False
    board = next(record for record in manifest["records"] if record["board_number"] == 2)
    assert board["immediate_origin"]["source_slide"] is None
