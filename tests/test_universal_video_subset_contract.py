from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from universal_video import batch_plan, subset_contract as contract, video_queue
from universal_video.terminal_evidence_v2 import source_identity_from_claim


SOURCE, OUTPUT, WORK = "sourceFolder000001", "outputFolder000001", "workFolder00000001"
OPTIONS = dict(source_folder_id=SOURCE, output_folder_id=OUTPUT, work_folder_id=WORK)
NOW = "2026-10-12T12:00:00Z"
PROFILE, REVISION = "bridge_3_1_free", "3.1-free-r25.16"
WORKER = "synthetic-worker"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def uuid(number):
    return f"00000000-0000-4000-8000-{number:012d}"


def item(number=1, **changes):
    return {"id": f"privateVideo{number:08d}", "name": f"Lesson {number}.mp4",
            "parents": [SOURCE], "mimeType": "video/mp4", "size": "2000000",
            "version": "17", "modifiedTime": "2026-10-10T10:00:00Z", "trashed": False,
            "md5Checksum": None, **changes}


def build(rows=None, *, sequence=1, bound=2, **changes):
    rows = [item(), item(2), item(3)] if rows is None else rows
    plan = batch_plan.build_batch_plan(rows, **OPTIONS, max_jobs_per_batch=bound)
    return contract.build_subset_contract(plan, **dict(expected_plan_sha256=plan["plan_sha256"],
        subset_sequence=sequence, processing_profile=PROFILE, algorithm_revision=REVISION,
        canary_source_file_id=plan["batches"][sequence - 1]["file_ids"][0], **changes))


def binding(value):
    return {"contract_sha256": value["contract_sha256"], "queue_manifest_sha256": value["queue_manifest_sha256"],
            "request_key": value["enqueue_manifest"]["request_key"], "batch_id": uuid(100),
            "jobs": [{"file_id": file["file_id"], "job_id": uuid(i + 1)} for i, file in enumerate(value["files"])]}


def claim(value, bound, *, canary=True, attempt=1):
    manifest = value["enqueue_manifest"]
    row = next(file for file in manifest["files"] if (file["file_id"] == manifest["canary_source_file_id"]) == canary)
    file_id = row["file_id"]
    return {"job_id": next(job["job_id"] for job in bound["jobs"] if job["file_id"] == file_id),
        "batch_id": bound["batch_id"], "lease_token": uuid(200), "attempt_count": attempt,
        **{key: manifest[key] for key in ("source_folder_id", "output_folder_id", "work_folder_id",
                                          "processing_profile", "algorithm_revision")},
        "source_file_id": file_id, "source_name": row["name"], "source_mime_type": row["mime_type"],
        "source_size_bytes": row["size_bytes"], "source_checksum": row["checksum"],
        "sequence": row["sequence"], "is_canary": canary,
        "stable_job_key": hashlib.sha256(("bridge-video|drive|" + file_id).encode()).hexdigest()[:32]}


def lease(claimed):
    return {**{key: claimed[key] for key in ("job_id", "batch_id", "lease_token", "attempt_count")},
            "lease_owner": WORKER, "status": "LEASED", "observed_at": NOW,
            "lease_expires_at": "2026-10-12T12:05:00Z"}


def ledger(**changes):
    return {"complete": True, "generation": 0, "observed_at": NOW, "admissions": [], **changes}


def admission(value=None, **changes):
    value = build() if value is None else value
    args = dict(expected_contract_sha256=value["contract_sha256"], observed_sources=[item(), item(2)],
                ledger=ledger(), now=NOW)
    return contract.check_admission(value, **{**args, **changes})


def attempt(value=None, **changes):
    value = build() if value is None else value
    bound = binding(value)
    claimed = claim(value, bound)
    args = dict(expected_contract_sha256=value["contract_sha256"], binding=bound, claim=claimed,
                lease=lease(claimed), observed_source=item(), worker_key=WORKER, now=NOW)
    return contract.check_attempt(value, **{**args, **changes})


