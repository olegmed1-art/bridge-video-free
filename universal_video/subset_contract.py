"""Offline consistency prototype over the existing durable queue, not an executor.

Build one immutable subset contract from an independently approved plan digest.
Reuse queue's pure manifest builder; never discover/enqueue/claim/retry/release.
Admission, binding, lease and terminal observations below are prospective adapter
inputs, NOT new database schemas or authenticated evidence. Trusted collection,
atomic uniqueness/CAS, live source verification and server-side fencing remain
deployment prerequisites. Caller-supplied timestamps and hashes prove no trust.

All results retain execution_allowed=false and release_allowed=false. Claims and
retries are canary-only. A terminal PASS prepares a HOLD review packet; an explicit
authorized decision bound to that packet and an atomic audit/release transaction
are separate, unimplemented prerequisites. No function accepts a release flag.
No CLI, state store, clock, credential lookup, network or file writes.
"""
from __future__ import annotations

from datetime import datetime
from functools import wraps
import hashlib
import re
from uuid import UUID

from . import batch_plan, video_queue
from .terminal_evidence_v2 import TerminalEvidenceV2Error, validate_terminal_output


MAX_OBSERVATION_AGE_SECONDS = 30
DISABLED = dict(execution_allowed=False, release_allowed=False,
                database_persistence_allowed=False, canonical_promotion_allowed=False)


class SubsetContractError(ValueError):
    """Fixed error codes only; never input values or dependency exception text."""


def _safe(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except SubsetContractError:
            raise
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError,
                TerminalEvidenceV2Error) as exc:
            raise SubsetContractError("INVALID_CONTRACT_INPUT") from exc
    return checked


def _require(condition, code):
    if not condition:
        raise SubsetContractError(code)


def _shape(value, keys):
    _require(isinstance(value, dict) and set(value) == set(keys.split()), "INVALID_OBSERVATION")
    batch_plan._digest(value)  # Finite JSON and valid Unicode, including unused fields.


def _sha(value):
    _require(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value), "INVALID_DIGEST")
    return value


def _uuid(value):
    _require(isinstance(value, str) and str(UUID(value)) == value, "INVALID_QUEUE_ID")
    return value


def _time(value):
    _require(isinstance(value, str), "INVALID_TIME")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require(parsed.tzinfo is not None, "INVALID_TIME")
    return parsed


def _fresh(observation, now):
    age = (_time(now) - _time(observation["observed_at"])).total_seconds()
    _require(0 <= age <= MAX_OBSERVATION_AGE_SECONDS, "STALE_OBSERVATION")


def _raw(file):
    return {"id": file["file_id"], "name": file["name"], "parents": [file["parent"]],
            "mimeType": file["mime_type"], "size": file["size_bytes"], "trashed": file["trashed"],
            "version": file["version"], "modifiedTime": file["modifiedTime"], **file["checksums"],
            "videoMediaMetadata": {"durationMillis": file["duration_ms"]},
            "estimated_cost_usd": file["estimated_cost_usd"]}


