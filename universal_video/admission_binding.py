"""Offline candidate for immutable admission receipts and source observations.

No database/API/transaction/worker integration. Inputs must eventually come from
server-owned approval, observation and queue rows; matching hashes or UUIDs do
not authenticate them. This module neither reserves a source nor authorizes work.
In particular, successful recovery MUST NOT require today's Drive revision or a
particular mutable queue status. A separate fresh-source/lease gate precedes work.
"""
from __future__ import annotations

from functools import wraps
import hashlib

from . import batch_plan, subset_contract, video_queue


DISABLED = dict(subset_contract.DISABLED)
BATCH_FIELDS = tuple(video_queue.INTAKE_FIELDS) + (
    "inventory_sha256", "expected_count", "total_size_bytes", "result_mode",
    "canonical_promotion_allowed", "database_persistence_allowed",
)


class AdmissionBindingError(ValueError):
    """Fixed diagnostic codes without caller data or dependency exception text."""


def _safe(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except AdmissionBindingError:
            raise
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
            raise AdmissionBindingError("INVALID_ADMISSION_INPUT") from exc
    return checked


def _require(condition, code):
    if not condition:
        raise AdmissionBindingError(code)


def _same(left, right):
    # JSON comparison distinguishes bool/int and rejects non-finite values.
    return batch_plan._digest(left) == batch_plan._digest(right)


def _contract(value, expected):
    manifest = subset_contract._contract(value, expected)
    _require(type(value["subset_sequence"]) is int and value["subset_sequence"] >= 1,
             "INVALID_SUBSET_SEQUENCE")
    subset_contract._sha(value["plan_sha256"])
    raw = [subset_contract._raw(row) for row in value["files"]]
    normalized = batch_plan.build_batch_plan(raw, **{key: manifest[key] for key in
        ("source_folder_id", "output_folder_id", "work_folder_id")})["files"]
    _require(_same(normalized, value["files"]), "SOURCE_PROJECTION_MISMATCH")
    rebuilt = video_queue.build_drive_manifest(
        {key: manifest[key] for key in video_queue.INTAKE_FIELDS}, raw)
    _require(_same(rebuilt, manifest), "MANIFEST_PROJECTION_MISMATCH")
    core = {key: value[key] for key in (
        "plan_sha256", "subset_sha256", "subset_sequence", "processing_profile",
        "algorithm_revision", "canary_source_file_id",
    )}
    _require(manifest["request_key"] == "subset-v1:" + batch_plan._digest(core)
             and all(manifest[key] == value[key] for key in
                     ("processing_profile", "algorithm_revision", "canary_source_file_id")),
             "REQUEST_PROJECTION_MISMATCH")
    return manifest


@_safe
def build_binding(contract, *, expected_contract_sha256, approval_id, batch_row, job_rows):
    """Bind actual persisted UUIDs to the exact approved immutable queue fields.

    Intended to run against rows read inside the future admission transaction.
    Does not create UUIDs, inspect mutable status or prove that a commit occurred.
    """
    manifest = _contract(contract, expected_contract_sha256)
    subset_contract._uuid(approval_id)
    _require(isinstance(batch_row, dict) and isinstance(job_rows, list), "INVALID_QUEUE_ROWS")
    batch_id = subset_contract._uuid(batch_row["batch_id"])
    _require(batch_row["source_kind"] == "google_drive_folder"
             and _same({key: batch_row[key] for key in BATCH_FIELDS},
                       {key: manifest[key] for key in BATCH_FIELDS}), "BATCH_BINDING_MISMATCH")
    _require(len(job_rows) == manifest["expected_count"], "JOB_SET_MISMATCH")
    planned = {row["file_id"]: row for row in contract["files"]}
    projected = {row["file_id"]: row for row in manifest["files"]}
    jobs, ids, sources = [], set(), set()
    for row in job_rows:
        _require(isinstance(row, dict), "INVALID_QUEUE_ROWS")
        file_id = row["source_file_id"]
        job_id = subset_contract._uuid(row["job_id"])
        _require(file_id in projected and file_id not in sources and job_id not in ids,
                 "JOB_SET_MISMATCH")
        item = projected[file_id]
        expected = dict(batch_id=batch_id, sequence=item["sequence"], source_file_id=file_id,
                        source_name=item["name"], source_mime_type=item["mime_type"],
                        source_size_bytes=item["size_bytes"], source_checksum=item["checksum"],
                        stable_job_key=hashlib.sha256(
                            ("bridge-video|drive|" + file_id).encode()).hexdigest()[:32],
                        is_canary=file_id == manifest["canary_source_file_id"])
        _require(_same({key: row[key] for key in expected}, expected), "JOB_BINDING_MISMATCH")
        ids.add(job_id)
        sources.add(file_id)
        jobs.append(dict(file_id=file_id, job_id=job_id, sequence=item["sequence"],
                         source_identity=planned[file_id]["source_identity"],
                         source_fingerprint=planned[file_id]["source_fingerprint"]))
    core = dict(schema="universal-video-admission-binding-v1", approval_id=approval_id,
                request_key=manifest["request_key"], batch_id=batch_id,
                jobs=sorted(jobs, key=lambda row: row["sequence"]),
                **{key: contract[key] for key in ("plan_sha256", "subset_sha256",
                   "contract_sha256", "queue_manifest_sha256")}, **DISABLED)
    return {**core, "binding_sha256": batch_plan._digest(core)}


@_safe
def recover_binding(contract, *, expected_contract_sha256, approval_id,
                    persisted_binding, batch_row, job_rows):
    """Validate immutable recovery evidence, never re-enqueue or refresh a source.

    Even revoked/expired approvals may be read to recover an already committed
    binding. They cannot authorize a new admission or attempt. Missing legacy
    bindings are explicit errors, never adopted from a matching request key.
    """
    _require(isinstance(persisted_binding, dict), "BINDING_MISSING_HOLD")
    rebuilt = build_binding(contract, expected_contract_sha256=expected_contract_sha256,
                            approval_id=approval_id, batch_row=batch_row, job_rows=job_rows)
    _require(_same(rebuilt, persisted_binding), "IMMUTABLE_BINDING_MISMATCH_HOLD")
    return dict(status="RECOVERED_IMMUTABLE_BINDING", binding=rebuilt, **DISABLED)


@_safe
def project_lease_binding(contract, *, expected_contract_sha256, approval_id,
                          persisted_binding, batch_row, job_rows):
    """Revalidate, then project to existing check_attempt's exact binding schema."""
    bound = recover_binding(contract, expected_contract_sha256=expected_contract_sha256,
                            approval_id=approval_id, persisted_binding=persisted_binding,
                            batch_row=batch_row, job_rows=job_rows)["binding"]
    projected = {key: bound[key] for key in
                 ("contract_sha256", "queue_manifest_sha256", "request_key", "batch_id")}
    projected["jobs"] = [{key: row[key] for key in ("file_id", "job_id")} for row in bound["jobs"]]
    return dict(binding=projected, **DISABLED)


@_safe
def check_source_observation(contract, *, expected_contract_sha256, observation, now,
                             admission_nonce=None, expected_attempt=None):
    """Check a prospective trusted observation, separately from binding recovery.

    The future server owns the observation row, its clock and its nonce/token;
    this pure checker cannot establish that trust or validate a live lease.
    expected_attempt is the independently read job_id/source_file_id/lease_token/
    attempt_count tuple. Call existing check_attempt too for its full lease gate,
    passing project_lease_binding(...)["binding"], not the richer stored binding.
    Re-evaluate age after acquiring admission locks and before attempt work.
    """
    manifest = _contract(contract, expected_contract_sha256)
    subset_contract._shape(observation,
        "observation_id contract_sha256 observed_at expires_at scope sources")
    subset_contract._uuid(observation["observation_id"])
    _require(observation["contract_sha256"] == expected_contract_sha256,
             "OBSERVATION_CONTRACT_MISMATCH")
    observed, expires, current = (subset_contract._time(observation["observed_at"]),
                                 subset_contract._time(observation["expires_at"]),
                                 subset_contract._time(now))
    _require(observed <= current < expires and 0 < (expires - observed).total_seconds() <= 30,
             "SOURCE_OBSERVATION_EXPIRED")
    if expected_attempt is None:
        subset_contract._uuid(admission_nonce)
        scope = dict(phase="ADMISSION", request_key=manifest["request_key"], nonce=admission_nonce)
        source_ids = [row["file_id"] for row in contract["files"]]
    else:
        _require(admission_nonce is None, "AMBIGUOUS_OBSERVATION_SCOPE")
        subset_contract._shape(expected_attempt, "job_id source_file_id lease_token attempt_count")
        subset_contract._uuid(expected_attempt["job_id"])
        subset_contract._uuid(expected_attempt["lease_token"])
        _require(type(expected_attempt["attempt_count"]) is int
                 and 1 <= expected_attempt["attempt_count"] <= 3, "INVALID_ATTEMPT")
        _require(expected_attempt["source_file_id"] in {row["file_id"] for row in contract["files"]},
                 "ATTEMPT_SOURCE_MISMATCH")
        scope = dict(phase="ATTEMPT", request_key=manifest["request_key"], **expected_attempt)
        source_ids = [expected_attempt["source_file_id"]]
    _require(_same(scope, observation["scope"]), "OBSERVATION_SCOPE_MISMATCH")
    subset_contract._sources(contract, observation["sources"], source_ids)
    return dict(status="CONSISTENT_ONLY", phase=scope["phase"],
                observation_sha256=batch_plan._digest(observation), **DISABLED)