def terminal(claimed):
    manifest = {"schema_version": "universal-video-artifact-manifest/v1",
                "source_identity": source_identity_from_claim(claimed), "job_id": claimed["stable_job_key"],
                "source_file_id": claimed["source_file_id"], "algorithm_revision": claimed["algorithm_revision"],
                "artifacts": [{"kind": "master_pdf", "sha256": "a" * 64},
                              {"kind": "ai_done", "sha256": "b" * 64}]}
    receipt = {"schema_version": "universal-video-terminal-receipt/v2", "status": "PASS",
               "source_identity_verified": True, "drive_readback_verified": True,
               "result_readback_verified": True, "checksum_verified": True, "artifact_count": 2,
               "artifact_manifest_sha256": digest(manifest)}
    evidence_sha = digest(receipt)
    return {"master_pdf_drive_id": "syntheticMaster001", "master_pdf_sha256": "a" * 64,
            "ai_done_drive_id": "syntheticAiDone001", "ai_done_sha256": "b" * 64,
            "artifact_locators": {"master_pdf": "syntheticMaster001", "ai_done": "syntheticAiDone001"},
            "artifact_manifest": manifest, "artifact_manifest_sha256": digest(manifest),
            "terminal_receipt": {**receipt, "evidence_sha256": evidence_sha}, "terminal_evidence_sha256": evidence_sha,
            "result_mode": "SHADOW_REVIEW_ONLY", "canonical_promotion_allowed": False,
            "database_persistence_allowed": False, "publication_state": "NOT_PUBLISHED",
            **{key: claimed[key] for key in ("source_file_id", "stable_job_key", "algorithm_revision")}}


def review_inputs(value):
    bound = binding(value)
    claimed = claim(value, bound)
    output = terminal(claimed)
    state = {**{key: claimed[key] for key in ("batch_id", "job_id", "lease_token", "attempt_count")},
             "batch_status": "CANARY_REVIEW", "job_status": "REVIEW_READY", "released_jobs": 0,
             "terminal_output_sha256": digest(output), "observed_at": NOW}
    return dict(expected_contract_sha256=value["contract_sha256"], binding=bound, claim=claimed, state=state,
                terminal_output=output, expected_terminal_sha256=digest(output), observed_sources=[item(), item(2)], now=NOW)


@pytest.fixture(autouse=True)
def no_live_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected live action")
    for target in ("requests.sessions.Session.request", "socket.create_connection",
                   "universal_video.drive_adapter.access_token", "psycopg.connect"):
        monkeypatch.setattr(target, forbidden)


def test_contract_reuses_queue_builder_preserves_fingerprints_and_exact_subset():
    rows = [item(name="Lesson 10.mp4"), item(2, name="Lesson 2.mp4"), item(3)]
    with patch.object(video_queue, "build_drive_manifest", wraps=video_queue.build_drive_manifest) as builder:
        value = build(rows)
        assert builder.call_count == 1
        assert {row["id"] for row in builder.call_args.args[1]} == {rows[0]["id"], rows[1]["id"]}
    manifest = value["enqueue_manifest"]
    assert [file["file_id"] for file in manifest["files"]] == [rows[1]["id"], rows[0]["id"]]
    assert value["files"] == batch_plan.build_batch_plan(rows, **OPTIONS, max_jobs_per_batch=2)["files"][:2]
    assert value["subset_sha256"] == digest(value["files"])
    assert value["queue_manifest_sha256"] == digest(manifest)
    assert value["queue_manifest_sha256"] != manifest["inventory_sha256"]
    assert value["files"][0]["duration_ms"] is value["files"][0]["estimated_cost_usd"] is None
    assert all(value[key] is False for key in contract.DISABLED)


def test_deterministic_request_key_and_distinct_subset_with_1001_plan_rows():
    rows = [item(i) for i in range(1001)]
    first = build(rows, bound=1000)
    assert build(rows[::-1], bound=1000) == first
    second = build(rows, sequence=2, bound=1000)
    assert len(first["files"]) == 1000 and len(second["files"]) == 1
    assert first["enqueue_manifest"]["request_key"] != second["enqueue_manifest"]["request_key"]
    assert video_queue.MAX_FILES == batch_plan.MAX_JOBS_PER_BATCH == 1000


@pytest.mark.parametrize("field", ["source_fingerprint", "version", "parent", "checksum", "name"])
def test_tampered_plan_rejects_even_if_embedded_plan_digest_is_recomputed(field):
    plan = batch_plan.build_batch_plan([item()], **OPTIONS)
    plan["files"][0][field] = "private-tamper-marker"
    plan["plan_sha256"] = digest({key: value for key, value in plan.items() if key != "plan_sha256"})
    with pytest.raises(contract.SubsetContractError):
        contract.build_subset_contract(plan, expected_plan_sha256=plan["plan_sha256"], subset_sequence=1,
            processing_profile=PROFILE, algorithm_revision=REVISION, canary_source_file_id=item()["id"])