@_safe
def build_subset_contract(plan, *, expected_plan_sha256, subset_sequence,
                          processing_profile, algorithm_revision, canary_source_file_id):
    """Revalidate the entire plan, then project exactly one subset into queue format."""
    _require(plan["plan_sha256"] == _sha(expected_plan_sha256), "PLAN_DIGEST_MISMATCH")
    options = {key: plan[key] for key in ("source_folder_id", "output_folder_id", "work_folder_id",
                                        "max_jobs_per_batch")}
    rebuilt = batch_plan.build_batch_plan([_raw(file) for file in plan["files"]], **options)
    _require(batch_plan._digest(plan) == batch_plan._digest(rebuilt), "PLAN_CONTENT_MISMATCH")
    _require(type(subset_sequence) is int and 1 <= subset_sequence <= len(plan["batches"]),
             "INVALID_SUBSET")
    subset = rebuilt["batches"][subset_sequence - 1]
    files = [file for file in rebuilt["files"] if file["file_id"] in set(subset["file_ids"])]
    request_core = {"plan_sha256": expected_plan_sha256, "subset_sha256": subset["subset_sha256"],
                    "subset_sequence": subset_sequence, "processing_profile": processing_profile,
                    "algorithm_revision": algorithm_revision, "canary_source_file_id": canary_source_file_id}
    request = {key: rebuilt[key] for key in ("source_folder_id", "output_folder_id", "work_folder_id")}
    request.update(processing_profile=processing_profile, algorithm_revision=algorithm_revision,
                   canary_source_file_id=canary_source_file_id,
                   request_key="subset-v1:" + batch_plan._digest(request_core))
    # This builder is pure; the network-bearing discover_drive_manifest is never called.
    manifest = video_queue.build_drive_manifest(request, [_raw(file) for file in files])
    _require(all(manifest[key] == request[key] for key in request), "QUEUE_REQUEST_NORMALIZED")
    projected = {file["file_id"]: file for file in manifest["files"]}
    _require(len(projected) == len(files) == manifest["expected_count"], "QUEUE_SUBSET_MISMATCH")
    for file in files:
        row = projected[file["file_id"]]
        _require(all(row[key] == file[key] for key in
                     ("name", "mime_type", "size_bytes", "checksum")), "QUEUE_PROJECTION_MISMATCH")
    core = {"schema": "universal-video-subset-contract-v1", **request_core, "files": files,
            "enqueue_manifest": manifest, "queue_manifest_sha256": batch_plan._digest(manifest), **DISABLED}
    return {**core, "contract_sha256": batch_plan._digest(core)}


def _contract(contract, expected):
    _shape(contract, "schema plan_sha256 subset_sha256 subset_sequence processing_profile "
           "algorithm_revision canary_source_file_id files enqueue_manifest queue_manifest_sha256 "
           "execution_allowed release_allowed database_persistence_allowed canonical_promotion_allowed contract_sha256")
    core = {key: value for key, value in contract.items() if key != "contract_sha256"}
    _require(contract["schema"] == "universal-video-subset-contract-v1"
             and all(contract[key] is False for key in DISABLED), "INVALID_CONTRACT")
    _require(batch_plan._digest(core) == contract["contract_sha256"] == _sha(expected),
             "CONTRACT_DIGEST_MISMATCH")
    _require(batch_plan._digest(contract["files"]) == contract["subset_sha256"]
             and batch_plan._digest(contract["enqueue_manifest"]) == contract["queue_manifest_sha256"],
             "SUBSET_DIGEST_MISMATCH")
    return contract["enqueue_manifest"]


def _sources(contract, raw, expected_ids):
    manifest = contract["enqueue_manifest"]
    observed = batch_plan.build_batch_plan(raw, **{key: manifest[key] for key in
        ("source_folder_id", "output_folder_id", "work_folder_id")})["files"]
    _require({file["file_id"] for file in observed} == set(expected_ids), "SOURCE_SET_MISMATCH")
    approved = {file["file_id"]: file for file in contract["files"]}
    for file in observed:
        planned = approved[file["file_id"]]
        _require(file["source_fingerprint"] == planned["source_fingerprint"], "SOURCE_REVISION_CHANGED")
        _require(file["name"] == planned["name"], "SOURCE_NAME_CHANGED")


def _receipt(contract, phase, observation):
    return {"schema": "universal-video-subset-check-v1", "status": "CONSISTENT_ONLY", "phase": phase,
            "contract_sha256": contract["contract_sha256"],
            "observation_sha256": batch_plan._digest(observation), **DISABLED}


