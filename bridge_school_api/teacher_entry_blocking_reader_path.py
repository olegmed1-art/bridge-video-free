"""Test-only prepared-reader -> teacher decision integration.

SOURCE_ONLY / native verification NOT_RUN. This module has no API registration,
DB connector, scheduler, card executor or production-canon activation claim.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Callable

from . import teacher_entry_blocking_adapter as adapter

VERSION = "teacher-entry-blocking-reader-path-source-v1"
REGISTRY_PATH = (Path(__file__).resolve().parents[1] / "knowledge" /
                 "teacher_entry_blocking_prepared_v1.json")
REGISTRY_SHA256 = "3227727b640b25e22cb65e004b7cde44eed620eaa4d9bf67fad0e6c31be66457"
MAX_REGISTRY_BYTES = 32768
SkillReader = Callable[[dict[str, Any]], list[dict[str, Any]]]


def _request() -> dict[str, Any]:
    # This bounded request shares the existing KB's system_profile name.
    # The reader receives no holding, exercise ID or answer body.
    return {
        "system_profile": adapter.SYSTEM_VERSION,
        "skill_ids": sorted(adapter.RULE_BINDINGS),
        "role": "declarer", "phase": "suit_timing", "trump": "NT",
        "task_class": "SYMBOLIC_ENTRY_ORDER", "adapter_ref": adapter.ADAPTER_REF,
    }


class PreparedTestReader:
    """Synthetic in-memory eligibility contour; never reports production READY.

    The immutable approved semantic fixture supplies the two skills. Revoke is
    only an in-memory test event. Current generation is returned on each record,
    and fresh reads return independent copies. There is no old-result fallback.
    """
    def __init__(self, prepared_records: Any, *, test_only: bool = False):
        if test_only is not True:
            raise adapter.OutsideScope("TEST_ONLY_OPT_IN_REQUIRED")
        records = adapter._validate_records(prepared_records)
        for record in records:
            skill = record["content"]["skill_contract"]["skill_id"]
            adapter._require(
                record["item_id"] == f"prepared:{skill}:v1"
                and record["version_no"] == 1 and record["dependency_generation"] == 1,
                "ONLY_SYNTHETIC_PREPARED_RECORDS",
            )
        self._records = {
            r["content"]["skill_contract"]["skill_id"]: r for r in records
        }
        self._generation = 1

    @property
    def dependency_generation(self) -> int:
        return self._generation

    def read_eligible_skills(self, request: Any) -> list[dict[str, Any]]:
        adapter._require(type(request) is dict and request == _request(),
                         "READER_REQUEST_SCOPE")
        result = deepcopy([self._records[s] for s in sorted(self._records)])
        for record in result:
            record["dependency_generation"] = self._generation
        return result

    def revoke(self, skill_id: str) -> int:
        """Simulate withdrawal in this instance only; no DB or registry write."""
        adapter._require(type(skill_id) is str and skill_id in adapter.RULE_BINDINGS,
                         "SKILL_SCOPE")
        if skill_id in self._records:
            del self._records[skill_id]
            self._generation += 1
        return self._generation


def load_prepared_test_reader(*, test_only: bool = False) -> PreparedTestReader:
    """Read only the fixed minimal fixture; no arbitrary paths or catalog scan."""
    if test_only is not True:
        raise adapter.OutsideScope("TEST_ONLY_OPT_IN_REQUIRED")
    with REGISTRY_PATH.open("rb") as stream:
        raw = stream.read(MAX_REGISTRY_BYTES + 1)
    adapter._require(len(raw) <= MAX_REGISTRY_BYTES, "PREPARED_REGISTRY_SIZE")
    adapter._require(sha256(raw).hexdigest() == REGISTRY_SHA256, "PREPARED_REGISTRY_PIN")
    registry = json.loads(raw.decode("utf-8"))
    adapter._require(type(registry) is dict
                     and registry.get("schema") == "teacher-entry-blocking-prepared-v1"
                     and registry.get("test_only") is True
                     and registry.get("status") == "SOURCE_ONLY_TEST_FIXTURE_NOT_ACTIVATED",
                     "PREPARED_REGISTRY_SCOPE")
    # Only typed skill records leave the loader. Literal/source-case text is not
    # passed to the decision mechanism, consulted or used to rank candidates.
    return PreparedTestReader(registry["items"], test_only=True)


def _read_inputs(observation: Any, read_eligible_skills: Any):
    try:
        root = adapter._validate_observation(observation)
    except adapter.OutsideScope as exc:
        return None, None, adapter._empty(exc.reason, exc.missing)
    if not callable(read_eligible_skills):
        return None, None, adapter._empty("ELIGIBLE_READER_REQUIRED")
    try:
        records = read_eligible_skills(_request())
    except Exception:
        # Do not echo service exceptions, private values, SQL or credentials.
        return None, None, adapter._empty("READER_FETCH_FAILED")
    try:
        current_root = adapter._validate_observation(observation)
    except adapter.OutsideScope:
        return None, None, adapter._empty("OBSERVATION_CHANGED_DURING_READ",
                                          ("observation",))
    if current_root != root:
        return None, None, adapter._empty("OBSERVATION_CHANGED_DURING_READ",
                                          ("observation",))
    return root, records, None


def decide_from_reader(observation: Any, read_eligible_skills: SkillReader,
                       *, test_only: bool = False) -> dict[str, Any]:
    """Validate visibility, fetch once, invoke the real joint decision seam.

    A future trusted ready-reader can implement this same callable contract.
    Passing current gated SQL metadata is rejected by the existing adapter.
    """
    if test_only is not True:
        return adapter._empty("TEST_ONLY_OPT_IN_REQUIRED")
    root, records, failure = _read_inputs(observation, read_eligible_skills)
    if failure is not None:
        return failure
    return adapter.decide(root, records, test_only=True)


def next_action_from_reader(decision: Any, observation: Any,
                            read_eligible_skills: SkillReader,
                            *, test_only: bool = False) -> dict[str, Any]:
    """Fetch a NEW snapshot before a proposed card; no cached records or replay."""
    if test_only is not True:
        return {"status": "ABSTAIN", "reason": "TEST_ONLY_OPT_IN_REQUIRED", "action": None}
    root, records, failure = _read_inputs(observation, read_eligible_skills)
    if failure is not None:
        return {"status": failure["status"], "reason": failure["reason"],
                "missing_context": failure["missing_context"], "action": None}
    return adapter.next_action(decision, root, records, test_only=True)