def test_external_plan_anchor_and_canonical_queue_projection_required():
    plan = batch_plan.build_batch_plan([item()], **OPTIONS)
    with pytest.raises(contract.SubsetContractError, match="PLAN_DIGEST_MISMATCH"):
        contract.build_subset_contract(plan, expected_plan_sha256="0" * 64, subset_sequence=1,
            processing_profile=PROFILE, algorithm_revision=REVISION, canary_source_file_id=item()["id"])
    with pytest.raises(contract.SubsetContractError, match="QUEUE_PROJECTION_MISMATCH"):
        build([item(name=" Lesson 1.mp4 ")])


@pytest.mark.parametrize("sequence", [0, -1, 3, True, "1", None])
def test_invalid_subset_sequence_rejects(sequence):
    plan = batch_plan.build_batch_plan([item()], **OPTIONS)
    with pytest.raises(contract.SubsetContractError):
        contract.build_subset_contract(plan, expected_plan_sha256=plan["plan_sha256"], subset_sequence=sequence,
            processing_profile=PROFILE, algorithm_revision=REVISION, canary_source_file_id=item()["id"])


def test_admission_returns_consistency_only_and_never_mutates_observations():
    value, observed = build(), ledger()
    before = copy.deepcopy((value, observed))
    result = admission(value, ledger=observed)
    assert result == admission(value, ledger=observed)
    assert result["status"] == "CONSISTENT_ONLY" and all(result[key] is False for key in contract.DISABLED)
    assert (value, observed) == before
    assert "privateVideo" not in json.dumps(result)


@pytest.mark.parametrize("change", [dict(version="18"), dict(modifiedTime="2026-10-11T10:00:00Z"),
    dict(md5Checksum="a" * 32), dict(size="3000000"), dict(sha256Checksum="b" * 64)])
def test_revision_change_blocks_admission_claim_retry_and_review(change):
    with pytest.raises(contract.SubsetContractError, match="SOURCE_REVISION_CHANGED"):
        admission(observed_sources=[item(**change), item(2)])
    for phase in ("CLAIM", "RETRY"):
        with pytest.raises(contract.SubsetContractError, match="SOURCE_REVISION_CHANGED"):
            attempt(observed_source=item(**change), phase=phase)
    value = build()
    args = review_inputs(value)
    args["observed_sources"] = [item(), item(2, **change)]
    with pytest.raises(contract.SubsetContractError, match="SOURCE_REVISION_CHANGED"):
        contract.prepare_canary_review(value, **args)


@pytest.mark.parametrize("observed", [[item()], [item(), item()], [item(), item(2), item(3)],
    [item(parents=[OUTPUT]), item(2)], [item(trashed=True), item(2)]])
def test_missing_extra_duplicate_wrong_parent_or_trashed_observations_fail(observed):
    with pytest.raises(contract.SubsetContractError):
        admission(observed_sources=observed)


def test_rename_preserves_identity_but_requires_review_and_cannot_readmit():
    with pytest.raises(contract.SubsetContractError, match="SOURCE_NAME_CHANGED"):
        admission(observed_sources=[item(name="Renamed.mp4"), item(2)])
    original = build()
    history = ledger(admissions=[{"request_key": original["enqueue_manifest"]["request_key"],
        "contract_sha256": original["contract_sha256"], "source_identities": [original["files"][0]["source_identity"]]}])
    for changed in (build(), build([item(version="18"), item(2)]), build([item(name="Renamed.mp4"), item(2)])):
        with pytest.raises(contract.SubsetContractError, match="ALREADY_ADMITTED"):
            admission(changed, ledger=history)


@pytest.mark.parametrize("changes", [dict(complete=False), dict(complete=1), dict(generation=True),
    dict(generation=-1), dict(admissions=None), dict(observed_at="2026-10-12T11:59:29Z"),
    dict(observed_at="2026-10-12T12:00:01Z"), dict(observed_at="2026-10-12T12:00:00")])
def test_unknown_or_stale_ledger_fails(changes):
    with pytest.raises(contract.SubsetContractError):
        admission(ledger=ledger(**changes))


