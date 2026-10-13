"""Explicit single-transaction client for the unapplied 0060 admission boundary.

This module does not discover credentials, approve sources, collect metadata or
retry mutations. Importing it performs no I/O. The server owns authority and the
actual queue mapping; these client checks reject an inconsistent receipt before
commit. No binding is returned to the caller until the connection context commits.
"""
from __future__ import annotations

import re

from . import admission_binding, batch_plan, subset_contract


class AdmissionClientError(ValueError):
    """A bounded diagnostic which never includes a DSN or database error text."""


class AdmissionOutcomeUnknown(AdmissionClientError):
    """Recover the original immutable request; do not blindly repeat admission."""


def _require(condition, code):
    if not condition:
        raise AdmissionClientError(code)


def _inputs(contract, expected_contract_sha256, approval_id, database_url):
    try:
        manifest = admission_binding._contract(contract, expected_contract_sha256)
        subset_contract._uuid(approval_id)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise AdmissionClientError("INVALID_ADMISSION_INPUT") from None
    _require(isinstance(database_url, str) and bool(database_url.strip()), "EXPLICIT_DATABASE_URL_REQUIRED")
    return manifest


def _receipt(value, contract, approval_id, *, recovery):
    """Validate transport output, not database existence or approval provenance."""
    try:
        _require(isinstance(value, dict), "INVALID_SERVER_RECEIPT")
        disabled = admission_binding.DISABLED
        if recovery and value.get("status") == "NOT_FOUND":
            _require(batch_plan._digest(value) == batch_plan._digest(dict(status="NOT_FOUND", **disabled)),
                     "INVALID_SERVER_RECEIPT")
            return value
        statuses = {"RECOVERED_IMMUTABLE_BINDING"} if recovery else {"ADMITTED", "RECOVERED_IMMUTABLE_BINDING"}
        _require(set(value) == {"status", "binding", *disabled} and value["status"] in statuses,
                 "INVALID_SERVER_RECEIPT")
        _require(all(value[key] is False for key in disabled), "INVALID_SERVER_RECEIPT")
        binding = value["binding"]
        manifest = contract["enqueue_manifest"]
        expected = dict(schema="universal-video-admission-binding-v1", approval_id=approval_id,
                        request_key=manifest["request_key"], **disabled,
                        **{key: contract[key] for key in ("plan_sha256", "subset_sha256",
                           "contract_sha256", "queue_manifest_sha256")})
        _require(isinstance(binding, dict) and set(binding) == {*expected, "batch_id", "jobs", "binding_sha256"},
                 "INVALID_SERVER_RECEIPT")
        _require(batch_plan._digest({key: binding[key] for key in expected}) == batch_plan._digest(expected),
                 "SERVER_BINDING_ANCHOR_MISMATCH")
        subset_contract._uuid(binding["batch_id"])
        _require(isinstance(binding["jobs"], list) and len(binding["jobs"]) == manifest["expected_count"],
                 "SERVER_JOB_MAPPING_MISMATCH")
        sources = {row["file_id"]: row for row in contract["files"]}
        seen = set()
        for job, planned in zip(binding["jobs"], manifest["files"]):
            _require(isinstance(job, dict) and set(job) == {
                "file_id", "job_id", "sequence", "source_identity", "source_fingerprint"},
                "SERVER_JOB_MAPPING_MISMATCH")
            actual_id = subset_contract._uuid(job["job_id"])
            _require(actual_id not in seen, "SERVER_JOB_MAPPING_MISMATCH")
            seen.add(actual_id)
            source = sources[planned["file_id"]]
            expected_job = dict(job_id=actual_id, file_id=planned["file_id"], sequence=planned["sequence"],
                                source_identity=source["source_identity"],
                                source_fingerprint=source["source_fingerprint"])
            _require(batch_plan._digest(job) == batch_plan._digest(expected_job), "SERVER_JOB_MAPPING_MISMATCH")
        core = {key: item for key, item in binding.items() if key != "binding_sha256"}
        _require(binding["binding_sha256"] == batch_plan._digest(core), "SERVER_BINDING_DIGEST_MISMATCH")
        return value
    except AdmissionClientError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise AdmissionClientError("INVALID_SERVER_RECEIPT") from None


def _call(database_url, statement, parameters, contract, approval_id, *, recovery):
    # Lazy import: collection, validation and module import never connect.
    from psycopg import connect

    try:
        with connect(database_url, autocommit=False, connect_timeout=8,
                     options="-c statement_timeout=15000 -c lock_timeout=5000",
                     application_name="universal-video-subset-admission") as connection:
            with connection.cursor() as cursor:
                cursor.execute(statement, parameters)
                row = cursor.fetchone()
                _require(isinstance(row, (tuple, list)) and len(row) == 1, "INVALID_SERVER_RECEIPT")
                receipt = _receipt(row[0], contract, approval_id, recovery=recovery)
        # Context exit is the only commit. A failed COMMIT never reaches return.
        return receipt
    except AdmissionClientError:
        raise
    except Exception as exc:
        message = getattr(getattr(exc, "diag", None), "message_primary", None)
        if getattr(exc, "sqlstate", None) == "P0001" and isinstance(message, str) and re.fullmatch(
                r"(?:VIDEO_(?:ADMISSION|SUBSET)|SUBSET)_[A-Z0-9_]{1,80}", message):
            raise AdmissionClientError(message) from None
        # Includes loss of a COMMIT acknowledgement. No retry, new key or UUID.
        raise AdmissionOutcomeUnknown("TRANSACTION_OUTCOME_UNKNOWN_RECOVER_ORIGINAL_REQUEST") from None


def admit_approved_subset(contract, *, expected_contract_sha256, approval_id, observation_id, database_url):
    """Call existing-queue admission once, returning only after successful commit."""
    manifest = _inputs(contract, expected_contract_sha256, approval_id, database_url)
    try:
        subset_contract._uuid(observation_id)
    except (ValueError, TypeError):
        raise AdmissionClientError("INVALID_OBSERVATION_ID") from None
    return _call(database_url,
                 "SELECT video_queue.admit_approved_subset(%s::uuid,%s::text,%s::text,%s::text,%s::uuid)",
                 (approval_id, manifest["request_key"], expected_contract_sha256,
                  contract["queue_manifest_sha256"], observation_id), contract, approval_id, recovery=False)


def recover_subset_admission(contract, *, expected_contract_sha256, approval_id, database_url):
    """Read original immutable evidence; no current metadata or observation needed."""
    manifest = _inputs(contract, expected_contract_sha256, approval_id, database_url)
    return _call(database_url,
                 "SELECT video_queue.recover_subset_admission(%s::text,%s::text,%s::text)",
                 (manifest["request_key"], expected_contract_sha256, contract["queue_manifest_sha256"]),
                 contract, approval_id, recovery=True)
