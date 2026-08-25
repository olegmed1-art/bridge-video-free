from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence


class TournamentInputManifestError(ValueError):
    pass


ALGORITHM_VERSION = "1.4"
ALGORITHM_DOCUMENT_ID = "1yGjeLGKqQfH7fHoKQ0CAM0z43KBxDoiy7plqCMD631E"
TEMPLATE_VERSION = "1.2"


def _rows(source: Mapping[str, Any]) -> list[dict[str, str]]:
    if source.get("schema") != "bridge-tournament-facts-v1":
        raise TournamentInputManifestError("unsupported tournament facts schema")
    columns = source.get("columns")
    raw_rows = source.get("rows")
    if not isinstance(columns, Sequence) or isinstance(columns, (str, bytes)):
        raise TournamentInputManifestError("facts columns are malformed")
    if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, (str, bytes)):
        raise TournamentInputManifestError("facts rows are malformed")
    names = [str(x) for x in columns]
    if len(set(names)) != len(names):
        raise TournamentInputManifestError("duplicate columns")
    parsed: list[dict[str, str]] = []
    for raw in raw_rows:
        if not isinstance(raw, str):
            raise TournamentInputManifestError("facts row must be pipe-delimited text")
        values = raw.split("|")
        if len(values) != len(names):
            raise TournamentInputManifestError("facts row width does not match columns")
        parsed.append(dict(zip(names, values, strict=True)))
    return parsed