@_safe
def check_admission(contract, *, expected_contract_sha256, observed_sources, ledger, now):
    """Strict no-readmission policy, even under a new key/profile/source revision.

    A complete fresh ledger is a caller observation, not a transaction guarantee.
    Production must repeat this check and reserve identities atomically with enqueue.
    """
    manifest = _contract(contract, expected_contract_sha256)
    _shape(ledger, "complete generation observed_at admissions")
    _require(ledger["complete"] is True and type(ledger["generation"]) is int
             and ledger["generation"] >= 0 and isinstance(ledger["admissions"], list), "INCOMPLETE_LEDGER")
    _fresh(ledger, now)
    identities = {file["source_identity"] for file in contract["files"]}
    seen_keys, seen_sources = set(), set()
    for event in ledger["admissions"]:
        _shape(event, "request_key contract_sha256 source_identities")
        _sha(event["contract_sha256"])
        key, sources = event["request_key"], event["source_identities"]
        _require(isinstance(key, str) and video_queue.REQUEST_KEY_RE.fullmatch(key)
                 and key not in seen_keys and isinstance(sources, list) and sources, "INVALID_LEDGER")
        seen_keys.add(key)
        for identity in sources:
            _require(isinstance(identity, str) and identity.startswith("google_drive:"), "INVALID_LEDGER")
            batch_plan._drive_id(identity.removeprefix("google_drive:"), "source_identity")
            _require(identity not in seen_sources, "INVALID_LEDGER")
            seen_sources.add(identity)
    _require(manifest["request_key"] not in seen_keys and not identities.intersection(seen_sources),
             "ALREADY_ADMITTED")
    _sources(contract, observed_sources, [file["file_id"] for file in contract["files"]])
    return _receipt(contract, "ADMISSION", {"ledger": ledger, "sources": observed_sources})


def _bound_claim(contract, binding, claim):
    manifest = contract["enqueue_manifest"]
    _shape(binding, "contract_sha256 queue_manifest_sha256 request_key batch_id jobs")
    _require(binding["contract_sha256"] == contract["contract_sha256"]
             and binding["queue_manifest_sha256"] == contract["queue_manifest_sha256"]
             and binding["request_key"] == manifest["request_key"], "ADMISSION_BINDING_MISMATCH")
    _uuid(binding["batch_id"])
    _require(isinstance(binding["jobs"], list), "INVALID_JOB_BINDING")
    jobs, job_ids = {}, set()
    for job in binding["jobs"]:
        _shape(job, "file_id job_id")
        file_id = batch_plan._drive_id(job["file_id"], "file_id")
        job_id = _uuid(job["job_id"])
        _require(file_id not in jobs and job_id not in job_ids, "INVALID_JOB_BINDING")
        jobs[file_id] = job_id
        job_ids.add(job_id)
    _require(set(jobs) == {file["file_id"] for file in contract["files"]}, "INVALID_JOB_BINDING")
    _shape(claim, "job_id batch_id lease_token sequence source_folder_id output_folder_id work_folder_id "
           "processing_profile algorithm_revision source_file_id source_name source_mime_type source_size_bytes "
           "source_checksum stable_job_key is_canary attempt_count")
    _uuid(claim["lease_token"])
    _require(type(claim["attempt_count"]) is int and 1 <= claim["attempt_count"] <= 3, "INVALID_ATTEMPT")
    file_id = claim["source_file_id"]
    _require(file_id in jobs and claim["job_id"] == jobs[file_id]
             and claim["batch_id"] == binding["batch_id"], "CLAIM_BINDING_MISMATCH")
    row = next(file for file in manifest["files"] if file["file_id"] == file_id)
    expected = {key: manifest[key] for key in ("source_folder_id", "output_folder_id", "work_folder_id",
                                               "processing_profile", "algorithm_revision")}
    expected.update(sequence=row["sequence"], source_file_id=file_id, source_name=row["name"],
                    source_mime_type=row["mime_type"], source_size_bytes=row["size_bytes"],
                    source_checksum=row["checksum"], is_canary=file_id == manifest["canary_source_file_id"],
                    # Exact formula of existing migration 0056, not a new job identity.
                    stable_job_key=hashlib.sha256(("bridge-video|drive|" + file_id).encode()).hexdigest()[:32])
    _require(batch_plan._digest({key: claim[key] for key in expected}) == batch_plan._digest(expected),
             "CLAIM_CONTENT_MISMATCH")
    _require(claim["is_canary"] is True, "RELEASE_NOT_AUTHORIZED")


