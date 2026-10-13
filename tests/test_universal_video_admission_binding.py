"""Offline checks only: these tests do not prove PostgreSQL atomicity."""
from copy import deepcopy
from contextlib import ExitStack
import hashlib
from unittest.mock import patch

import pytest

from universal_video import admission_binding as admission
from universal_video import batch_plan, subset_contract, video_queue


SOURCE, OUTPUT, WORK = "syntheticSource001", "syntheticOutput001", "syntheticWork00001"
NOW = "2026-10-12T12:00:10Z"


def uid(number):
    return f"00000000-0000-4000-8000-{number:012d}"


def raw(number=1, **changes):
    return dict(id=f"syntheticVideo{number:04d}", name=f"Lesson {number}.mp4",
                parents=[SOURCE], mimeType="video/mp4", size="2000000", trashed=False,
                version="17", modifiedTime="2026-10-10T10:00:00Z", md5Checksum=None, **changes)


@pytest.fixture
def rows():
    plan = batch_plan.build_batch_plan([raw(), raw(2)], source_folder_id=SOURCE,
        output_folder_id=OUTPUT, work_folder_id=WORK, max_jobs_per_batch=2)
    value = subset_contract.build_subset_contract(plan, expected_plan_sha256=plan["plan_sha256"],
        subset_sequence=1, processing_profile="bridge_3_1_free", algorithm_revision="3.1-free-r25.16",
        canary_source_file_id=raw()["id"])
    manifest = value["enqueue_manifest"]
    batch = {key: manifest[key] for key in admission.BATCH_FIELDS}
    batch.update(batch_id=uid(10), source_kind="google_drive_folder", status="QUEUED_CANARY")
    jobs = [dict(job_id=uid(i), batch_id=uid(10), source_file_id=item["file_id"],
                 sequence=item["sequence"], source_name=item["name"], source_mime_type=item["mime_type"],
                 source_size_bytes=item["size_bytes"], source_checksum=item["checksum"],
                 stable_job_key=hashlib.sha256(("bridge-video|drive|" + item["file_id"]).encode()).hexdigest()[:32],
                 is_canary=item["file_id"] == manifest["canary_source_file_id"], status="QUEUED")
            for i, item in enumerate(manifest["files"], 1)]
    return dict(contract=value, expected_contract_sha256=value["contract_sha256"],
                approval_id=uid(20), batch_row=batch, job_rows=jobs)


def observe(rows, *, attempt=None):
    value = rows["contract"]
    scope = dict(phase="ADMISSION", request_key=value["enqueue_manifest"]["request_key"], nonce=uid(30))
    sources = [raw(), raw(2)]
    if attempt is not None:
        scope = dict(phase="ATTEMPT", request_key=value["enqueue_manifest"]["request_key"], **attempt)
        sources = [raw()]
    return dict(observation_id=uid(40), contract_sha256=value["contract_sha256"],
                observed_at="2026-10-12T12:00:00Z", expires_at="2026-10-12T12:00:30Z",
                scope=scope, sources=sources)


def check(rows, observation, **changes):
    args = dict(expected_contract_sha256=rows["expected_contract_sha256"], observation=observation,
                now=NOW, admission_nonce=uid(30))
    return admission.check_source_observation(rows["contract"], **{**args, **changes})


def resign(value):
    value["contract_sha256"] = batch_plan._digest({k: v for k, v in value.items() if k != "contract_sha256"})


def test_mapping_uses_actual_ids_deterministic_order_and_preserves_nulls(rows):
    bound = admission.build_binding(**rows)
    assert bound == admission.build_binding(**{**rows, "job_rows": list(reversed(rows["job_rows"]))})
    assert [j["job_id"] for j in bound["jobs"]] == [uid(1), uid(2)]
    assert bound["batch_id"] == uid(10)
    assert rows["contract"]["files"][0]["checksum"] is None
    assert rows["contract"]["files"][0]["duration_ms"] is None
    assert rows["contract"]["files"][0]["estimated_cost_usd"] is None
    assert all(bound[key] is False for key in admission.DISABLED)


