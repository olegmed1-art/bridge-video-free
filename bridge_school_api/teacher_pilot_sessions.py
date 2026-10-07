"""Source-only durable session seam. No canon, solver, DB connection or app registration."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from threading import RLock
from typing import Callable, Protocol
from uuid import UUID, uuid4

MAX_EVENTS = 200
MAX_STATE_BYTES = 131072


class PilotError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def require(ok: bool, code: str) -> None:
    if not ok:
        raise PilotError(code)


def json_copy(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    require(len(raw) <= MAX_STATE_BYTES, "STATE_LIMIT")
    return json.loads(raw)


def digest(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return sha256(raw).hexdigest()


def session_uuid(value: str) -> str:
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise PilotError("SESSION_NOT_FOUND") from None
    require(str(parsed) == value and parsed.version == 4, "SESSION_NOT_FOUND")
    return value


class Runtime(Protocol):
    def view(self) -> dict: ...
    def handle(self, request: dict) -> dict: ...
    def checkpoint(self) -> dict: ...


class RuntimeFactory(Protocol):
    def create(self) -> Runtime: ...
    def restore(self, checkpoint: dict) -> Runtime: ...


class Store(Protocol):
    def create(self, row: dict) -> None: ...
    def read(self, session_id: str) -> dict: ...
    def mutate(self, session_id: str, transform: Callable) -> dict: ...


class SessionService:
    """Restore a checkpoint and atomically persist one accepted runtime event."""
    def __init__(self, runtime_factory: RuntimeFactory, store: Store):
        self.factory = runtime_factory
        self.store = store

    @staticmethod
    def identity(view: dict) -> dict:
        keys = ("version", "content_sha256", "control_suite_version", "control_suite_sha256")
        value = {key: view[key] for key in keys}
        require(all(isinstance(v, str) and v for v in value.values()), "RUNTIME_IDENTITY")
        return value

    def start(self) -> dict:
        runtime = self.factory.create()
        view = json_copy(runtime.view())
        require(view["turn"] == 0, "RUNTIME_INITIAL_TURN")
        session_id = str(uuid4())
        row = {"session_id": session_id, "identity": self.identity(view),
               "turn": 0, "checkpoint": self._checkpoint(session_id, runtime, view), "events": []}
        try:
            self.store.create(json_copy(row))
        except PilotError as exc:
            if exc.code == "COMMIT_OUTCOME_UNKNOWN":
                exc.recovery_session_id = session_id
            raise
        return self._response(row["session_id"], view)

    def read(self, session_id: str) -> dict:
        row = self.store.read(session_uuid(session_id))
        runtime = self._restore(row)
        view = json_copy(runtime.view())
        require(self.identity(view) == row["identity"] and view["turn"] == row["turn"],
                "SESSION_CONTENT_MISMATCH")
        return self._response(session_id, view)

    def event(self, session_id: str, request: dict) -> dict:
        session_uuid(session_id)
        request = json_copy(request)

        def transform(row):
            require(type(request) is dict and type(request.get("turn")) is int,
                    "EVENT_FIELDS")
            require(request["turn"] == row["turn"], "STALE_TURN")
            require(len(row["events"]) < MAX_EVENTS, "EVENT_LIMIT")
            runtime = self._restore(row)
            before = json_copy(runtime.view())
            require(self.identity(before) == row["identity"] and before["turn"] == row["turn"],
                    "SESSION_CONTENT_MISMATCH")
            runtime.handle(deepcopy(request))
            after = json_copy(runtime.view())
            require(self.identity(after) == row["identity"] and after["turn"] == row["turn"] + 1,
                    "RUNTIME_TRANSITION")
            # The immutable journal contains the received event, not a claimed grade.
            event = {"turn": row["turn"], "request": deepcopy(request),
                     "before_sha256": digest(before), "after_sha256": digest(after)}
            row["events"].append(event)
            row["turn"] = after["turn"]
            row["checkpoint"] = self._checkpoint(session_id, runtime, after)
            return json_copy(row), self._response(session_id, after)

        return self.store.mutate(session_id, transform)

    @staticmethod
    def _checkpoint(session_id, runtime, view):
        return {"session_id": session_id, "runtime": runtime.checkpoint(),
                "view_sha256": digest(view)}

    def _restore(self, row):
        checkpoint = row["checkpoint"]
        require(type(checkpoint) is dict
                and set(checkpoint) == {"session_id", "runtime", "view_sha256"}
                and checkpoint["session_id"] == row["session_id"], "CHECKPOINT_SESSION")
        events = row["events"]
        require(type(row["turn"]) is int and type(events) is list
                and row["turn"] == len(events)
                and [e.get("turn") for e in events] == list(range(row["turn"])),
                "JOURNAL_CONSISTENCY")
        for i, entry in enumerate(events):
            require(type(entry) is dict
                    and set(entry) == {"turn", "request", "before_sha256", "after_sha256"}
                    and type(entry["turn"]) is int and entry["turn"] == i
                    and type(entry["request"]) is dict
                    and type(entry["request"].get("turn")) is int
                    and entry["request"]["turn"] == i, "JOURNAL_EVENT_FIELDS")
            for field in ("before_sha256", "after_sha256"):
                value = entry[field]
                require(type(value) is str and len(value) == 64
                        and all(c in "0123456789abcdef" for c in value), "JOURNAL_DIGEST")
            if i:
                require(entry["before_sha256"] == events[i - 1]["after_sha256"],
                        "JOURNAL_CHAIN_MISMATCH")
        if events:
            require(events[0]["before_sha256"] == digest(self.factory.create().view()),
                    "JOURNAL_INITIAL_VIEW")
        runtime = self.factory.restore(deepcopy(checkpoint["runtime"]))
        view = runtime.view()
        if "task_records" in view:
            self._check_record_bindings(events, view)
        view_hash = digest(view)
        require(view_hash == checkpoint["view_sha256"]
                and (not events or events[-1]["after_sha256"] == view_hash),
                "CHECKPOINT_VIEW_MISMATCH")
        return runtime

    @staticmethod
    def _check_record_bindings(events, view):
        # Structural checks over the checkpoint; no handle(), solver or event replay.
        from .text_lesson import parse_answer
        records = view["task_records"]
        require(type(records) is dict, "JOURNAL_RECORDS")
        expected = {}
        for task_id, record in records.items():
            for attempt in record["attempts"]:
                expected[attempt["turn"]] = (task_id, "answer", attempt["answer"])
            for hint in record["hints"]:
                expected[hint["turn"]] = (task_id, "hint", None)
            if record["completed_turn"] is not None:
                expected[record["completed_turn"]] = (task_id, "next", None)
        require(set(expected) == set(range(len(events))), "JOURNAL_RECORD_COVERAGE")
        for entry in events:
            request = entry["request"]
            require(set(request) == {"version", "task_id", "turn", "event", "answer"}
                    and request["version"] == view["version"], "JOURNAL_REQUEST_FIELDS")
            task_id, event, answer = expected[entry["turn"]]
            require(request["task_id"] == task_id and request["event"] == event,
                    "JOURNAL_RECORD_BINDING")
            actual_answer = parse_answer(request["answer"]) if event == "answer" else request["answer"]
            require(actual_answer == answer, "JOURNAL_ANSWER_BINDING")

    @staticmethod
    def _response(session_id, view):
        view = deepcopy(view)
        view["persisted"] = True
        return {"session_id": session_id, "session": view}


class MemoryStore:
    """Tests only; never select this store for a serverless deployment."""
    def __init__(self):
        self.rows = {}
        self._lock = RLock()

    def create(self, row):
        with self._lock:
            require(row["session_id"] not in self.rows, "SESSION_COLLISION")
            self.rows[row["session_id"]] = deepcopy(row)

    def read(self, session_id):
        with self._lock:
            require(session_id in self.rows, "SESSION_NOT_FOUND")
            return deepcopy(self.rows[session_id])

    def mutate(self, session_id, transform):
        with self._lock:
            row = self.read(session_id)
            updated, response = transform(row)
            self.rows[session_id] = deepcopy(updated)
            return response