@_safe
def check_attempt(contract, *, expected_contract_sha256, binding, claim, lease,
                  observed_source, worker_key, now, phase="CLAIM"):
    """Validate a canary claim or pre-retry observation; never schedule a retry."""
    _contract(contract, expected_contract_sha256)
    _bound_claim(contract, binding, claim)
    _require(phase in ("CLAIM", "RETRY"), "INVALID_PHASE")
    _shape(lease, "job_id batch_id lease_token lease_owner attempt_count status lease_expires_at observed_at")
    _fresh(lease, now)
    _require(isinstance(worker_key, str) and video_queue.REQUEST_KEY_RE.fullmatch(worker_key), "INVALID_WORKER")
    _require(all(lease[key] == claim[key] for key in ("job_id", "batch_id", "lease_token", "attempt_count"))
             and type(lease["attempt_count"]) is int and lease["lease_owner"] == worker_key
             and lease["status"] == "LEASED" and _time(lease["lease_expires_at"]) > _time(now), "LEASE_MISMATCH")
    _require(phase != "RETRY" or claim["attempt_count"] < 3, "RETRY_EXHAUSTED")
    _sources(contract, [observed_source], [claim["source_file_id"]])
    return _receipt(contract, phase, {"binding": binding, "claim": claim, "lease": lease,
                                      "source": observed_source})


@_safe
def prepare_canary_review(contract, *, expected_contract_sha256, binding, claim, state,
                          terminal_output, expected_terminal_sha256, observed_sources, now):
    """Prepare a HOLD packet, not terminal/live verification or release approval.

    Reuse the existing pure v2 consistency validator. Full 0058/live artifact
    checks, authorized reviewer identity and transactional audit remain required.
    The snapshot of the successful attempt must still match its persisted event.
    """
    _contract(contract, expected_contract_sha256)
    _bound_claim(contract, binding, claim)
    _shape(state, "batch_id job_id lease_token attempt_count batch_status job_status released_jobs "
           "terminal_output_sha256 observed_at")
    _fresh(state, now)
    _require(all(state[key] == claim[key] for key in ("batch_id", "job_id", "lease_token", "attempt_count"))
             and type(state["attempt_count"]) is int and state["batch_status"] == "CANARY_REVIEW"
             and state["job_status"] == "REVIEW_READY" and type(state["released_jobs"]) is int
             and state["released_jobs"] == 0, "CANARY_STATE_MISMATCH")
    _require(isinstance(terminal_output, dict), "INVALID_TERMINAL_OUTPUT")
    _require(batch_plan._digest(terminal_output) == state["terminal_output_sha256"]
             == _sha(expected_terminal_sha256), "TERMINAL_DIGEST_MISMATCH")
    validate_terminal_output(claim, terminal_output)
    _sources(contract, observed_sources, [file["file_id"] for file in contract["files"]])
    packet = {"schema": "universal-video-subset-review-v1", "status": "HOLD_FOR_AUTHORIZED_REVIEW",
              "contract_sha256": contract["contract_sha256"], "plan_sha256": contract["plan_sha256"],
              "subset_sha256": contract["subset_sha256"], "binding_sha256": batch_plan._digest(binding),
              "canary_event_sha256": batch_plan._digest(state), "terminal_output_sha256": expected_terminal_sha256,
              "source_observation_sha256": batch_plan._digest(observed_sources),
              "remaining_file_ids": sorted(file["file_id"] for file in contract["files"]
                                           if file["file_id"] != claim["source_file_id"]),
              "required_decision": "AUTHORIZED_APPROVAL_OR_REJECTION_OF_EXACT_REMAINDER",
              "audit_required": True, **DISABLED}
    return {"review_packet": packet, "review_sha256": batch_plan._digest(packet), **DISABLED}