def test_same_request_key_with_other_sources_is_rejected():
    value = build()
    history = ledger(admissions=[{"request_key": value["enqueue_manifest"]["request_key"],
        "contract_sha256": "a" * 64, "source_identities": ["google_drive:otherVideo000001"]}])
    with pytest.raises(contract.SubsetContractError, match="ALREADY_ADMITTED"):
        admission(value, ledger=history)


def test_valid_claim_and_pre_retry_reuse_exact_canary_and_fence():
    assert attempt()["phase"] == "CLAIM"
    assert attempt(phase="RETRY")["phase"] == "RETRY"


@pytest.mark.parametrize("field,value", [("lease_token", uuid(300)), ("job_id", uuid(300)),
    ("batch_id", uuid(300)), ("lease_owner", "other-worker"), ("attempt_count", 2),
    ("status", "QUEUED"), ("lease_expires_at", NOW), ("observed_at", "2026-10-12T11:59:00Z")])
def test_wrong_expired_or_stale_lease_fails_claim_and_retry(field, value):
    built = build()
    observed = lease(claim(built, binding(built)))
    observed[field] = value
    for phase in ("CLAIM", "RETRY"):
        with pytest.raises(contract.SubsetContractError):
            attempt(built, lease=observed, phase=phase)


@pytest.mark.parametrize("field,value", [("source_file_id", item(3)["id"]), ("source_name", "Other.mp4"),
    ("source_size_bytes", 3000000), ("source_checksum", "md5:" + "a" * 32),
    ("output_folder_id", WORK), ("processing_profile", "other_profile"), ("algorithm_revision", "other"),
    ("stable_job_key", "0" * 32), ("sequence", True), ("is_canary", 1)])
def test_queue_claim_must_match_exact_projection(field, value):
    built = build()
    claimed = claim(built, binding(built))
    claimed[field] = value
    with pytest.raises(contract.SubsetContractError):
        attempt(built, claim=claimed)


def test_foreign_binding_duplicate_job_mapping_and_bad_contract_hash_fail():
    built = build()
    for mutate in (lambda b: b.update(contract_sha256="0" * 64),
                   lambda b: b.update(queue_manifest_sha256="0" * 64),
                   lambda b: b["jobs"].pop(), lambda b: b["jobs"].append(b["jobs"][0])):
        bound = binding(built)
        mutate(bound)
        with pytest.raises(contract.SubsetContractError):
            attempt(built, binding=bound)
    with pytest.raises(contract.SubsetContractError, match="CONTRACT_DIGEST_MISMATCH"):
        attempt(built, expected_contract_sha256="0" * 64)
    built["files"][0]["version"] = "18"
    with pytest.raises(contract.SubsetContractError, match="CONTRACT_DIGEST_MISMATCH"):
        admission(built)


def test_retry_limit_and_noncanary_release_fail_closed():
    value = build()
    for attempt_number in (0, 3, 4, True):
        claimed = claim(value, binding(value), attempt=attempt_number)
        with pytest.raises(contract.SubsetContractError):
            attempt(value, claim=claimed, lease=lease(claimed), phase="RETRY")
    claimed = claim(value, binding(value), canary=False)
    with pytest.raises(contract.SubsetContractError, match="RELEASE_NOT_AUTHORIZED"):
        attempt(value, claim=claimed, lease=lease(claimed), observed_source=item(2))


def test_canary_pass_only_prepares_hold_and_binds_exact_remainder_and_audit():
    value = build()
    args = review_inputs(value)
    before = copy.deepcopy(args)
    with patch.object(contract, "validate_terminal_output", wraps=contract.validate_terminal_output) as verifier:
        result = contract.prepare_canary_review(value, **args)
        verifier.assert_called_once_with(args["claim"], args["terminal_output"])
    packet = result["review_packet"]
    assert packet["status"] == "HOLD_FOR_AUTHORIZED_REVIEW"
    assert packet["remaining_file_ids"] == [item(2)["id"]]
    assert packet["audit_required"] is True
    assert result["review_sha256"] == digest(packet)
    assert all(result[key] is packet[key] is False for key in contract.DISABLED)
    assert args == before and contract.prepare_canary_review(value, **args) == result


