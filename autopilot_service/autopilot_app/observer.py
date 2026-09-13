from __future__ import annotations

"""Deterministic, read-only finding engine for Global School Observer (#1157).

The module deliberately has no network, persistence, workflow, notification, or
mutation capability. Callers supply an already-observed snapshot; the engine
only returns evidence-backed findings.
"""

from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Mapping, Sequence


ACTIVE_STATES = frozenset({"ACTIVE", "RUNNING", "IN_PROGRESS"})
TERMINAL_SUCCESS_STATES = frozenset({"PASS", "DONE", "COMPLETED", "SUCCESS"})
FINDING_CLASSES = frozenset({"ERROR", "RISK", "IMPROVEMENT", "LOOP_DUPLICATION", "STALE"})
SEVERITIES = frozenset({"P0", "P1", "P2", "P3"})


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_strings(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return sorted({item.strip() for item in value if isinstance(item, str) and item.strip()})


def _task_evidence(task: Mapping[str, Any]) -> list[str]:
    refs = _safe_strings(task.get("evidence_refs"))
    task_id = task.get("task_id")
    if isinstance(task_id, str) and task_id.strip():
        fallback = f"task:{task_id.strip()}"
        if fallback not in refs:
            refs.append(fallback)
    return sorted(refs)


def _operation_evidence(operation: Mapping[str, Any]) -> list[str]:
    refs = _safe_strings(operation.get("evidence_refs"))
    operation_id = operation.get("operation_id")
    if isinstance(operation_id, str) and operation_id.strip():
        fallback = f"operation:{operation_id.strip()}"
        if fallback not in refs:
            refs.append(fallback)
    return sorted(refs)


def _make_finding(
    *,
    rule: str,
    finding_class: str,
    severity: str,
    subject: str,
    observed_at: str,
    evidence_refs: Sequence[str],
    current_state: str,
    why_it_matters: str,
    minimal_next_action: str,
    owner_or_lane: str,
) -> dict[str, Any]:
    if finding_class not in FINDING_CLASSES:
        raise ValueError("unsupported finding class")
    if severity not in SEVERITIES:
        raise ValueError("unsupported severity")

    refs = sorted({ref for ref in evidence_refs if isinstance(ref, str) and ref})
    if not refs:
        raise ValueError("observer findings require evidence_refs")

    material = "\x1f".join([rule, finding_class, subject, *refs])
    dedupe_key = sha256(material.encode("utf-8")).hexdigest()
    return {
        "observer_finding_id": f"obs-{dedupe_key[:16]}",
        "class": finding_class,
        "severity": severity,
        "subject": subject,
        "observed_at": observed_at,
        "evidence_refs": refs,
        "current_state": current_state,
        "why_it_matters": why_it_matters,
        "minimal_next_action": minimal_next_action,
        "owner_or_lane": owner_or_lane,
        "dedupe_key": dedupe_key,
        "status": "OPEN",
    }


def evaluate_snapshot(
    snapshot: Mapping[str, Any],
    *,
    now: datetime | None = None,
    stale_after_seconds: int = 3600,
) -> list[dict[str, Any]]:
    """Return deterministic findings without performing any side effect.

    Expected input is intentionally small and normalized by the caller. Unknown
    fields are ignored; missing optional fields never grant authority.
    """

    if stale_after_seconds <= 0:
        raise ValueError("stale_after_seconds must be positive")
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    observed_at = now.isoformat()

    raw_tasks = snapshot.get("tasks", [])
    tasks = [item for item in raw_tasks if isinstance(item, Mapping)] if isinstance(raw_tasks, Sequence) else []
    raw_operations = snapshot.get("operations", [])
    operations = (
        [item for item in raw_operations if isinstance(item, Mapping)]
        if isinstance(raw_operations, Sequence)
        else []
    )

    findings: list[dict[str, Any]] = []
    active_by_resource: dict[str, list[Mapping[str, Any]]] = defaultdict(list)

    for task in tasks:
        task_id = str(task.get("task_id") or "UNKNOWN_TASK")
        status = str(task.get("status") or "UNKNOWN").upper()
        owner = str(task.get("owner") or "UNKNOWN_OWNER")
        resource = task.get("resource")
        refs = _task_evidence(task)

        if status in TERMINAL_SUCCESS_STATES and not _safe_strings(task.get("evidence_refs")):
            findings.append(
                _make_finding(
                    rule="SUCCESS_WITHOUT_EVIDENCE",
                    finding_class="ERROR",
                    severity="P1",
                    subject=task_id,
                    observed_at=observed_at,
                    evidence_refs=refs,
                    current_state=status,
                    why_it_matters="A successful terminal state is claimed without retained evidence.",
                    minimal_next_action="Attach the existing result/receipt evidence or downgrade the claimed state.",
                    owner_or_lane=owner,
                )
            )

        if status in ACTIVE_STATES:
            updated_at = _parse_time(task.get("updated_at"))
            if updated_at is not None and (now - updated_at).total_seconds() > stale_after_seconds:
                findings.append(
                    _make_finding(
                        rule="STALE_ACTIVE_TASK",
                        finding_class="STALE",
                        severity="P1",
                        subject=task_id,
                        observed_at=observed_at,
                        evidence_refs=refs,
                        current_state=status,
                        why_it_matters="The task is still active but its last observation is older than the freshness window.",
                        minimal_next_action="Reconcile the task with its latest terminal receipt or refresh only this task state.",
                        owner_or_lane=owner,
                    )
                )

            if bool(task.get("superseded")):
                findings.append(
                    _make_finding(
                        rule="SUPERSEDED_TASK_STILL_ACTIVE",
                        finding_class="STALE",
                        severity="P2",
                        subject=task_id,
                        observed_at=observed_at,
                        evidence_refs=refs,
                        current_state=status,
                        why_it_matters="A superseded assignment is still represented as active and can cause duplicate work.",
                        minimal_next_action="Stop carrying the superseded assignment forward and keep only the current task.",
                        owner_or_lane=owner,
                    )
                )

            if isinstance(resource, str) and resource.strip():
                active_by_resource[resource.strip()].append(task)

        if bool(task.get("repeated_without_new_evidence")):
            findings.append(
                _make_finding(
                    rule="REPEATED_WITHOUT_NEW_EVIDENCE",
                    finding_class="LOOP_DUPLICATION",
                    severity="P2",
                    subject=task_id,
                    observed_at=observed_at,
                    evidence_refs=refs,
                    current_state=status,
                    why_it_matters="The same check/fix is being repeated without state drift or new evidence.",
                    minimal_next_action="Stop the repeat and continue from the last valid evidence-backed state.",
                    owner_or_lane=owner,
                )
            )

        accepted_state = task.get("accepted_state")
        observed_state = task.get("observed_state")
        if (
            isinstance(accepted_state, str)
            and isinstance(observed_state, str)
            and accepted_state
            and observed_state
            and accepted_state != observed_state
        ):
            findings.append(
                _make_finding(
                    rule="ACCEPTED_OBSERVED_STATE_CONTRADICTION",
                    finding_class="ERROR",
                    severity="P1",
                    subject=task_id,
                    observed_at=observed_at,
                    evidence_refs=refs,
                    current_state=f"accepted={accepted_state}; observed={observed_state}",
                    why_it_matters="The latest observed state contradicts the state currently treated as authoritative.",
                    minimal_next_action="Reconcile this subject to the newest valid evidence before further action.",
                    owner_or_lane=owner,
                )
            )

        if bool(task.get("measurably_duplicative")) and isinstance(task.get("simpler_existing_path"), str):
            findings.append(
                _make_finding(
                    rule="MEASURABLY_DUPLICATIVE_PATH",
                    finding_class="IMPROVEMENT",
                    severity="P2",
                    subject=task_id,
                    observed_at=observed_at,
                    evidence_refs=refs,
                    current_state=status,
                    why_it_matters="The current path duplicates an already-existing simpler path according to supplied evidence.",
                    minimal_next_action=f"Prefer the existing path: {task['simpler_existing_path']}",
                    owner_or_lane=owner,
                )
            )

    for resource, resource_tasks in active_by_resource.items():
        owners = sorted({str(item.get("owner") or "UNKNOWN_OWNER") for item in resource_tasks})
        if len(owners) > 1:
            refs = sorted({ref for item in resource_tasks for ref in _task_evidence(item)})
            findings.append(
                _make_finding(
                    rule="MULTIPLE_ACTIVE_OWNERS",
                    finding_class="LOOP_DUPLICATION",
                    severity="P1",
                    subject=resource,
                    observed_at=observed_at,
                    evidence_refs=refs,
                    current_state=f"owners={','.join(owners)}",
                    why_it_matters="More than one active owner can mutate or coordinate the same resource.",
                    minimal_next_action="Keep one current owner and place the competing assignment on HOLD/STOP.",
                    owner_or_lane="DISPATCHER",
                )
            )

    heavy_by_host: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for operation in operations:
        status = str(operation.get("status") or "UNKNOWN").upper()
        if status not in ACTIVE_STATES:
            continue
        refs = _operation_evidence(operation)
        owner = str(operation.get("owner") or "UNKNOWN_OWNER")
        operation_id = str(operation.get("operation_id") or "UNKNOWN_OPERATION")

        if operation.get("authority_allowed") is False:
            findings.append(
                _make_finding(
                    rule="AUTHORITY_BOUNDARY_VIOLATION",
                    finding_class="RISK",
                    severity="P0",
                    subject=operation_id,
                    observed_at=observed_at,
                    evidence_refs=refs,
                    current_state=status,
                    why_it_matters="An active operation is marked outside its permitted authority boundary.",
                    minimal_next_action="Stop/hold the operation and route it to the required owner authority tier.",
                    owner_or_lane=owner,
                )
            )

        host = operation.get("host")
        if bool(operation.get("heavy")) and isinstance(host, str) and host.strip():
            heavy_by_host[host.strip()].append(operation)

    for host, host_operations in heavy_by_host.items():
        if len(host_operations) > 1:
            refs = sorted({ref for item in host_operations for ref in _operation_evidence(item)})
            findings.append(
                _make_finding(
                    rule="MULTIPLE_HEAVY_OPERATIONS_PER_HOST",
                    finding_class="RISK",
                    severity="P0",
                    subject=host,
                    observed_at=observed_at,
                    evidence_refs=refs,
                    current_state=f"active_heavy={len(host_operations)}",
                    why_it_matters="Multiple heavy workloads on one host violate the default resource-reserve policy.",
                    minimal_next_action="Keep one bounded heavy workload on the host and hold the others.",
                    owner_or_lane="DISPATCHER",
                )
            )

    # Stable dedupe: repeated identical evidence in one snapshot yields one finding.
    by_key: dict[str, dict[str, Any]] = {}
    for finding in findings:
        by_key.setdefault(finding["dedupe_key"], finding)
    return sorted(by_key.values(), key=lambda item: (item["severity"], item["class"], item["subject"]))
