"""Pure reader with scoped mock storage. Synthetic success is NOT auth/media e2e."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path

import pytest

from bridge_contracts.video_canon_replay import digest
from bridge_contracts.video_teacher_read import read_teacher_video_candidate

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
CID, SCHOOL, SOURCE, RUN, OTHER = [f"00000000-0000-4000-8000-{i:012d}" for i in range(1, 6)]


def lease():
    return {"status": "active", "revoked": False, "checked_at": NOW, "valid_until": NOW + timedelta(seconds=30)}


def fixture(kind="video_artifact_source_observation"):
    quote = "A synthetic excerpt for review."
    text_hash = hashlib.sha256(quote.encode()).hexdigest()
    claims = {"source_video_file_id": "synthetic-video", "source_media_sha256": "a" * 64,
              "parent_raw_sha256": "b" * 64, "parent_canonical_sha256": "c" * 64}
    if kind == "video_artifact_source_observation":
        payload = {"schema": "video-artifact-source-observation-v1", "stage": "SOURCE_OBSERVATION",
                   **{k: v for k, v in claims.items() if k != "source_media_sha256"},
                   "source_media_sha256_claim": claims["source_media_sha256"],
                   "transcript": {"quote": quote, "text_sha256": text_hash, "start": 1., "end": 3.},
                   "speaker_identity_status": "VERIFIED", "speaker_role": "teacher",
                   "hidden_hands": {"private": "DO_NOT_DISCLOSE"}}
    else:
        payload = {"schema": "video-source-knowledge-draft-v1", "status": "CANDIDATE_RESEARCH",
                   "source_claim": {"drive_file_id": claims["source_video_file_id"], "source_sha256": claims["source_media_sha256"]},
                   "parent_document_claim": {"embedded_raw_sha256": claims["parent_raw_sha256"],
                                             "embedded_canonical_sort_compact_utf8_sha256": claims["parent_canonical_sha256"]},
                   "transcript": {"exact_text": quote, "exact_text_utf8_sha256": text_hash, "start": 1., "end": 3.},
                   "private_metadata": "DO_NOT_DISCLOSE"}
    member = {"candidate_type": kind, "payload": payload, "payload_hash": digest(payload),
              "stable_key": "synthetic:sha256:" + digest(payload), "method_version": payload["schema"],
              "quality_status": "BLOCKED", "promotion_status": "STAGING_ONLY", "authoritative_tables_modified": False}
    receipt = {"schema": "video-canon-staging-replay-v1", "candidates": [member],
               "status": "BLOCKED", "promotion_commands": [], "authoritative_write_performed": False}
    receipt["replay_sha256"] = digest(receipt)
    row = {**deepcopy(member), "analysis_candidate_id": CID, "school_id": SCHOOL, "source_id": SOURCE,
           "analysis_run_id": RUN, "status": "active", "promotion_status": "staging"}
    data = {"candidate": row,
            "source": {"source_id": SOURCE, "school_id": SCHOOL, "status": "active",
                       "video_file_id": claims["source_video_file_id"], "media_sha256": claims["source_media_sha256"]},
            "run": {"analysis_run_id": RUN, "school_id": SCHOOL, "input_source_id": SOURCE,
                    "run_status": "partial_success", "publication_authorization_status": "blocked"},
            "binding": {**lease(), **claims, **{k: row[k] for k in ("analysis_candidate_id", "school_id", "source_id", "analysis_run_id", "payload_hash")},
                        "replay_sha256": receipt["replay_sha256"], "disclosure_status": "TEACHER_EXCERPT_ALLOWED",
                        "display_sha256": digest({"quote": quote, "start": 1., "end": 3.})},
            "receipt": receipt}
    auth = {**lease(), "school_id": SCHOOL, "actor_context_valid": True, "teacher_authorized": True}
    return MockReader(auth, data)


class MockReader:
    """Test double only: filtering precedes materialization, no production DB."""
    def __init__(self, auth, data):
        self.auth, self.data, self.calls, self.materialized = auth, data, [], []
        self.after_auth = None
        self.before_revalidate = None
        self.auth_reads = 0

    @contextmanager
    def snapshot(self):
        yield self

    def authorization(self):
        self.auth_reads += 1
        return deepcopy(self.after_auth if self.after_auth and self.auth_reads % 2 == 0 else self.auth)

    def lookup(self, *, candidate_id, school_id):
        self.calls.append((candidate_id, school_id))
        row = self.data["candidate"]
        if row["analysis_candidate_id"] != candidate_id or row["school_id"] != school_id:
            return None
        self.materialized.append(candidate_id)
        return deepcopy(self.data)

    def revalidate(self, *, candidate_id, school_id):
        if self.before_revalidate:
            self.before_revalidate(self.data)
        auth = self.authorization()
        allowed = (auth.get("actor_context_valid") is True and auth.get("teacher_authorized") is True
                   and auth.get("revoked") is False and auth.get("school_id") == school_id)
        return {"authorization": auth, "data": self.lookup(candidate_id=candidate_id, school_id=school_id) if allowed else None}


def read(reader):
    return read_teacher_video_candidate(CID, reader=reader, now=NOW)


def blocked(result, code=None):
    assert result["status"] == "BLOCKED"
    assert not result["accepted_canon"] and result["canon_write_allowed"] is False
    assert set(result) == {"schema", "status", "gaps", "accepted_canon", "canon_write_allowed"}
    if code:
        assert code in result["gaps"]


@pytest.mark.parametrize("kind,stage", [("video_artifact_source_observation", "SOURCE_OBSERVATION"),
                                         ("video_source_knowledge_draft", "CANDIDATE_RESEARCH")])
def test_scoped_staging_projection_is_repeatable_and_not_authority(kind, stage):
    reader = fixture(kind)
    before = deepcopy(reader.data)
    result = read(reader)
    assert result == read(reader)
    assert reader.data == before
    assert result["status"] == "READABLE_STAGING" and result["stage"] == stage
    assert result["speaker_identity_status"] == result["speaker_role"] == "UNKNOWN"
    assert result["media_review_status"] == "UNVERIFIED"
    assert result["accepted_canon"] == [] and result["canon_write_allowed"] is False
    assert "DO_NOT_DISCLOSE" not in json.dumps(result)
    assert "synthetic-video" not in json.dumps(result)
    assert set(result["excerpt"]) == {"quote", "start", "end"}
    assert reader.calls == [(CID, SCHOOL)] * 4


def test_default_has_no_adapter_and_request_cannot_supply_authority():
    blocked(read_teacher_video_candidate(CID), "TRUSTED_READER_ADAPTER_UNAVAILABLE")
    reader = fixture()
    blocked(read_teacher_video_candidate({"candidate_id": CID, "school_id": SCHOOL, "VERIFIED": True}, reader=reader))
    assert reader.calls == []
    with pytest.raises(TypeError):
        read_teacher_video_candidate(CID, school_id=SCHOOL, teacher_authorized=True)


@pytest.mark.parametrize("field,value,code", [
    ("actor_context_valid", False, "AUTHENTICATION_REQUIRED"),
    ("actor_context_valid", "true", "AUTHENTICATION_REQUIRED"),
    ("teacher_authorized", False, "TEACHER_ACCESS_REQUIRED"),
    ("status", "revoked", "AUTHORIZATION_STALE_OR_REVOKED"),
    ("revoked", True, "AUTHORIZATION_STALE_OR_REVOKED"),
    ("checked_at", NOW - timedelta(seconds=61), "AUTHORIZATION_STALE_OR_REVOKED"),
    ("checked_at", NOW + timedelta(seconds=1), "AUTHORIZATION_STALE_OR_REVOKED"),
    ("valid_until", NOW, "AUTHORIZATION_STALE_OR_REVOKED"),
    ("valid_until", NOW.replace(tzinfo=None), "AUTHORIZATION_STALE_OR_REVOKED"),
])
def test_anonymous_stale_or_revoked_access_never_fetches_candidate(field, value, code):
    reader = fixture()
    reader.auth[field] = value
    blocked(read(reader), code)
    assert reader.calls == reader.materialized == []


def test_cross_school_and_unknown_candidate_are_indistinguishable_without_loading_foreign_row():
    reader = fixture()
    reader.data["candidate"]["school_id"] = OTHER
    cross_school = read(reader)
    blocked(cross_school, "CANDIDATE_NOT_AVAILABLE")
    assert reader.calls == [(CID, SCHOOL)] and reader.materialized == []
    reader.data["candidate"]["school_id"] = SCHOOL
    reader.data["candidate"]["analysis_candidate_id"] = OTHER
    assert read(reader) == cross_school
    assert reader.materialized == []


@pytest.mark.parametrize("section,field,value,code", [
    ("candidate", "status", "superseded", "CANDIDATE_NOT_CURRENT_STAGING"),
    ("candidate", "promotion_status", "promoted", "CANDIDATE_NOT_CURRENT_STAGING"),
    ("candidate", "source_id", None, "SOURCE_RUN_BINDING_MISSING"),
    ("candidate", "analysis_run_id", None, "SOURCE_RUN_BINDING_MISSING"),
    ("source", "school_id", OTHER, "SOURCE_RUN_BINDING_MISMATCH"),
    ("source", "source_id", OTHER, "SOURCE_RUN_BINDING_MISMATCH"),
    ("run", "analysis_run_id", OTHER, "SOURCE_RUN_BINDING_MISMATCH"),
    ("run", "school_id", OTHER, "SOURCE_RUN_BINDING_MISMATCH"),
    ("run", "input_source_id", OTHER, "SOURCE_RUN_BINDING_MISMATCH"),
    ("source", "status", "revoked", "SOURCE_RUN_NOT_CURRENT"),
    ("run", "publication_authorization_status", "revoked", "SOURCE_RUN_NOT_CURRENT"),
    ("run", "run_status", "failed", "SOURCE_RUN_NOT_CURRENT"),
    ("binding", "analysis_candidate_id", OTHER, "READ_BINDING_MISMATCH"),
    ("binding", "school_id", OTHER, "READ_BINDING_MISMATCH"),
    ("binding", "source_id", OTHER, "READ_BINDING_MISMATCH"),
    ("binding", "analysis_run_id", OTHER, "READ_BINDING_MISMATCH"),
    ("binding", "payload_hash", "d" * 64, "READ_BINDING_MISMATCH"),
    ("binding", "replay_sha256", "d" * 64, "REPLAY_RECEIPT_HASH_MISMATCH"),
    ("binding", "source_video_file_id", "wrong", "SOURCE_HASH_BINDING_MISMATCH"),
    ("binding", "parent_raw_sha256", "d" * 64, "SOURCE_HASH_BINDING_MISMATCH"),
    ("binding", "parent_canonical_sha256", "d" * 64, "SOURCE_HASH_BINDING_MISMATCH"),
    ("source", "media_sha256", "d" * 64, "SOURCE_HASH_BINDING_MISMATCH"),
    ("source", "video_file_id", "wrong", "SOURCE_HASH_BINDING_MISMATCH"),
    ("binding", "disclosure_status", "VERIFIED", "DISCLOSURE_BINDING_MISSING"),
    ("binding", "display_sha256", "d" * 64, "DISCLOSURE_BINDING_MISSING"),
    ("binding", "status", "revoked", "READ_BINDING_STALE_OR_REVOKED"),
    ("binding", "checked_at", NOW - timedelta(seconds=61), "READ_BINDING_STALE_OR_REVOKED"),
])
def test_exact_binding_freshness_and_disclosure_negatives(section, field, value, code):
    reader = fixture()
    reader.data[section][field] = value
    blocked(read(reader), code)


@pytest.mark.parametrize("section,code", [("receipt", "REPLAY_RECEIPT_MISSING"),
    ("binding", "TRUSTED_READ_BINDING_MISSING"), ("source", "SOURCE_RUN_BINDING_MISSING"), ("run", "SOURCE_RUN_BINDING_MISSING")])
def test_missing_link_never_falls_back(section, code):
    reader = fixture()
    reader.data[section] = None
    blocked(read(reader), code)


def test_tampering_even_when_attacker_rehashes_artifact_is_not_trusted():
    reader = fixture()
    assert read(reader)["status"] == "READABLE_STAGING"
    row = reader.data["candidate"]
    row["payload"]["transcript"]["quote"] = "tampered"
    blocked(read(reader), "PAYLOAD_HASH_MISMATCH")
    row["payload_hash"] = digest(row["payload"])
    blocked(read(reader), "READ_BINDING_MISMATCH")
    reader = fixture()
    receipt = reader.data["receipt"]
    receipt["status"] = "forged"
    blocked(read(reader), "REPLAY_RECEIPT_HASH_MISMATCH")
    receipt["replay_sha256"] = digest({k: v for k, v in receipt.items() if k != "replay_sha256"})
    blocked(read(reader), "REPLAY_RECEIPT_HASH_MISMATCH")


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "wrong_type", "wrong_method", "wrong_stable_key"])
def test_receipt_must_include_exact_unique_candidate(mutation):
    reader = fixture()
    receipt = reader.data["receipt"]
    member = receipt["candidates"][0]
    if mutation == "missing": receipt["candidates"] = []
    elif mutation == "duplicate": receipt["candidates"].append(deepcopy(member))
    else: member[{"wrong_type": "candidate_type", "wrong_method": "method_version", "wrong_stable_key": "stable_key"}[mutation]] = "wrong"
    receipt["replay_sha256"] = digest({k: v for k, v in receipt.items() if k != "replay_sha256"})
    reader.data["binding"]["replay_sha256"] = receipt["replay_sha256"]
    blocked(read(reader), "REPLAY_CANDIDATE_BINDING_MISMATCH")


def test_revocation_between_check_and_disclosure_and_subsequent_reads():
    reader = fixture()
    reader.after_auth = {**reader.auth, "revoked": True}
    blocked(read(reader), "AUTHORIZATION_STALE_OR_REVOKED")
    reader.after_auth = None
    assert read(reader)["status"] == "READABLE_STAGING"
    reader.auth["revoked"] = True
    blocked(read(reader), "AUTHORIZATION_STALE_OR_REVOKED")


@pytest.mark.parametrize("section,field,value,code", [
    ("binding", "revoked", True, "READ_BINDING_STALE_OR_REVOKED"),
    ("binding", "disclosure_status", "revoked", "DISCLOSURE_BINDING_MISSING"),
    ("candidate", "status", "superseded", "CANDIDATE_NOT_CURRENT_STAGING"),
    ("source", "status", "revoked", "SOURCE_RUN_NOT_CURRENT"),
    ("run", "publication_authorization_status", "revoked", "SOURCE_RUN_NOT_CURRENT"),
])
def test_final_revalidation_rechecks_all_current_disclosure_state(section, field, value, code):
    reader = fixture()
    reader.before_revalidate = lambda data: data[section].update({field: value})
    blocked(read(reader), code)


def test_lease_cannot_expire_during_final_revalidation(monkeypatch):
    import bridge_contracts.video_teacher_read as module
    class Clock(datetime):
        instant = NOW
        @classmethod
        def now(cls, tz=None):
            return cls.instant
    reader = fixture()
    reader.before_revalidate = lambda _: setattr(Clock, "instant", NOW + timedelta(seconds=31))
    monkeypatch.setattr(module, "datetime", Clock)
    # _current checks datetime types, so use clock subclass for stored times too.
    for record in (reader.auth, reader.data["binding"]):
        for key in ("checked_at", "valid_until"):
            record[key] = Clock.fromtimestamp(record[key].timestamp(), timezone.utc)
    blocked(module.read_teacher_video_candidate(CID, reader=reader), "AUTHORIZATION_STALE_OR_REVOKED")


@pytest.mark.parametrize("value", [None, [], {"candidate": None}, {"candidate": {"school_id": SCHOOL}}, {"candidate": "PRIVATE"}])
def test_incomplete_contract_never_discloses_or_crashes(value):
    reader = fixture()
    reader.lookup = lambda **kwargs: value
    blocked(read(reader))


def test_real_artifact_cannot_create_missing_auth_or_db_receipt_binding(tmp_path):
    root = os.environ.get("BRIDGE_VIDEO_REAL_FIXTURE_DIR")
    if not root:
        pytest.skip("private fixture unavailable; synthetic tests are not real e2e")
    from bridge_contracts.video_local_evidence import LocalEvidenceReader
    from bridge_contracts.video_replay_input import build_replay_input
    from tests.test_video_local_evidence import write_index
    root = Path(root)
    raw = (root / "inputs/master_analysis.raw.json").read_bytes()
    parent = json.loads(raw)
    manifest = json.loads((root / "MANIFEST.json").read_bytes())
    expected = next(item for item in manifest['files'] if item['path'] == 'inputs/master_analysis.raw.json')
    assert len(raw) == expected['bytes'] and hashlib.sha256(raw).hexdigest() == expected['sha256']
    selection = json.loads((root / "inputs/selection.json").read_bytes())
    (tmp_path / "parent.json").write_bytes(raw)
    frames = []
    for path in (root / "inputs").glob("frame_*.jpg"):
        content = path.read_bytes()
        (tmp_path / path.name).write_bytes(content)
        frames.append({"path": path.name, "evidence_id": path.stem, "sha256": hashlib.sha256(content).hexdigest()})
    path, pin = write_index(tmp_path, {"schema": "video-local-evidence-index-v1",
        "parent": {"path": "parent.json", "raw_sha256": hashlib.sha256(raw).hexdigest(), "canonical_sha256": digest(parent)},
        "source": {"path": None, "video_file_id": parent["source"]["driveId"], "sha256": parent["source"]["sha256"]}, "frames": frames})
    receipt = LocalEvidenceReader(path, pin).replay(build_replay_input(raw, selection))
    assert receipt["status"] == "BLOCKED"
    member = next(m for m in receipt["candidates"] if m["candidate_type"] == "video_artifact_source_observation")
    # Synthetic IDs here deliberately do not claim real DB or authorization proof.
    reader = fixture()
    reader.data["candidate"].update(deepcopy(member), promotion_status="staging", source_id=None, analysis_run_id=None)
    reader.data["receipt"] = receipt
    reader.data["binding"] = None
    blocked(read(reader), "SOURCE_RUN_BINDING_MISSING")
    reader.data["candidate"].update(source_id=SOURCE, analysis_run_id=RUN)
    blocked(read(reader), "TRUSTED_READ_BINDING_MISSING")
    blocked(read_teacher_video_candidate(CID), "TRUSTED_READER_ADAPTER_UNAVAILABLE")