@pytest.mark.parametrize("field,value", [("batch_status", "RUNNING"), ("job_status", "FAILED"),
    ("released_jobs", 1), ("released_jobs", False), ("attempt_count", 2), ("lease_token", uuid(777)),
    ("batch_id", uuid(777)), ("job_id", uuid(777)), ("terminal_output_sha256", "0" * 64),
    ("observed_at", "2026-10-12T11:00:00Z")])
def test_canary_result_cannot_be_replayed_or_promote_release(field, value):
    built = build()
    args = review_inputs(built)
    args["state"][field] = value
    with pytest.raises(contract.SubsetContractError):
        contract.prepare_canary_review(built, **args)


def test_terminal_outer_anchor_and_existing_inner_hash_validator_both_apply():
    value = build()
    args = review_inputs(value)
    args["terminal_output"]["artifact_manifest"]["job_id"] = "private-tamper"
    with pytest.raises(contract.SubsetContractError, match="TERMINAL_DIGEST_MISMATCH"):
        contract.prepare_canary_review(value, **args)
    args["state"]["terminal_output_sha256"] = args["expected_terminal_sha256"] = digest(args["terminal_output"])
    with pytest.raises(contract.SubsetContractError):
        contract.prepare_canary_review(value, **args)


@pytest.mark.parametrize("payload", [None, [], "terminal", 1, True,
    ["master_pdf_drive_id", "master_pdf_sha256", "ai_done_drive_id", "ai_done_sha256",
     "artifact_locators", "artifact_manifest", "artifact_manifest_sha256",
     "terminal_receipt", "terminal_evidence_sha256"]])
def test_terminal_payload_must_be_object_even_with_matching_outer_anchor(payload):
    value = build()
    args = review_inputs(value)
    args["terminal_output"] = payload
    args["state"]["terminal_output_sha256"] = args["expected_terminal_sha256"] = digest(payload)
    with pytest.raises(contract.SubsetContractError, match="^INVALID_TERMINAL_OUTPUT$"):
        contract.prepare_canary_review(value, **args)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "\ud800"])
def test_nonfinite_and_invalid_unicode_fail_closed(bad):
    value = build()
    value["enqueue_manifest"]["unused"] = bad
    with pytest.raises(contract.SubsetContractError):
        admission(value)


def test_import_build_and_all_checks_make_no_external_or_runtime_calls():
    targets = ["universal_video.drive_adapter.access_token", "universal_video.drive_adapter.file_metadata",
        "universal_video.drive_adapter.list_folder_files", "universal_video.drive_adapter.download_file",
        "universal_video.video_queue.discover_drive_manifest", "universal_video.video_queue.enqueue_manifest",
        "universal_video.video_queue.enqueue_drive_request", "universal_video.video_queue.claim_job",
        "universal_video.video_queue.retry_job", "universal_video.video_queue.finish_job",
        "universal_video.video_queue.heartbeat_job", "universal_video.video_queue.database_url_from_env",
        "universal_video.terminal_evidence_v2.reverify_terminal_output_live",
        "universal_video.terminal_evidence_v2.build_terminal_evidence", "ops.ibm_vpc_power.create_action",
        "universal_video.drive_results.publish_result", "psycopg.connect", "requests.sessions.Session.request",
        "urllib.request.urlopen", "socket.create_connection", "subprocess.Popen", "multiprocessing.process.BaseProcess.start"]
    with ExitStack() as stack:
        spies = [stack.enter_context(patch(target, side_effect=AssertionError(target))) for target in targets]
        spec = importlib.util.spec_from_file_location("universal_video.offline_subset_probe", contract.__file__)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        plan = batch_plan.build_batch_plan([item(), item(2)], **OPTIONS)
        value = module.build_subset_contract(plan, expected_plan_sha256=plan["plan_sha256"], subset_sequence=1,
            processing_profile=PROFILE, algorithm_revision=REVISION, canary_source_file_id=item()["id"])
        module.check_admission(value, expected_contract_sha256=value["contract_sha256"],
                               observed_sources=[item(), item(2)], ledger=ledger(), now=NOW)
        bound = binding(value)
        claimed = claim(value, bound)
        module.check_attempt(value, expected_contract_sha256=value["contract_sha256"], binding=bound,
            claim=claimed, lease=lease(claimed), observed_source=item(), worker_key=WORKER, now=NOW, phase="RETRY")
        assert module.prepare_canary_review(value, **review_inputs(value))["release_allowed"] is False
        for spy in spies:
            spy.assert_not_called()
