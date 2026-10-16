"""Read-only catalog adapter for generated shape exercises, with no API route.

The caller must authorize the school and own a fresh database transaction.
Only batch.student_exercises() may be delivered to the learner. Neither this
module nor the generator activates canon or persists student learning evidence.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any, Mapping
from uuid import UUID

from .tournament_shape_core import ASSESSMENT_VERSION, FULL_CALL, PROFILE, SCOPE, VERSION
from .tournament_shape_exercises import ShapeBatch, ShapeExercise, generate_shape_exercises


def _read_catalog(connection, school_id: UUID):
    # Lazy import keeps pure generation testable with the standard library.
    from .bidding_catalog_reader import read_school_catalog
    return read_school_catalog(connection, school_id, SCOPE)


def _bound_source(connection, school_id: UUID, binding: Mapping[str, Any]) -> Mapping[str, Any] | None:
    rows = connection.execute(
        """SELECT s.source_id,s.canonical_locator,kvs.source_locator
           FROM public.knowledge_version_source kvs JOIN public.source s USING(source_id)
           WHERE kvs.knowledge_version_id=%s AND s.school_id=%s AND s.status='active'""",
        (binding["knowledge_version_id"], school_id),
    ).fetchall()
    matches = [row for row in rows if row.get("canonical_locator") == binding["source_url"]
               and isinstance(row.get("source_locator"), dict)
               and row["source_locator"].get("original_excerpt") == binding["original_excerpt"]
               and row["source_locator"].get("teacher_excerpt") == binding["teacher_excerpt"]]
    return matches[0] if len(matches) == 1 else None


def _database_source_id(source: Mapping[str, Any]) -> str | None:
    """Preserve an actual UUID from the verified row; never derive one from URL."""
    value = source.get("source_id")
    if isinstance(value, UUID):
        return str(value)
    if type(value) is str:
        try:
            return str(UUID(value))
        except ValueError:
            pass
    return None


def _school_active(connection, school_id):
    row = connection.execute("SELECT status FROM public.school WHERE school_id=%s", (school_id,)).fetchone()
    return row is not None and row.get("status") == "active"


def available_targets_from_school_catalog(connection, *, school_id: UUID,
                                          assessment_mode: str = FULL_CALL) -> list[dict[str, Any]]:
    """Teacher-facing capabilities from executable ACTIVE rows, not site labels.

    The SQL reader already filters activation status, validity interval, School
    authority and activation gates. A row must also pass the pinned evaluator
    and source binding. No sample hand or correct answer is returned here.
    """
    if not isinstance(school_id, UUID):
        return []
    connection.execute("SET TRANSACTION READ ONLY")
    if not _school_active(connection, school_id):
        return []
    rows = _read_catalog(connection, school_id)
    context = {"school_id": school_id, "school_status": "active", "system_us": PROFILE,
               "input_status": "COMPLETE", "decision_type": "BIDDING", "dealer": "N", "seat": "S",
               "auction_json": ["1NT", "PASS"], "cards_played_json": [], "dummy_pbn": None}
    targets = []
    for call in ("3H", "3S"):
        probe = generate_shape_exercises(context=context, call=call, canon_version=VERSION,
                                         catalog=rows, seed=0, count=1, positive_count=1,
                                         assessment_mode=assessment_mode)
        if probe.status != "GENERATED":
            continue
        binding = probe.exercises[0]._binding
        if _bound_source(connection, school_id, binding) is None:
            continue
        targets.append({"task": "SHAPE_MEANING_ONLY", "proposed_call": call,
                        "system_profile": PROFILE, "scope_key": SCOPE,
                        "canon_version": VERSION, "rule_key": binding["rule_key"],
                        "assessment_mode": assessment_mode, "assessment_version": ASSESSMENT_VERSION,
                        "assessment_complete": False, "call_eligibility": "UNKNOWN"})
    return targets


def generate_from_school_catalog(connection, *, school_id: UUID,
                                 public_context: Mapping[str, Any], **generation) -> ShapeBatch:
    """Bind the caller's school to current authoritative rows and source evidence.

    public_context contains only system_us, dealer, seat, auction_json,
    decision_type, input_status, cards_played_json, dummy_pbn. No fixture-catalog,
    school-status override or full deal is accepted through this entry point.
    Database failures propagate; there is no cached or WORLD fallback.
    """
    expected = {"system_us", "dealer", "seat", "auction_json", "decision_type",
                "input_status", "cards_played_json", "dummy_pbn"}
    if not isinstance(school_id, UUID) or not isinstance(public_context, Mapping) or set(public_context) != expected:
        return ShapeBatch("OUT_OF_SCOPE", "INVALID_PUBLIC_CONTEXT")
    if set(generation) - {"call", "canon_version", "seed", "count", "positive_count", "difficulty",
                         "suit_length_constraints", "excluded_hands", "max_attempts", "target_error", "assessment_mode"}:
        return ShapeBatch("OUT_OF_SCOPE", "UNSUPPORTED_GENERATION_INPUT")
    connection.execute("SET TRANSACTION READ ONLY")
    if not _school_active(connection, school_id):
        return ShapeBatch("ABSTAIN", "SCHOOL_NOT_ACTIVE")
    catalog = _read_catalog(connection, school_id)
    context = deepcopy(dict(public_context)) | {"school_id": school_id, "school_status": "active"}
    batch = generate_shape_exercises(context=context, catalog=catalog, **generation)
    if batch.status != "GENERATED":
        return batch
    checked = {}
    exercises = []
    for exercise in batch.exercises:
        binding = exercise._binding
        key = (binding["knowledge_version_id"], binding["runtime_activation_id"], binding["payload_sha256"])
        if key not in checked:
            source = _bound_source(connection, school_id, binding)
            if source is None:
                return ShapeBatch("ABSTAIN", "SOURCE_BINDING_MISMATCH")
            checked[key] = _database_source_id(source)
        exercises.append(replace(exercise, _database_source_id=checked[key]))
    return replace(batch, exercises=tuple(exercises))


def assess_from_school_catalog(connection, *, school_id: UUID, exercise: ShapeExercise,
                               response: bool) -> dict[str, Any]:
    """Rebind after submission; changed/missing evidence cannot mark an error."""
    if not isinstance(school_id, UUID) or exercise._context.get("school_id") != school_id:
        return {"status": "ABSTAIN", "learner_error": None, "reason": "SCHOOL_MISMATCH"}
    connection.execute("SET TRANSACTION READ ONLY")
    if not _school_active(connection, school_id):
        return {"status": "ABSTAIN", "learner_error": None, "reason": "SCHOOL_NOT_ACTIVE"}
    result = exercise.assess_response(response, catalog=_read_catalog(connection, school_id))
    if result["status"] != "ABSTAIN":
        source = _bound_source(connection, school_id, result["source_binding"])
        if source is None:
            return {"status": "ABSTAIN", "learner_error": None, "reason": "SOURCE_BINDING_MISMATCH"}
        source_id = _database_source_id(source)
        if exercise._database_source_id is not None and source_id != exercise._database_source_id:
            return {"status": "ABSTAIN", "learner_error": None, "reason": "DATABASE_SOURCE_CHANGED"}
        result["database_source_id"] = source_id
    return result
