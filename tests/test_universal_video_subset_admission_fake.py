"""Test-only transaction model; NOT a second production queue or DB proof.

Autospecced existing enqueue/status/claim/retry/finish interfaces never run their
real implementations. The lock and rollback model a REQUIRED future server-side
transaction around identity reservation + existing enqueue SQL + binding. The
current Python enqueue opens its own connection and cannot provide that boundary.
No fake/adapter/executor is installed under universal_video by these tests.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from copy import deepcopy
from threading import Barrier, RLock
from unittest.mock import create_autospec, patch

import pytest

from universal_video import capture_plan, subset_contract as contract, video_queue
from test_universal_video_capture_plan import fixture as capture_fixture
from test_universal_video_subset_contract import (
    NOW, OPTIONS, PROFILE, REVISION, WORKER, binding, build, claim, digest, item,
    lease, terminal, uuid,
)


class ModelConflict(ValueError):
    pass


class FakeExistingQueue:
    """Only enqueue and status receipts needed to test the admission boundary."""

    def __init__(self):
        self.records = {}
        self.enqueue_manifest = create_autospec(video_queue.enqueue_manifest, side_effect=self._enqueue)
        self.batch_status = create_autospec(video_queue.batch_status, side_effect=self._status)

    def _enqueue(self, manifest, database_url):
        assert database_url == "FAKE_ONLY"
        key = manifest["request_key"]
        if key in self.records:
            if self.records[key]["manifest"] != manifest:
                raise ModelConflict("REQUEST_CONFLICT")
            return deepcopy(self.records[key]["receipt"])
        receipt = {"schema": "universal-video-batch-intake-v1", "batch_id": uuid(100 + len(self.records)),
            **{k: manifest[k] for k in ("request_key", "expected_count", "total_size_bytes",
                                       "inventory_sha256", "canary_source_file_id", "result_mode",
                                       "canonical_promotion_allowed", "database_persistence_allowed")},
            "status": "QUEUED_CANARY", "enqueue_elapsed_seconds": 0.0}
        self.records[key] = {"manifest": deepcopy(manifest), "receipt": receipt}
        return deepcopy(receipt)

    def _status(self, request_key, database_url=None):
        assert database_url == "FAKE_ONLY"
        row = self.records.get(request_key)
        if row is None:
            return None
        # Only the existing batch_status fields used for reconciliation. It does
        # NOT expose contract digests or per-job bindings; those are missing today.
        return {**{k: row["receipt"][k] for k in ("batch_id", "request_key", "expected_count",
                                                "total_size_bytes", "inventory_sha256", "status")},
                **{k: row["manifest"][k] for k in ("processing_profile", "algorithm_revision")}}


class AtomicAdmissionModel:
    """A test specification of one future server transaction, not an API proposal."""

    def __init__(self, queue=None):
        self.queue = queue or FakeExistingQueue()
        self.lock = RLock()
        self.bindings, self.reservations = {}, {}

    def admit_or_recover(self, value, sources, *, expected, fault=None):
        # Anchors/metadata are synthetic trusted inputs in this model only.
        manifest = contract._contract(value, expected)
        contract._sources(value, sources, [f["file_id"] for f in value["files"]])
        key = manifest["request_key"]
        with self.lock:
            status = self.queue.batch_status(key, "FAKE_ONLY")
            saved = self.bindings.get(key)
            if status is not None or saved is not None:
                # Unknown/partial commit is HOLD, never blind re-enqueue.
                if status is None or saved is None:
                    raise ModelConflict("BINDING_UNKNOWN")
                if (saved["contract_sha256"] != expected
                        or saved["binding_sha256"] != digest(saved["binding"])
                        or saved["binding"]["queue_manifest_sha256"] != value["queue_manifest_sha256"]
                        or status != saved["status"]):
                    raise ModelConflict("RECOVERY_CONFLICT")
                return "RECOVERED", deepcopy(saved["binding"])
            ledger = {"complete": True, "generation": len(self.bindings), "observed_at": NOW,
                      "admissions": [{"request_key": k, "contract_sha256": b["contract_sha256"],
                          "source_identities": [s for s, owner in self.reservations.items() if owner == k]}
                          for k, b in self.bindings.items()]}
            contract.check_admission(value, expected_contract_sha256=expected,
                                     observed_sources=sources, ledger=ledger, now=NOW)
            before = deepcopy((self.queue.records, self.bindings, self.reservations))
            try:
                if fault == "before_enqueue":
                    raise RuntimeError("INJECTED")
                receipt = self.queue.enqueue_manifest(manifest, "FAKE_ONLY")
                if fault == "after_enqueue":
                    raise RuntimeError("INJECTED")
                bound = binding(value)
                bound["batch_id"] = receipt["batch_id"]
                bound["jobs"] = [{"file_id": f["file_id"], "job_id": uuid(1000 * len(self.queue.records) + i)}
                                 for i, f in enumerate(value["files"])]
                for f in value["files"]:
                    self.reservations[f["source_identity"]] = key
                self.bindings[key] = {"contract_sha256": expected, "binding": bound,
                    "binding_sha256": digest(bound), "status": self.queue.batch_status(key, "FAKE_ONLY")}
                if fault == "after_binding":
                    raise RuntimeError("INJECTED")
            except Exception:
                self.queue.records, self.bindings, self.reservations = before
                raise
            # Commit completed. A response failure must not roll it back or cause
            # another enqueue on retry. This is distinct from repeated admission.
            if fault == "lost_response":
                raise TimeoutError("RESPONSE_LOST_AFTER_COMMIT")
            return "ADMITTED", deepcopy(bound)

    def submit(self, value=None, sources=None, **options):
        value = build() if value is None else value
        return self.admit_or_recover(value, [item(), item(2)] if sources is None else sources,
                                     expected=value["contract_sha256"], **options)


@pytest.fixture(autouse=True)
def forbid_real_io():
    targets = ["psycopg.connect", "socket.create_connection", "requests.sessions.Session.request",
        "urllib.request.urlopen", "subprocess.Popen", "universal_video.drive_adapter.access_token",
        "universal_video.drive_adapter.file_metadata", "universal_video.drive_adapter.download_file",
        "universal_video.drive_adapter.list_folder_files", "universal_video.drive_results.publish_result",
        "ops.ibm_vpc_power.create_action", "universal_video.video_queue.database_url_from_env",
        "universal_video.video_queue.discover_drive_manifest", "universal_video.video_queue.enqueue_drive_request"]
    with ExitStack() as stack:
        # IBM's module-level user agent asks platform.system() on import; keep
        # even that Windows subprocess probe out of this entirely synthetic test.
        stack.enter_context(patch("platform.system", return_value="Synthetic"))
        stack.enter_context(patch("platform.machine", return_value="Synthetic"))
        spies = [stack.enter_context(patch(t, side_effect=AssertionError(t))) for t in targets]
        # Autospec captures real facade signatures, but never enters the facades.
        yield
        for spy in spies:
            spy.assert_not_called()


def test_capture_selection_plan_subset_atomic_admission_chain():
    rows = [item(), item(2), item(3)]
    data = capture_fixture(rows, selected=[rows[0]["id"], rows[1]["id"]],
        excluded=[{"file_id": rows[2]["id"], "reason": "DEFERRED"}])
    captured = capture_plan.build_capture_plan(*data, **OPTIONS, max_jobs_per_batch=2)
    plan = captured["plan"]
    value = contract.build_subset_contract(plan, expected_plan_sha256=plan["plan_sha256"],
        subset_sequence=1, processing_profile=PROFILE, algorithm_revision=REVISION,
        canary_source_file_id=rows[0]["id"])
    model = AtomicAdmissionModel()
    result, bound = model.submit(value, rows[:2])
    assert result == "ADMITTED" and len(bound["jobs"]) == 2
    assert value["files"] == plan["files"]
    assert all(value[k] is False for k in contract.DISABLED)
    model.queue.enqueue_manifest.assert_called_once_with(value["enqueue_manifest"], "FAKE_ONLY")
    assert value["files"][0]["duration_ms"] is value["files"][0]["estimated_cost_usd"] is None


@pytest.mark.parametrize("fault", ["before_enqueue", "after_enqueue", "after_binding"])
def test_failure_before_commit_rolls_back_queue_binding_and_reservations(fault):
    model = AtomicAdmissionModel()
    with pytest.raises(RuntimeError, match="INJECTED"):
        model.submit(fault=fault)
    assert model.queue.records == model.bindings == model.reservations == {}
    assert model.submit()[0] == "ADMITTED"
    assert len(model.queue.records) == len(model.bindings) == 1 and len(model.reservations) == 2


def test_lost_response_recovers_exact_binding_without_another_enqueue():
    model = AtomicAdmissionModel()
    with pytest.raises(TimeoutError, match="RESPONSE_LOST"):
        model.submit(fault="lost_response")
    assert len(model.queue.records) == len(model.bindings) == 1
    result, recovered = model.submit()
    assert result == "RECOVERED"
    assert model.submit() == (result, recovered)
    assert model.queue.enqueue_manifest.call_count == 1
    assert len(model.reservations) == 2
    # Caller mutations cannot change the committed binding.
    recovered["jobs"].clear()
    assert len(model.submit()[1]["jobs"]) == 2


def test_two_simultaneous_identical_requests_enqueue_once():
    model, barrier = AtomicAdmissionModel(), Barrier(2)
    def submit():
        barrier.wait(timeout=5)
        return model.submit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert sorted(r[0] for r in results) == ["ADMITTED", "RECOVERED"]
    assert results[0][1] == results[1][1]
    assert model.queue.enqueue_manifest.call_count == len(model.bindings) == 1


def test_concurrent_different_keys_with_overlapping_sources_have_one_winner():
    model, barrier = AtomicAdmissionModel(), Barrier(2)
    values = [build(), build([item(), item(3)])]
    sources = [[item(), item(2)], [item(), item(3)]]
    def submit(i):
        barrier.wait(timeout=5)
        try:
            return model.submit(values[i], sources[i])[0]
        except contract.SubsetContractError as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert sorted(results) == ["ADMITTED", "ALREADY_ADMITTED"]
    assert model.queue.enqueue_manifest.call_count == len(model.bindings) == 1
    assert len(model.reservations) == 2


def test_distinct_subsets_can_admit_independently_under_existing_cap():
    model = AtomicAdmissionModel()
    first, second = build(), build(sequence=2)
    assert model.submit(first)[0] == model.submit(second, [item(3)])[0] == "ADMITTED"
    assert len(model.bindings) == len(model.queue.records) == 2 and len(model.reservations) == 3
    assert {r["receipt"]["expected_count"] for r in model.queue.records.values()} == {1, 2}


@pytest.mark.parametrize("change", [{"version": "18"}, {"name": "Renamed.mp4"}, {"md5Checksum": "a" * 32}])
def test_source_revision_or_rename_cannot_recover_or_readmit_under_new_key(change):
    model = AtomicAdmissionModel()
    original = build()
    model.submit(original)
    observed = [item(**change), item(2)]
    with pytest.raises(contract.SubsetContractError, match="SOURCE_REVISION_CHANGED|SOURCE_NAME_CHANGED"):
        model.submit(original, observed)
    changed = build(observed)
    with pytest.raises(contract.SubsetContractError, match="ALREADY_ADMITTED"):
        model.submit(changed, observed)
    assert model.queue.enqueue_manifest.call_count == 1


@pytest.mark.parametrize("damage", ["missing_binding", "missing_queue", "hash", "job", "status"])
def test_unknown_or_conflicting_recovery_holds_without_reenqueue(damage):
    model = AtomicAdmissionModel()
    value = build()
    model.submit(value)
    key = value["enqueue_manifest"]["request_key"]
    if damage == "missing_binding": model.bindings.clear()
    elif damage == "missing_queue": model.queue.records.clear()
    elif damage == "hash": model.bindings[key]["contract_sha256"] = "0" * 64
    elif damage == "job": model.bindings[key]["binding"]["jobs"][0]["job_id"] = uuid(999)
    else: model.queue.records[key]["receipt"]["inventory_sha256"] = "0" * 64
    with pytest.raises(ModelConflict, match="BINDING_UNKNOWN|RECOVERY_CONFLICT"):
        model.submit(value)
    assert model.queue.enqueue_manifest.call_count == 1


def test_wrong_approval_anchor_never_calls_queue():
    model = AtomicAdmissionModel()
    with pytest.raises(contract.SubsetContractError, match="CONTRACT_DIGEST_MISMATCH"):
        model.admit_or_recover(build(), [item(), item(2)], expected="0" * 64)
    model.queue.enqueue_manifest.assert_not_called()
    model.queue.batch_status.assert_not_called()


def test_legacy_enqueue_key_idempotency_is_not_cross_request_source_uniqueness():
    queue = FakeExistingQueue()
    manifest = build()["enqueue_manifest"]
    first = queue.enqueue_manifest(manifest, "FAKE_ONLY")
    assert queue.enqueue_manifest(manifest, "FAKE_ONLY") == first
    conflicting = {**manifest, "algorithm_revision": "different"}
    with pytest.raises(ModelConflict, match="REQUEST_CONFLICT"):
        queue.enqueue_manifest(conflicting, "FAKE_ONLY")
    queue.enqueue_manifest({**manifest, "request_key": "different-key"}, "FAKE_ONLY")
    assert len(queue.records) == 2  # Missing global reservation is explicit, not attributed to SQL.


@pytest.mark.parametrize("change", ["revision", "stale_token", "expired", "attempt_limit"])
def test_lease_or_revision_failure_never_invokes_retry(change):
    value = build()
    bound = AtomicAdmissionModel().submit(value)[1]
    claimed = claim(value, bound, attempt=3 if change == "attempt_limit" else 1)
    observed_lease, source = lease(claimed), item()
    if change == "revision": source["version"] = "18"
    if change == "stale_token": observed_lease["lease_token"] = uuid(777)
    if change == "expired": observed_lease["lease_expires_at"] = NOW
    retry = create_autospec(video_queue.retry_job)
    with pytest.raises(contract.SubsetContractError):
        contract.check_attempt(value, expected_contract_sha256=value["contract_sha256"], binding=bound,
            claim=claimed, lease=observed_lease, observed_source=source, worker_key=WORKER, now=NOW, phase="RETRY")
        retry("FAKE_ONLY", job_id=claimed["job_id"], lease_token=claimed["lease_token"],
              worker_key=WORKER, error_code="UV_SYNTHETIC")
    retry.assert_not_called()


def test_retry_rotates_fence_then_canary_pass_stays_hold_without_release():
    value = build()
    bound = AtomicAdmissionModel().submit(value)[1]
    first, second = claim(value, bound), claim(value, bound, attempt=2)
    second["lease_token"] = uuid(201)
    claimant = create_autospec(video_queue.claim_job, side_effect=[first, second, None])
    retry = create_autospec(video_queue.retry_job, return_value={"job_status": "QUEUED",
        "batch_status": "RUNNING", "retry_after": "2026-10-12T12:01:00Z"})
    finish = create_autospec(video_queue.finish_job, return_value={"job_status": "REVIEW_READY",
        "batch_status": "CANARY_REVIEW", "released_jobs": 0})
    def take():
        return claimant("FAKE_ONLY", WORKER, 300, processing_profile=PROFILE, algorithm_revision=REVISION)
    def check(claimed, observed_lease, phase="CLAIM"):
        return contract.check_attempt(value, expected_contract_sha256=value["contract_sha256"],
            binding=bound, claim=claimed, lease=observed_lease, observed_source=item(),
            worker_key=WORKER, now=NOW, phase=phase)
    check(take(), lease(first), "RETRY")
    retry("FAKE_ONLY", job_id=first["job_id"], lease_token=first["lease_token"],
          worker_key=WORKER, error_code="UV_SYNTHETIC", max_attempts=3)
    assert take() == second
    with pytest.raises(contract.SubsetContractError, match="LEASE_MISMATCH"):
        check(first, lease(second), "RETRY")
    check(second, lease(second))
    output = terminal(second)
    result = finish("FAKE_ONLY", job_id=second["job_id"], lease_token=second["lease_token"],
                    worker_key=WORKER, outcome="REVIEW_READY", output=output)
    state = {**result, **{k: second[k] for k in ("batch_id", "job_id", "lease_token", "attempt_count")},
             "terminal_output_sha256": digest(output), "observed_at": NOW}
    review = contract.prepare_canary_review(value, expected_contract_sha256=value["contract_sha256"],
        binding=bound, claim=second, state=state, terminal_output=output, expected_terminal_sha256=digest(output),
        observed_sources=[item(), item(2)], now=NOW)
    assert review["review_packet"]["status"] == "HOLD_FOR_AUTHORIZED_REVIEW"
    assert review["review_packet"]["remaining_file_ids"] == [item(2)["id"]]
    assert all(review[k] is False for k in contract.DISABLED)
    assert take() is None
    assert retry.call_count == finish.call_count == 1
    noncanary = claim(value, bound, canary=False)
    with pytest.raises(contract.SubsetContractError, match="RELEASE_NOT_AUTHORIZED"):
        check(noncanary, lease(noncanary))