@pytest.mark.parametrize("status", ["QUEUED_CANARY", "RUNNING", "CANARY_REVIEW", "CANARY_BLOCKED", "REVIEW"])
def test_lost_response_recovery_ignores_progress_and_does_not_revalidate_live_sources(rows, status):
    stored = admission.build_binding(**rows)
    rows["batch_row"].update(status=status, updated_at="later", completed_at="later")
    rows["job_rows"][0].update(status="REVIEW_READY", lease_token=None, attempt_count=3, output={"changed": True})
    with patch.object(subset_contract, "_sources", side_effect=AssertionError("recovery must not reread source")):
        receipt = admission.recover_binding(**rows, persisted_binding=stored)
    assert receipt["binding"] == stored
    assert all(receipt[key] is False for key in admission.DISABLED)


@pytest.mark.parametrize("field,value", [
    ("request_key", "other-request"), ("source_folder_id", "otherSource0001"),
    ("output_folder_id", "otherOutput0001"), ("work_folder_id", "otherWork000001"),
    ("processing_profile", "other_profile"), ("algorithm_revision", "other-revision"),
    ("canary_source_file_id", "syntheticVideo0002"), ("inventory_sha256", "f" * 64),
    ("expected_count", 1), ("total_size_bytes", 4000001), ("source_kind", "local"),
    ("result_mode", "LIVE"), ("canonical_promotion_allowed", 0),
    ("database_persistence_allowed", True),
])
def test_batch_immutable_mismatch_fails(rows, field, value):
    rows["batch_row"][field] = value
    with pytest.raises(admission.AdmissionBindingError, match="BATCH_BINDING_MISMATCH"):
        admission.build_binding(**rows)


@pytest.mark.parametrize("field,value", [
    ("batch_id", uid(11)), ("sequence", 2), ("source_name", "renamed.mp4"),
    ("source_mime_type", "video/avi"), ("source_size_bytes", 2000001),
    ("source_checksum", "md5:" + "a" * 32), ("stable_job_key", "0" * 32), ("is_canary", 1),
])
def test_job_immutable_mismatch_fails(rows, field, value):
    rows["job_rows"][0][field] = value
    with pytest.raises(admission.AdmissionBindingError, match="JOB_BINDING_MISMATCH"):
        admission.build_binding(**rows)


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate_id", "duplicate_source", "foreign_source"])
def test_actual_mapping_must_be_bijective(rows, mutation):
    jobs = rows["job_rows"]
    if mutation == "missing":
        jobs.pop()
    elif mutation == "extra":
        jobs.append(deepcopy(jobs[0]))
    elif mutation == "duplicate_id":
        jobs[1]["job_id"] = jobs[0]["job_id"]
    elif mutation == "duplicate_source":
        jobs[1]["source_file_id"] = jobs[0]["source_file_id"]
    else:
        jobs[1]["source_file_id"] = "otherSource0001"
    with pytest.raises(admission.AdmissionBindingError, match="JOB_SET_MISMATCH"):
        admission.build_binding(**rows)


@pytest.mark.parametrize("field", ["approval_id", "batch_id", "contract_sha256", "queue_manifest_sha256", "binding_sha256", "jobs"])
def test_recovery_never_adopts_a_different_binding(rows, field):
    bound = admission.build_binding(**rows)
    bound[field] = [] if field == "jobs" else "tampered"
    with pytest.raises(admission.AdmissionBindingError, match="IMMUTABLE_BINDING_MISMATCH_HOLD"):
        admission.recover_binding(**rows, persisted_binding=bound)


