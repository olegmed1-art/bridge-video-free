"""Offline candidate: existing Journal surface + replay of TeacherLesson.

SQLite is a local file fixture, not a school DB migration. Each call locks,
replays, appends at most one record, and commits before returning any result.
No HTTP, identity, mastery, profile, or curriculum activation is provided.
"""
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3

from .teacher_routing import TeacherLesson

SCHEMA = "local-teacher-session-journal-v2"
APPLICATION_ID = 0x544C4A31
MAX_RECORDS = 257  # header + 128 commands + 128 delivery acknowledgments
MAX_RECORD_BYTES = 262144
MAX_FILE_BYTES = 16 * 1024 * 1024


class SessionStoreError(ValueError):
    pass


def _require(ok, code):
    if not ok:
        raise SessionStoreError(code)


def _json_native(value, depth=0):
    _require(depth <= 24, "JSON_DEPTH")
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        _require(math.isfinite(value), "JSON_FINITE")
        return
    if type(value) is list:
        for item in value:
            _json_native(item, depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            _require(type(key) is str, "JSON_STRING_KEYS")
            _json_native(item, depth + 1)
        return
    raise SessionStoreError("JSON_NATIVE_TYPES_REQUIRED")


def _encoded(value, limit=4 * 1024 * 1024):
    _json_native(value)
    try:
        result = json.dumps(value, sort_keys=True, ensure_ascii=True,
                            separators=(",", ":"), allow_nan=False).encode("ascii")
    except (ValueError, TypeError, RecursionError) as exc:
        raise SessionStoreError("JSON_INVALID") from exc
    _require(len(result) <= limit, "JSON_SIZE")
    return result


def _digest(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _decode(raw):
    try:
        value = json.loads(raw, object_pairs_hook=_unique)
        _encoded(value, MAX_RECORD_BYTES)
        return value
    except (ValueError, TypeError, RecursionError) as exc:
        raise SessionStoreError("JOURNAL_JSON_INVALID") from exc


class SQLiteJournal:
    """Portable local implementation of records/tail/assert_live/append/close.

    Like ops.native_maintenance_workflow_pause.Journal, append is durable before
    returning and records are {previous,event}. The lock covers reading through
    commit; one append per context avoids using state after releasing that lock.
    Existing files must never be recreated implicitly. Trusted private directory
    and caller-selected filename are prerequisites; hashes are not signatures.
    """

    def __init__(self, path, *, create=False):
        self.path = Path(path).absolute()
        self.records, self.failed, self._conn, self._committed = [], False, None, False
        _require(type(create) is bool, "CREATE_MODE")
        _require(not self.path.is_symlink(), "JOURNAL_SYMLINK")
        if create:
            # Reserve a new path atomically; never truncate/reuse an existing file.
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        else:
            _require(self.path.is_file(), "JOURNAL_MISSING")
        _require(self.path.stat().st_size <= MAX_FILE_BYTES, "JOURNAL_FILE_SIZE")
        try:
            self._conn = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True,
                                         timeout=5, isolation_level=None)
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute("BEGIN IMMEDIATE")
            if create:
                self._conn.execute(f"PRAGMA application_id={APPLICATION_ID}")
                self._conn.execute("PRAGMA user_version=1")
                self._conn.execute("CREATE TABLE journal (seq INTEGER PRIMARY KEY, record TEXT NOT NULL)")
            _require(self._conn.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
                     and self._conn.execute("PRAGMA user_version").fetchone()[0] == 1,
                     "JOURNAL_SCHEMA")
            _require(self._conn.execute("PRAGMA quick_check").fetchall() == [("ok",)], "JOURNAL_CORRUPT")
            _require(self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                     == [("journal",)], "JOURNAL_TABLES")
            rows = self._conn.execute("SELECT seq, record FROM journal ORDER BY seq LIMIT ?",
                                      (MAX_RECORDS + 1,)).fetchall()
            _require(len(rows) <= MAX_RECORDS, "JOURNAL_LENGTH")
            for index, (seq, raw) in enumerate(rows, 1):
                _require(seq == index and type(raw) is str and len(raw) <= MAX_RECORD_BYTES,
                         "JOURNAL_SEQUENCE_OR_SIZE")
                record = _decode(raw)
                _require(type(record) is dict and set(record) == {"previous", "event"}
                         and record["previous"] == self.tail, "JOURNAL_CHAIN")
                self.records.append(record)
            _require(create or bool(self.records), "JOURNAL_EMPTY")
        except BaseException:
            self.close()
            raise

    @property
    def tail(self):
        return _digest(self.records[-1]) if self.records else None

    def assert_live(self):
        _require(self._conn is not None and not self.failed and not self._committed,
                 "JOURNAL_UNAVAILABLE")

    def append(self, event):
        self.assert_live()
        record = {"previous": self.tail, "event": deepcopy(event)}
        raw = _encoded(record, MAX_RECORD_BYTES).decode("ascii")
        _require(len(self.records) < MAX_RECORDS, "JOURNAL_LENGTH")
        try:
            self._conn.execute("INSERT INTO journal(seq,record) VALUES (?,?)",
                               (len(self.records) + 1, raw))
            self._conn.commit()
            self._committed = True
            self.records.append(record)
        except BaseException:
            self.failed = True
            raise

    def close(self):
        if self._conn is not None:
            self._conn.close()  # rolls back an unfinished transaction
            self._conn = None

    def __enter__(self):
        self.assert_live()
        return self

    def __exit__(self, *_):
        self.close()


def _bindings(lesson):
    result = lesson.source_bindings()
    result["evolutionary_course/teacher_session_store.py"] = hashlib.sha256(
        Path(__file__).read_bytes()).hexdigest()
    return result


def _lesson(session_id, configuration):
    _require(type(configuration) is dict and set(configuration) == {"mode", "preview_exercise_id"},
             "SESSION_CONFIGURATION")
    if configuration["mode"] == "LOCAL_FORMATIVE" and configuration["preview_exercise_id"] is None:
        return TeacherLesson(session_id)
    if configuration["mode"] == "SPOT_CANDIDATE_PREVIEW_V1":
        from .teacher_preview import PreviewTeacherLesson
        return PreviewTeacherLesson(session_id, configuration["preview_exercise_id"])
    raise SessionStoreError("SESSION_MODE")


def _header(session_id, lesson, configuration):
    return {"schema": SCHEMA, "session_id": session_id, "revision": 1,
            "configuration": deepcopy(configuration),
            "kind": "SESSION_OPEN", "source_bindings": _bindings(lesson),
            "initial_assignment": lesson.current(),
            "post_snapshot_sha256": _digest(lesson.teacher_snapshot())}


def _command(value):
    _encoded(value, 24000)
    _require(type(value) is dict and set(value) == {"command_id", "kind", "issuance_id", "payload"},
             "COMMAND_SHAPE")
    _require(type(value["command_id"]) is str and 1 <= len(value["command_id"]) <= 120
             and type(value["issuance_id"]) is str and 1 <= len(value["issuance_id"]) <= 128,
             "COMMAND_ID")
    _require(value["kind"] in ("RESPONSE", "HINT", "ADVANCE"), "COMMAND_KIND")
    _require(value["kind"] == "RESPONSE" or value["payload"] is None, "COMMAND_PAYLOAD")
    return deepcopy(value)


def _apply(lesson, command):
    args = {k: command[k] for k in ("command_id", "issuance_id")}
    if command["kind"] == "RESPONSE":
        return lesson.record_response(response=command["payload"], **args)
    if command["kind"] == "HINT":
        return lesson.request_hint(**args)
    return lesson.advance(**args)


def _command_event(session_id, revision, command, lesson):
    result = _apply(lesson, command)
    delivery = {"schema": SCHEMA, "session_id": session_id, "revision": revision,
                "delivery_id": command["command_id"], "result": result,
                "result_sha256": _digest(result), "local_journal_persisted": True,
                "domain_writes_performed": False}
    return {"schema": SCHEMA, "session_id": session_id, "revision": revision,
            "kind": "COMMAND", "command": command, "delivery": delivery,
            "post_snapshot_sha256": _digest(lesson.teacher_snapshot())}


def _ack_event(session_id, revision, delivery):
    return {"schema": SCHEMA, "session_id": session_id, "revision": revision,
            "kind": "DELIVERY_ACK", "delivery_id": delivery["delivery_id"],
            "result_sha256": delivery["result_sha256"]}


class TeacherSessionStore:
    """Stateless server-side wrapper; every operation reopens and verifies.

    A reply is a durable delivery, not proof the learner saw it. Pending delivery
    blocks further new commands; replay/ACK remain possible. Committed support
    is conservatively treated as potentially exposed even before delivery ACK.
    """

    def __init__(self, path, session_id):
        _require(type(session_id) is str and 1 <= len(session_id) <= 120, "SESSION_ID")
        self.path, self.session_id = Path(path).absolute(), session_id

    @classmethod
    def create(cls, path, session_id, *, preview_exercise_id=None):
        store = cls(path, session_id)
        configuration = {"mode": "LOCAL_FORMATIVE" if preview_exercise_id is None else "SPOT_CANDIDATE_PREVIEW_V1",
                         "preview_exercise_id": preview_exercise_id}
        lesson = _lesson(session_id, configuration)
        with SQLiteJournal(store.path, create=True) as journal:
            journal.append(_header(session_id, lesson, configuration))
        return store

    def _replay(self, journal):
        first = journal.records[0]["event"] if journal.records else None
        _require(type(first) is dict, "SESSION_HEADER")
        configuration = first.get("configuration")
        lesson = _lesson(self.session_id, configuration)
        _require(_encoded(first) == _encoded(_header(self.session_id, lesson, configuration)),
                 "SESSION_OR_SOURCE_BINDING_MISMATCH")
        commands, acknowledgments, pending = {}, {}, None
        for revision, record in enumerate(journal.records[1:], 2):
            event = record["event"]
            _require(type(event) is dict, "EVENT_SHAPE")
            if event.get("kind") == "COMMAND":
                command = _command(event.get("command"))
                _require(pending is None and command["command_id"] not in commands,
                         "COMMAND_ORDER_OR_DUPLICATE")
                expected = _command_event(self.session_id, revision, command, lesson)
                commands[command["command_id"]] = expected
                pending = expected["delivery"]
            elif event.get("kind") == "DELIVERY_ACK":
                _require(pending is not None, "ACK_WITHOUT_DELIVERY")
                expected = _ack_event(self.session_id, revision, pending)
                acknowledgments[pending["delivery_id"]] = expected
                pending = None
            else:
                raise SessionStoreError("UNKNOWN_EVENT")
            # Canonical bytes also distinguish bool/int and int/float tampering.
            _require(_encoded(event) == _encoded(expected), "REPLAY_MISMATCH")
        return lesson, commands, acknowledgments, pending

    def resume(self):
        with SQLiteJournal(self.path) as journal:
            lesson, _, _, pending = self._replay(journal)
            current = None if pending else lesson.current()
            return {"schema": SCHEMA, "session_id": self.session_id,
                    "session_mode": journal.records[0]["event"]["configuration"]["mode"],
                    "revision": len(journal.records), "local_journal_persisted": True,
                    "domain_writes_performed": False, "pending_delivery": deepcopy(pending),
                    "current_assignment": current,
                    "can_send_new_command": pending is None and current is not None and "issuance_id" in current,
                    "support_delivery_semantics": "COMMITTED_MEANS_POSSIBLY_EXPOSED"}

    def execute(self, command, *, expected_revision):
        command = _command(command)
        _require(type(expected_revision) is int and expected_revision >= 1, "EXPECTED_REVISION")
        with SQLiteJournal(self.path) as journal:
            lesson, commands, _, pending = self._replay(journal)
            old = commands.get(command["command_id"])
            if old is not None:
                _require(_encoded(old["command"]) == _encoded(command), "CONFLICTING_COMMAND_ID")
                return deepcopy(old["delivery"])  # original receipt despite stale revision
            _require(expected_revision == len(journal.records), "STALE_REVISION")
            _require(pending is None, "DELIVERY_ACK_REQUIRED")
            event = _command_event(self.session_id, len(journal.records) + 1, command, lesson)
            journal.append(event)
            return deepcopy(event["delivery"])

    def acknowledge(self, delivery_id, result_sha256):
        _require(type(delivery_id) is str and 1 <= len(delivery_id) <= 120
                 and type(result_sha256) is str and len(result_sha256) == 64, "ACK_SHAPE")
        with SQLiteJournal(self.path) as journal:
            _, _, acknowledgments, pending = self._replay(journal)
            old = acknowledgments.get(delivery_id)
            if old is not None:
                _require(old["result_sha256"] == result_sha256, "CONFLICTING_ACK")
                return deepcopy(old)
            _require(pending is not None and pending["delivery_id"] == delivery_id
                     and pending["result_sha256"] == result_sha256, "ACK_MISMATCH")
            event = _ack_event(self.session_id, len(journal.records) + 1, pending)
            journal.append(event)
            return deepcopy(event)

    def teacher_snapshot(self):
        """Private only: full first response, revisions, support and assessments."""
        with SQLiteJournal(self.path) as journal:
            lesson, _, _, pending = self._replay(journal)
            return {"audience": "TEACHER_ONLY", "revision": len(journal.records),
                    "local_journal_persisted": True, "domain_writes_performed": False,
                    "pending_delivery_id": pending["delivery_id"] if pending else None,
                    "support_delivery_semantics": "COMMITTED_MEANS_POSSIBLY_EXPOSED",
                    "lesson": lesson.teacher_snapshot()}
