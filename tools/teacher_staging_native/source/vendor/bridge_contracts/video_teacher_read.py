"""Pure staging reader contract. No HTTP route or production adapter is installed.

The adapter is a SERVER dependency, never request JSON or a generated artifact.
It must check existing authenticated actor/school/teacher access and current
revocation in one read-only snapshot, and filter by school BEFORE reading rows.
Hashes prove equality only; the out-of-band binding also needs a current,
independent disclosure decision for the exact text shown to a teacher.
"""
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
import hashlib
import math
import re
from typing import Protocol
from uuid import UUID

from bridge_contracts.video_canon_replay import digest

SCHEMA = "video-teacher-staging-read-v1"
MAX_SNAPSHOT_AGE = timedelta(seconds=60)


class ReaderUnavailable(Exception):
    """Adapter cannot establish a complete, current trusted snapshot."""


class ScopedSnapshot(Protocol):
    def authorization(self) -> dict: ...

    def lookup(self, *, candidate_id: str, school_id: str) -> dict | None:
        """Return at most one school-filtered row with source/run/binding/receipt.

        Use both arguments in storage predicates, including every related row.
        Never SELECT an arbitrary candidate then inspect its school in Python.
        Missing links remain null; do not replace them with latest/nearby rows.
        """
        ...

    def revalidate(self, *, candidate_id: str, school_id: str) -> dict:
        """Fresh atomic authorization + scoped data immediately before disclosure.

        Return {authorization, data}; omit data unless access is still valid.
        Recheck current candidate/source/run/read-disclosure revocation, not a
        cached or repeatable-read snapshot. This requires an actual adapter
        consistency/fencing policy; this pure interface does not provide one.
        """
        ...


class TrustedReader(Protocol):
    def snapshot(self) -> AbstractContextManager[ScopedSnapshot]: ...


class _Blocked(Exception):
    pass


def _need(condition, code):
    if not condition:
        raise _Blocked(code)