def test_recovery_requires_persisted_binding_and_actual_uuid_matches(rows):
    with pytest.raises(admission.AdmissionBindingError, match="BINDING_MISSING_HOLD"):
        admission.recover_binding(**rows, persisted_binding=None)
    bound = admission.build_binding(**rows)
    rows["job_rows"][0]["job_id"] = uid(50)
    with pytest.raises(admission.AdmissionBindingError, match="IMMUTABLE_BINDING_MISMATCH_HOLD"):
        admission.recover_binding(**rows, persisted_binding=bound)


@pytest.mark.parametrize("mutation", ["count", "sequence", "request", "fingerprint", "subset_sequence"])
def test_even_rehashed_contract_must_have_exact_queue_projection(rows, mutation):
    value = rows["contract"]
    if mutation == "count":
        value["enqueue_manifest"]["expected_count"] = 1
    elif mutation == "sequence":
        value["enqueue_manifest"]["files"][0]["sequence"] = 2
    elif mutation == "request":
        value["enqueue_manifest"]["request_key"] = "spoofed-request"
    elif mutation == "fingerprint":
        value["files"][0]["source_fingerprint"] = "0" * 64
    else:
        value["subset_sequence"] = True
    value["subset_sha256"] = batch_plan._digest(value["files"])
    value["queue_manifest_sha256"] = batch_plan._digest(value["enqueue_manifest"])
    resign(value)
    rows["expected_contract_sha256"] = value["contract_sha256"]
    with pytest.raises(admission.AdmissionBindingError):
        admission.build_binding(**rows)


def test_fresh_admission_observation_is_still_disabled(rows):
    receipt = check(rows, observe(rows))
    assert receipt["status"] == "CONSISTENT_ONLY"
    assert all(receipt[key] is False for key in admission.DISABLED)


@pytest.mark.parametrize("field,value", [
    ("version", "18"), ("modifiedTime", "2026-10-11T10:00:00Z"),
    ("sha1Checksum", "a" * 40), ("md5Checksum", "a" * 32),
    ("parents", ["differentParent01"]), ("trashed", True), ("name", "renamed.mp4"),
])
def test_same_size_null_checksum_source_changes_block_first_admission(rows, field, value):
    obs = observe(rows)
    obs["sources"][0][field] = value
    with pytest.raises(admission.AdmissionBindingError):
        check(rows, obs)


@pytest.mark.parametrize("now", ["2026-10-11T23:59:59Z", "2026-10-12T12:00:30Z", "2026-10-12T12:00:31Z"])
def test_source_observation_expiry_is_checked_after_lock_wait(rows, now):
    with pytest.raises(admission.AdmissionBindingError, match="SOURCE_OBSERVATION_EXPIRED"):
        check(rows, observe(rows), now=now)


@pytest.mark.parametrize("field,value", [("nonce", uid(99)), ("phase", "ATTEMPT"), ("request_key", "other")])
def test_observation_scope_cannot_be_replayed(rows, field, value):
    obs = observe(rows)
    obs["scope"][field] = value
    with pytest.raises(admission.AdmissionBindingError, match="OBSERVATION_SCOPE_MISMATCH"):
        check(rows, obs)


@pytest.mark.parametrize("number", [1, 2, 3])
def test_each_attempt_requires_its_own_token_count_and_approved_revision(rows, number):
    attempt = dict(job_id=uid(1), source_file_id=raw()["id"], lease_token=uid(60 + number), attempt_count=number)
    obs = observe(rows, attempt=attempt)
    assert check(rows, obs, admission_nonce=None, expected_attempt=attempt)["phase"] == "ATTEMPT"
    obs["sources"][0]["version"] = "18"
    with pytest.raises(admission.AdmissionBindingError):
        check(rows, obs, admission_nonce=None, expected_attempt=attempt)