def _stable_run_id(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "tournament-v3-" + hashlib.sha256(raw).hexdigest()[:24]


def build_input_manifest(
    source: Mapping[str, Any],
    *,
    normalized_facts_sha256: str,
    normalized_facts_size_bytes: int,
    normalized_facts_received_at: str,
    normalized_facts_commit: str,
    algorithm_revision_id: str,
) -> dict[str, Any]:
    """Build the v1.4 input/provenance manifest without inventing missing origins.

    The committed facts JSON is treated as the immediate machine-readable input.
    Its source metadata remains provenance, but official URLs are references only
    unless exact bytes/rows are independently preserved in the current evidence.
    """
    if len(str(normalized_facts_sha256)) != 64:
        raise TournamentInputManifestError("normalized facts SHA-256 is required")
    if int(normalized_facts_size_bytes) <= 0:
        raise TournamentInputManifestError("normalized facts size must be positive")
    if not str(normalized_facts_received_at).strip():
        raise TournamentInputManifestError("normalized facts receipt time is required")
    if not str(normalized_facts_commit).strip():
        raise TournamentInputManifestError("normalized facts commit is required")
    if not str(algorithm_revision_id).strip():
        raise TournamentInputManifestError("algorithm revision id is required")

    rows = _rows(source)
    if not rows:
        raise TournamentInputManifestError("tournament facts contain no rows")

    tournament = source.get("tournament")
    origin = source.get("source")
    policy = source.get("policy")
    if not isinstance(tournament, Mapping) or not isinstance(origin, Mapping) or not isinstance(policy, Mapping):
        raise TournamentInputManifestError("source/tournament/policy metadata is required")

    provider_key = str(tournament.get("provider_native_key") or "").strip()
    scoring = str(tournament.get("scoring") or "").strip().upper()
    if not provider_key:
        raise TournamentInputManifestError("provider_native_key is required")
    if not scoring:
        raise TournamentInputManifestError("scoring method is required")

    board_numbers: list[int] = []
    records: list[dict[str, Any]] = []
    immediate_locators_complete = True
    conflicts: list[dict[str, Any]] = []

    source_drive_id = str(origin.get("drive_id") or "").strip() or None
    source_sha = str(origin.get("sha256") or "").strip() or None
    source_size = origin.get("size_bytes")
    source_title = str(origin.get("title") or "").strip() or None

    for index, row in enumerate(rows):
        try:
            board = int(row.get("board", ""))
        except (TypeError, ValueError) as exc:
            raise TournamentInputManifestError(f"invalid board number at row {index}") from exc
        board_numbers.append(board)
        status = str(row.get("status", "")).strip().lower()
        slide_raw = str(row.get("slide", "")).strip()
        slide = int(slide_raw) if slide_raw.isdigit() else None
        if slide is None or not source_drive_id or not source_sha:
            immediate_locators_complete = False

        has_actual_auction = False  # the audited facts artifact contains no actual auction column/evidence
        has_opening_lead = bool(str(row.get("opening_lead", "")).strip())
        is_played = status == "played"

        # Full play is unavailable; an actual opening lead is only partial play evidence.
        play_status = "partial" if is_played and has_opening_lead else "absent"
        auction_status = "actual" if has_actual_auction else "absent"
        decision_status = "actual" if is_played and has_opening_lead else None
        decision_scope = "opening_lead_only" if decision_status == "actual" else "none"

        # Missing auction/full play is an explicit limitation, not a source conflict.
        source_status = "partial"
        if status not in {"played", "average", "unplayed"}:
            source_status = "conflict"
            conflicts.append({"board_number": board, "reason": f"unsupported status {status!r}"})

        field_origins: dict[str, Any] = {}
        for field in (
            "dealer",
            "vulnerability",
            "N",
            "E",
            "S",
            "W",
            "pair_direction",
            "status",
            "contract",
            "declarer",
            "result_delta",
            "opening_lead",
            "pair_score",
            "pair_percentage",
        ):
            if str(row.get(field, "")).strip():
                field_origins[field] = {
                    "immediate_source_role": "audited_derivative_slide",
                    "drive_id": source_drive_id,
                    "source_sha256": source_sha,
                    "slide": slide,
                    "facts_row_index": index,
                }

        records.append(
            {
                "board_number": board,
                "status": status,
                "source_status": source_status,
                "auction_status": auction_status,
                "play_status": play_status,
                "decision_status": decision_status,
                "decision_scope": decision_scope,
                "slide_mode": "tournament",
                "scoring_method": scoring,
                "immediate_origin": {
                    "facts_row_index": index,
                    "source_slide": slide,
                    "source_drive_id": source_drive_id,
                    "source_sha256": source_sha,
                },
                "field_origins": field_origins,
            }
        )

    expected = list(range(1, max(board_numbers) + 1))
    coverage_complete = sorted(board_numbers) == expected and len(set(board_numbers)) == len(board_numbers)
    if not coverage_complete:
        conflicts.append({"reason": "board coverage is not contiguous/unique"})

    upstream_limitations: list[str] = []
    if not source_drive_id or not source_sha or not source_size or not source_title:
        upstream_limitations.append("ORIGIN_FILE_METADATA_INCOMPLETE")
    # Current facts preserve official URLs as references, but not exact traveller/PBN row/tag locators per field.
    upstream_limitations.append("OFFICIAL_FIELD_LEVEL_LOCATORS_NOT_PRESERVED_IN_CURRENT_FACTS")
    if not origin.get("official_session_url"):
        upstream_limitations.append("OFFICIAL_SESSION_URL_MISSING")
    if not origin.get("official_personal_url"):
        upstream_limitations.append("OFFICIAL_PERSONAL_URL_MISSING")

    run_seed = {
        "normalized_facts_sha256": normalized_facts_sha256,
        "normalized_facts_commit": normalized_facts_commit,
        "algorithm_version": ALGORITHM_VERSION,
        "algorithm_revision_id": algorithm_revision_id,
        "template_version": TEMPLATE_VERSION,
        "provider_native_key": provider_key,
    }

    return {
        "schema": "tournament-input-manifest-v1",
        "run_id": _stable_run_id(run_seed),
        "normative_boundary": {
            "algorithm_version": ALGORITHM_VERSION,
            "algorithm_document_id": ALGORITHM_DOCUMENT_ID,
            "algorithm_revision_id": algorithm_revision_id,
            "template_version": TEMPLATE_VERSION,
        },
        "tournament": {
            "provider_native_key": provider_key,
            "scoring_method": scoring,
            "board_count": len(rows),
            "board_range": [min(board_numbers), max(board_numbers)],
        },
        "inputs": [
            {
                "role": "normalized_machine_readable_facts",
                "sha256": normalized_facts_sha256,
                "size_bytes": int(normalized_facts_size_bytes),
                "received_at": normalized_facts_received_at,
                "receipt_basis": "repository_ingestion_commit",
                "commit": normalized_facts_commit,
                "covered_boards": [min(board_numbers), max(board_numbers)],
            },
            {
                "role": "audited_derivative_origin",
                "drive_id": source_drive_id,
                "title": source_title,
                "sha256": source_sha,
                "size_bytes": source_size,
                "received_at": None,
                "covered_boards": [min(board_numbers), max(board_numbers)],
            },
            {
                "role": "official_session_reference",
                "url": origin.get("official_session_url"),
                "bytes_preserved_in_current_bundle": False,
            },
            {
                "role": "official_personal_reference",
                "url": origin.get("official_personal_url"),
                "bytes_preserved_in_current_bundle": False,
            },
        ],
        "policy_mode": policy.get("mode"),
        "coverage_complete": coverage_complete,
        "immediate_field_locators_complete": immediate_locators_complete,
        "upstream_official_field_provenance_complete": False,
        "provenance_limitations": upstream_limitations,
        "source_conflicts": conflicts,
        "source_conflict_gate_pass": not conflicts,
        "records": records,
    }