def _uuid(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except ValueError:
        return False


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _current(record, now, code):
    checked, until = record.get("checked_at"), record.get("valid_until")
    _need(record.get("status") == "active" and record.get("revoked") is False, code)
    _need(isinstance(checked, datetime) and checked.utcoffset() is not None
          and isinstance(until, datetime) and until.utcoffset() is not None, code)
    _need(now - MAX_SNAPSHOT_AGE <= checked <= now < until, code)


def _project(payload, kind, *, definition_replay=None):
    """Explicitly supported existing contracts only; no generic parser."""
    if kind == "video_artifact_source_observation":
        _need(payload["schema"] == "video-artifact-source-observation-v1"
              and payload["stage"] == "SOURCE_OBSERVATION", "STAGE_UNSUPPORTED")
        text = payload["transcript"]
        display = {"quote": text["quote"], "start": text["start"], "end": text["end"]}
        claims = {
            "source_video_file_id": payload["source_video_file_id"],
            "source_media_sha256": payload["source_media_sha256_claim"],
            "parent_raw_sha256": payload["parent_raw_sha256"],
            "parent_canonical_sha256": payload["parent_canonical_sha256"],
        }
        text_hash, stage = text["text_sha256"], "SOURCE_OBSERVATION"
    elif kind == "video_source_knowledge_draft":
        _need(payload["schema"] == "video-source-knowledge-draft-v1"
              and payload["status"] == "CANDIDATE_RESEARCH", "STAGE_UNSUPPORTED")
        text, source, parent = payload["transcript"], payload["source_claim"], payload["parent_document_claim"]
        display = {"quote": text["exact_text"], "start": text["start"], "end": text["end"]}
        claims = {
            "source_video_file_id": source["drive_file_id"],
            "source_media_sha256": source["source_sha256"],
            "parent_raw_sha256": parent["embedded_raw_sha256"],
            "parent_canonical_sha256": parent["embedded_canonical_sort_compact_utf8_sha256"],
        }
        text_hash, stage = text["exact_text_utf8_sha256"], "CANDIDATE_RESEARCH"
    elif kind == 'video_school_canon_candidate':
        from bridge_contracts.video_definition_staging import project_definition
        stage, display, claims = project_definition(payload, definition_replay)
        text_hash = payload['knowledge_version']['content']['definition_evidence']['source_binding']['raw_text_sha256']
    else:
        raise _Blocked("STAGE_UNSUPPORTED")
    _need(isinstance(display["quote"], str) and bool(display["quote"].strip()), "TEXT_INVALID")
    _need(hashlib.sha256(display["quote"].encode()).hexdigest() == text_hash, "TEXT_HASH_MISMATCH")
    _need(all(type(display[k]) in (int, float) and math.isfinite(display[k]) for k in ("start", "end"))
          and 0 <= display["start"] < display["end"], "INTERVAL_INVALID")
    _need(isinstance(claims["source_video_file_id"], str) and bool(claims["source_video_file_id"]), "SOURCE_BINDING_MISSING")
    _need(all(_sha(v) for k, v in claims.items() if k.endswith("sha256")), "SOURCE_BINDING_MISSING")
    return stage, display, claims


def _validate(candidate_id, school_id, data, now, *, school_internal=False):
    row = data["candidate"]
    _need(row["analysis_candidate_id"] == candidate_id and row["school_id"] == school_id, "SCOPE_BINDING_MISMATCH")
    _need(row["status"] == "active" and row["promotion_status"] in ("staging", "review_queue"), "CANDIDATE_NOT_CURRENT_STAGING")
    _need(_uuid(row.get("source_id")) and _uuid(row.get("analysis_run_id")), "SOURCE_RUN_BINDING_MISSING")
    source, run = data.get("source"), data.get("run")
    _need(isinstance(source, dict) and isinstance(run, dict), "SOURCE_RUN_BINDING_MISSING")
    _need(source["source_id"] == row["source_id"] and run["analysis_run_id"] == row["analysis_run_id"]
          and source["school_id"] == run["school_id"] == school_id
          and run["input_source_id"] == source["source_id"], "SOURCE_RUN_BINDING_MISMATCH")
    allowed_run_states = ('success', 'partial_success', 'running') if school_internal else ('success', 'partial_success')
    _need(source["status"] == "active" and run["run_status"] in allowed_run_states
          and run["publication_authorization_status"] in ("blocked", "allowed"), "SOURCE_RUN_NOT_CURRENT")
    payload = row["payload"]
    _need(_sha(row["payload_hash"]) and digest(payload) == row["payload_hash"], "PAYLOAD_HASH_MISMATCH")
    binding = data.get("binding")
    _need(isinstance(binding, dict), "TRUSTED_READ_BINDING_MISSING")
    _current(binding, now, "READ_BINDING_STALE_OR_REVOKED")
    for key in ("analysis_candidate_id", "school_id", "source_id", "analysis_run_id", "payload_hash"):
        _need(binding.get(key) == row[key], "READ_BINDING_MISMATCH")
    receipt = data.get("receipt")
    _need(isinstance(receipt, dict), "REPLAY_RECEIPT_MISSING")
    receipt_hash = receipt.get("replay_sha256")
    _need(_sha(receipt_hash) and binding.get("replay_sha256") == receipt_hash
          and digest({k: v for k, v in receipt.items() if k != "replay_sha256"}) == receipt_hash,
          "REPLAY_RECEIPT_HASH_MISMATCH")
    _need(receipt["schema"] == "video-canon-staging-replay-v1"
          and receipt["authoritative_write_performed"] is False and receipt["promotion_commands"] == [],
          "REPLAY_RECEIPT_UNSAFE")
    members = [m for m in receipt["candidates"] if m.get("payload_hash") == row["payload_hash"]]
    _need(len(members) == 1, "REPLAY_CANDIDATE_BINDING_MISMATCH")
    member = members[0]
    compare_fields=("candidate_type", "method_version", "quality_status", "payload")
    if row['candidate_type']=='video_school_canon_candidate':
        from bridge_contracts.definition_staging_identity import definition_staging_keys
        logical=payload['knowledge_item']['stable_key']
        _need(member.get('stable_key')==logical and row['stable_key'] in definition_staging_keys(logical,row['payload_hash']),
              'REPLAY_CANDIDATE_BINDING_MISMATCH')
    else:
        _need(member.get('stable_key')==row['stable_key'],'REPLAY_CANDIDATE_BINDING_MISMATCH')
    _need(all(member.get(k) == row[k] for k in compare_fields)
          and member.get("promotion_status") == "STAGING_ONLY"
          and member.get("authoritative_tables_modified") is False, "REPLAY_CANDIDATE_BINDING_MISMATCH")
    stage, display, claims = _project(payload, row["candidate_type"], definition_replay=receipt)
    _need(all(binding.get(k) == v for k, v in claims.items()), "SOURCE_HASH_BINDING_MISMATCH")
    _need(source["video_file_id"] == claims["source_video_file_id"]
          and source["media_sha256"] == claims["source_media_sha256"], "SOURCE_HASH_BINDING_MISMATCH")
    # Not a claim inside payload: an independent server disclosure decision.
    # Omitting it blocks even a byte-perfect replay. Images/hands are never sent.
    if not school_internal:
        _need(binding.get("disclosure_status") == "TEACHER_EXCERPT_ALLOWED"
              and binding.get("display_sha256") == digest(display), "DISCLOSURE_BINDING_MISSING")
    return {"schema": SCHEMA, "status": "READABLE_STAGING", "stage": stage,
            "candidate_id": candidate_id, "source_id": row["source_id"], "analysis_run_id": row["analysis_run_id"],
            "payload_sha256": row["payload_hash"], "replay_sha256": receipt_hash, "excerpt": display,
            "speaker_identity_status": "UNKNOWN", "speaker_role": "UNKNOWN",
            "media_review_status": "UNVERIFIED", "accepted_canon": [], "canon_write_allowed": False,
            "gaps": ["IDENTITY_UNVERIFIED", "MEDIA_UNVERIFIED", "CANON_NOT_ACCEPTED"]}


def read_teacher_video_candidate(candidate_id: str, *, reader: TrustedReader | None = None,
                                 now: datetime | None = None) -> dict:
    """Only candidate UUID is a request input. Other arguments are server-only.

    No adapter is provided by this module. Absence fails closed. In particular,
    do not build the adapter from request roles, school IDs, bearer-token presence,
    artifact VERIFIED labels, local file hashes, or a cached permission receipt.
    The clock override exists for deterministic local contract tests only.
    """
    try:
        _need(_uuid(candidate_id), "CANDIDATE_ID_INVALID")
        _need(reader is not None, "TRUSTED_READER_ADAPTER_UNAVAILABLE")
        instant = now if now is not None else datetime.now(timezone.utc)
        _need(instant.utcoffset() is not None, "CLOCK_INVALID")
        with reader.snapshot() as snapshot:
            auth = snapshot.authorization()
            _need(auth.get("actor_context_valid") is True, "AUTHENTICATION_REQUIRED")
            _need(auth.get("teacher_authorized") is True and _uuid(auth.get("school_id")), "TEACHER_ACCESS_REQUIRED")
            _current(auth, instant, "AUTHORIZATION_STALE_OR_REVOKED")
            data = snapshot.lookup(candidate_id=candidate_id, school_id=auth["school_id"])
            _need(data is not None, "CANDIDATE_NOT_AVAILABLE")
            result = _validate(candidate_id, auth["school_id"], data, instant)
            # Re-check current policy before disclosure; adapters must not cache
            # revocation. A long read cannot outlive its authorization lease.
            fresh = snapshot.revalidate(candidate_id=candidate_id, school_id=auth["school_id"])
            end = now if now is not None else datetime.now(timezone.utc)
            current = fresh["authorization"]
            _need(current.get("school_id") == auth["school_id"] and current.get("actor_context_valid") is True
                  and current.get("teacher_authorized") is True, "AUTHORIZATION_CHANGED")
            _current(current, end, "AUTHORIZATION_STALE_OR_REVOKED")
            _need(fresh.get("data") is not None, "CANDIDATE_NOT_AVAILABLE")
            current_result = _validate(candidate_id, auth["school_id"], fresh["data"], end)
            _need(current_result == result, "STAGING_SNAPSHOT_CHANGED")
            completed = now if now is not None else datetime.now(timezone.utc)
            _current(current, completed, "AUTHORIZATION_STALE_OR_REVOKED")
            _current(fresh["data"]["binding"], completed, "READ_BINDING_STALE_OR_REVOKED")
            return result
    except _Blocked as exc:
        code = str(exc)
    except (ReaderUnavailable, OSError):
        code = "TRUSTED_READER_UNAVAILABLE"
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        code = "READ_CONTRACT_INCOMPLETE"
    return {"schema": SCHEMA, "status": "BLOCKED", "gaps": [code], "accepted_canon": [], "canon_write_allowed": False}