@pytest.mark.parametrize("field,value", [("lease_token", None), ("attempt_count", 4), ("attempt_count", True)])
def test_invalid_attempt_fails_closed(rows, field, value):
    attempt = dict(job_id=uid(1), source_file_id=raw()["id"], lease_token=uid(61), attempt_count=1)
    attempt[field] = value
    with pytest.raises(admission.AdmissionBindingError):
        check(rows, observe(rows, attempt=attempt), admission_nonce=None, expected_attempt=attempt)


def test_old_lease_observation_cannot_follow_reclaim(rows):
    attempt = dict(job_id=uid(1), source_file_id=raw()["id"], lease_token=uid(61), attempt_count=1)
    obs = observe(rows, attempt=attempt)
    attempt.update(lease_token=uid(62), attempt_count=2)
    with pytest.raises(admission.AdmissionBindingError, match="OBSERVATION_SCOPE_MISMATCH"):
        check(rows, obs, admission_nonce=None, expected_attempt=attempt)


@pytest.mark.parametrize("attempt_number", [1, 2, 3])
def test_projection_composes_with_existing_strict_lease_check(rows, attempt_number):
    bound = admission.build_binding(**rows)
    packet = admission.project_lease_binding(**rows, persisted_binding=bound)
    assert all(packet[key] is False for key in admission.DISABLED)
    assert set(packet["binding"]) == {
        "contract_sha256", "queue_manifest_sha256", "request_key", "batch_id", "jobs"}
    job = rows["job_rows"][0]
    claim = {key: value for key, value in job.items() if key != "status"}
    claim.update({key: rows["batch_row"][key] for key in (
        "source_folder_id", "output_folder_id", "work_folder_id", "processing_profile", "algorithm_revision")})
    claim.update(lease_token=uid(60 + attempt_number), attempt_count=attempt_number)
    lease = {key: claim[key] for key in ("job_id", "batch_id", "lease_token", "attempt_count")}
    lease.update(lease_owner="synthetic-worker", status="LEASED", observed_at=NOW,
                 lease_expires_at="2026-10-12T12:05:00Z")
    args = dict(expected_contract_sha256=rows["expected_contract_sha256"], binding=packet["binding"],
                claim=claim, lease=lease, observed_source=raw(), worker_key="synthetic-worker", now=NOW)
    assert subset_contract.check_attempt(rows["contract"], **args)["execution_allowed"] is False
    lease["lease_expires_at"] = NOW
    with pytest.raises(subset_contract.SubsetContractError, match="LEASE_MISMATCH"):
        subset_contract.check_attempt(rows["contract"], **args)
    bound["jobs"][0]["job_id"] = uid(99)
    with pytest.raises(admission.AdmissionBindingError, match="IMMUTABLE_BINDING_MISMATCH_HOLD"):
        admission.project_lease_binding(**rows, persisted_binding=bound)


def test_all_helpers_have_no_external_effects(rows):
    targets = ["socket.socket", "socket.create_connection", "subprocess.Popen", "psycopg.connect",
               "requests.sessions.Session.request", "universal_video.drive_adapter.access_token",
               "universal_video.drive_adapter.download_file", "universal_video.drive_adapter.file_metadata",
               "universal_video.drive_results._upload_or_verify_file", "universal_video.video_queue.enqueue_manifest",
               "universal_video.video_queue.claim_job", "universal_video.video_queue.retry_job",
               "universal_video.video_queue.finish_job", "ops.ibm_vpc_power.create_action",
               "universal_video.video_queue.database_url_from_env"]
    with ExitStack() as stack:
        stack.enter_context(patch("platform.system", return_value="Synthetic"))
        stack.enter_context(patch("platform.machine", return_value="Synthetic"))
        spies = [stack.enter_context(patch(target, side_effect=AssertionError("external effect"))) for target in targets]
        bound = admission.build_binding(**rows)
        admission.recover_binding(**rows, persisted_binding=bound)
        admission.project_lease_binding(**rows, persisted_binding=bound)
        check(rows, observe(rows))
        for spy in spies:
            spy.assert_not_called()
