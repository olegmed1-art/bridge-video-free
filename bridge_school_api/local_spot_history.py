"""Add acknowledged public deliveries to the existing store's resume response.

No host, route, command, storage or assessment algorithm is implemented here.
The existing store validates/replays once under its journal lock, exactly as
resume() already does. History is then copied from committed deliveries; it
never submits ADVANCE or computes a new learner assessment.

This additive adapter is pinned to the accepted learner-preview handoff because
TeacherSessionStore currently exposes no public read-history method. Replace
the narrow private _replay dependency when the store publishes that method.
"""
from copy import deepcopy
from hashlib import sha256
import importlib
from pathlib import Path

from .local_spot_session import load_store_class

STORE_SHA256 = "884f9a77b50310383107939edc9c7f516d699f10170e9156102357ca6888cc2b"
PROJECTION_SCHEMA = "teacher-ui-acknowledged-history-v1"


def project_acknowledged_history(records):
    """Project already validated records. Never call this on client input.

    Only public issuance and delivery objects are copied. In particular command
    payload, raw_response, source_bindings and private snapshots are excluded.
    The enclosing locked resume supplies validation and trusted provenance.
    """
    header = records[0]["event"]
    rows = []
    by_issuance = {}

    def issued(value):
        if value is None or "issuance_id" not in value:
            return
        key = value["issuance_id"]
        if key not in by_issuance:
            row = {"assignment": deepcopy(value), "deliveries": []}
            rows.append(row)
            by_issuance[key] = row

    issued(header["initial_assignment"])
    pending = None
    for record in records[1:]:
        event = record["event"]
        if event["kind"] == "COMMAND":
            pending = event
        elif event["kind"] == "DELIVERY_ACK":
            # _replay already established pairing, hash equality and ordering.
            if pending is None or pending["delivery"]["delivery_id"] != event["delivery_id"]:
                raise ValueError("HISTORY_ACK_MISMATCH")
            command, delivery = pending["command"], pending["delivery"]
            by_issuance[command["issuance_id"]]["deliveries"].append({
                "kind": command["kind"], "ack_revision": event["revision"],
                "delivery": deepcopy(delivery),
            })
            issued(delivery["result"].get("next_assignment"))
            pending = None
    return {"schema": PROJECTION_SCHEMA, "session_id": header["session_id"],
            "revision": len(records), "rows": rows}


def resume_with_history(store):
    """Same resume boundary plus an additive safe history, on the same journal.

    The caller supplies its already-authorized TeacherSessionStore instance.
    No browser-supplied path, session creation or reset is accepted here.
    """
    if type(store) is not load_store_class():
        raise ValueError("HISTORY_STORE_TYPE")
    store_module = importlib.import_module(type(store).__module__)
    if sha256(Path(store_module.__file__).read_bytes()).hexdigest() != STORE_SHA256:
        raise store_module.SessionStoreError("HISTORY_STORE_VERSION_MISMATCH")
    with store_module.SQLiteJournal(store.path) as journal:
        lesson, _, _, pending = store._replay(journal)
        current = None if pending else lesson.current()
        return {
            "schema": store_module.SCHEMA, "session_id": store.session_id,
            "session_mode": journal.records[0]["event"]["configuration"]["mode"],
            "revision": len(journal.records), "local_journal_persisted": True,
            "domain_writes_performed": False, "pending_delivery": deepcopy(pending),
            "current_assignment": current,
            "can_send_new_command": pending is None and current is not None and "issuance_id" in current,
            "support_delivery_semantics": "COMMITTED_MEANS_POSSIBLY_EXPOSED",
            "acknowledged_history": project_acknowledged_history(journal.records),
        }
