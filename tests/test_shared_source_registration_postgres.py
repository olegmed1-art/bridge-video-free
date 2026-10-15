"""Shared identity writers against unchanged migrations in disposable PostgreSQL.

All payloads are invented; no production DSN or source artifact is consumed.
Gate cursors execute real SQL before coordinating scheduling or interruptions.
The public tournament wrapper uses an explicit synthetic transport seam: its
connector/normalizer namespace routes to a genuine caller-owned test connection.
SQL, results, errors and transaction behavior are not mocked. This suite does
not establish production DSN policy or worker login/deployment readiness.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from queue import Queue
import re
from threading import Event, Lock, local
import time
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb
import pytest

from bridge_contracts.book_material import identity
from database.book_source_reconciliation import (
    new_book_source_id, reconcile_book_source_asset,
)
import database.tournament_facts_importer as tournament_writer
from database.source_identity_persistence import ensure_drive_source_identity_on_connection
from database.video_result_persistence import (
    SCHOOL_STABLE_NAME, persist_video_result_on_connection,
)
import test_book_world_postgres as world_pg
from test_book_world import reviewed_fixture, reviewed_identity_fixture

pytestmark = pytest.mark.skipif(
    os.environ.get("BOOK_WORLD_PG_TEST") != "1",
    reason="explicit disposable synthetic PostgreSQL opt-in required",
)

# Book validation retains its existing public error family. Other writers use
# the shared registration contract; unrelated DB/runtime errors never satisfy it.
IDENTITY_CONFLICT = r"(?:SOURCE_REGISTRATION_(?:CONFLICT|DUPLICATE_ALIAS)|BOOK_SOURCE_.*(?:CONFLICT|DUPLICATE))"

TABLES = (
    "source", "asset", "source_asset", "source_identity", "tournament",
    "tournament_participation", "tournament_participant_member", "evidence",
    "entity_resolution_decision", "tournament_identity_attribution",
    "source_observation", "deal", "tournament_board", "table_result",
    "changeset", "ingestion_run", "ingestion_item", "learning_interaction",
    "episode", "decision", "media_asset", "asset_location", "transcript",
    "transcript_segment", "analysis_run", "analysis_run_input", "analysis_run_output",
    "artifact", "artifact_version", "domain_event", "outbox_message", "evidence_link",
    "storage_verification",
)


@pytest.fixture(scope="module")
def local_dsn():
    value = world_pg.DSN
    parts = conninfo_to_dict(value)
    assert parts["host"] == "127.0.0.1"
    assert parts["port"] in ("5432", "55434")
    assert parts["dbname"] == "book_world_test"
    assert parts["user"] == "postgres"
    assert "password" not in parts and "service" not in parts
    with psycopg.connect(value, autocommit=True) as conn:
        assert conn.execute("SELECT current_database()").fetchone() == ("book_world_test",)
        # A complete, unchanged migration surface is mandatory for these tests.
        assert conn.execute("SELECT to_regclass('public.tournament_board'),"
                            "to_regclass('public.analysis_run')").fetchone() == (
                                "tournament_board", "analysis_run")
        conn.execute(
            "INSERT INTO public.school(school_id,stable_name) VALUES(%s,%s)"
            " ON CONFLICT(stable_name) DO NOTHING",
            (uuid4(), SCHOOL_STABLE_NAME),
        )
    return value


def _connect(dsn, checkpoint=None):
    class GateCursor(psycopg.Cursor):
        def execute(self, query, params=None, **kwargs):
            result = super().execute(query, params, **kwargs)
            if checkpoint is not None:
                checkpoint(str(query))
            return result

    conn = psycopg.connect(dsn, cursor_factory=GateCursor, autocommit=True)
    conn.execute("SET statement_timeout='12s'")
    conn.execute("SET deadlock_timeout='100ms'")
    conn.autocommit = False
    return conn


def _snapshot(dsn):
    with psycopg.connect(dsn, autocommit=True) as conn:
        return {table: conn.execute(
            f"SELECT count(*) FROM public.{table}").fetchone()[0] for table in TABLES}


def _school(dsn):
    with psycopg.connect(dsn, autocommit=True) as conn:
        return str(conn.execute(
            "SELECT school_id FROM public.school WHERE stable_name=%s",
            (SCHOOL_STABLE_NAME,)).fetchone()[0])


def _book(dsn, file_id=None):
    bundle, receipt = reviewed_fixture()
    file_id = file_id or "SyntheticShared_" + uuid4().hex
    bundle["source"].update(
        locator="https://drive.google.com/file/d/" + file_id + "/view",
        edition_id=str(uuid4()), rendition_sha256=hashlib.sha256(
            ("SYNTHETIC BOOK " + uuid4().hex).encode()).hexdigest())
    bundle["run"]["run_id"] = str(uuid4())
    receipt["rendition_sha256"] = bundle["source"]["rendition_sha256"]
    for obj, claim in zip(bundle["objects"], receipt["claims"], strict=True):
        obj["object_id"] = "BOOK-" + identity(bundle["source"], obj)
        claim["object_id"] = obj["object_id"]
    school = _school(dsn)
    source = new_book_source_id(school, bundle["source"]["locator"])
    args = dict(
        school_id=school, source_id=source, register_missing_source=True,
        identity_receipt=reviewed_identity_fixture(
            bundle, receipt, school, source, bundle["source"]["locator"]))
    return bundle, receipt, args


def _video(dsn, file_id=None):
    token = uuid4().hex
    revision = "synthetic-shared-writer-v1"
    master = {
        "job_id": "synthetic-shared-video-" + token,
        "algorithmVersion": "synthetic", "algorithmRevision": revision,
        "schemaVersion": "synthetic-shared-v1",
        "source": {
            "driveId": file_id or "SyntheticVideo_" + token,
            "sha256": hashlib.sha256(("SYNTHETIC VIDEO " + token).encode()).hexdigest(),
            "name": "synthetic-shared-video.bin", "mimeType": "video/mp4",
            "sizeBytes": 123, "durationSeconds": 30,
        },
        "transcript": [], "episodes": [], "learning_interactions": [], "decisions": [],
    }
    done = {"masterPdf": {
        "driveId": "SyntheticReport_" + token, "name": "synthetic-report.pdf",
        "sha256": hashlib.sha256(("SYNTHETIC REPORT " + token).encode()).hexdigest(),
        "sizeBytes": 77, "masterJsonEmbedded": True,
    }}
    with psycopg.connect(dsn) as conn:
        school = _school(dsn)
        algorithm = conn.execute(
            "INSERT INTO public.algorithm(school_id,stable_key,name)"
            " VALUES(%s,'bridge-video-master-analysis','Synthetic shared writer')"
            " ON CONFLICT(school_id,stable_key) DO UPDATE SET stable_key=excluded.stable_key"
            " RETURNING algorithm_id", (school,)).fetchone()[0]
        conn.execute(
            "INSERT INTO public.algorithm_version(algorithm_id,version_no,version_label)"
            " SELECT %s,COALESCE(max(version_no),0)+1,%s"
            " FROM public.algorithm_version WHERE algorithm_id=%s"
            " HAVING NOT EXISTS(SELECT 1 FROM public.algorithm_version"
            " WHERE algorithm_id=%s AND version_label=%s)",
            (algorithm, revision, algorithm, algorithm, revision))
    return master, done


def _tournament(dsn, tmp_path, file_id=None):
    token = uuid4().hex
    target = "Synthetic Person " + token
    with psycopg.connect(dsn) as conn:
        person = uuid4()
        conn.execute("INSERT INTO public.person(person_id,preferred_name) VALUES(%s,%s)",
                     (person, target))
        conn.execute("INSERT INTO public.student(student_id,school_id,person_id)"
                     " VALUES(%s,%s,%s)", (uuid4(), _school(dsn), person))
    columns = [
        "board", "slide", "dealer", "vulnerability", "N", "E", "S", "W",
        "status", "pair_direction", "contract", "declarer", "result_delta",
        "opening_lead", "pair_score", "pair_percentage",
    ]
    rows = []
    for board in range(1, 25):
        fields = [board, board + 1, "N", "None",
                  "AKQJT98765432...", ".AKQJT98765432..",
                  "..AKQJT98765432.", "...AKQJT98765432"]
        fields += (["played", "N–S", "1NT", "N", 0, "SA", 90, 50]
                   if board <= 22 else ["unplayed", "", "", "", "", "", "", ""])
        rows.append("|".join(map(str, fields)))
    data = {
        "schema": "bridge-tournament-facts-v1",
        "policy": {"mode": "FACTS_ONLY", "student_observation_writes_allowed": False},
        "source": {
            "drive_id": file_id or "SyntheticTournament_" + token,
            "sha256": hashlib.sha256(("SYNTHETIC TOURNAMENT " + token).encode()).hexdigest(),
            "size_bytes": 456, "title": "Synthetic tournament source",
        },
        "tournament": {
            "provider_native_key": "bridge.co.il:event:" + str(int(token, 16)) + ":round:1",
            "pair_key": "pair:1", "pair_number": 1, "target_student_name": target,
            "members": [target, "Synthetic Other " + token], "scoring": "matchpoints",
            "final_percentage": 50, "rank": 1, "field_size": 2,
            "counted_results": 22, "played_boards": 22,
            "average_results": 0, "unplayed_boards": 2, "date": "2026-01-01",
        },
        "columns": columns, "rows": rows,
    }
    path = tmp_path / ("synthetic-tournament-" + token + ".json")
    path.write_text(json.dumps(data), encoding="utf-8")
    return tournament_writer.load_and_validate(path)


def _book_call(conn, value):
    bundle, receipt, args = value
    return reconcile_book_source_asset(conn, bundle, receipt, **args)


def _video_call(conn, value):
    return persist_video_result_on_connection(conn, *value)


_legacy_lock = Lock()
_legacy_local = local()
_legacy_active = 0
_legacy_original = None


def _tournament_call(conn, value):
    # The public CLI wrapper validates a production DSN and owns its connection.
    # Route only its connector to the genuine caller-owned disposable connection.
    # Thread-local routing permits real concurrent public-writer transactions;
    # refcounted restoration avoids changing the shared psycopg module globally.
    global _legacy_active, _legacy_original

    class BorrowedConnection:
        def __init__(self, borrowed):
            self.borrowed = borrowed
        def __enter__(self):
            return self.borrowed
        def __exit__(self, exc_type, exc, tb):
            if exc_type:
                self.borrowed.rollback()
            return False

    def connect_test(raw, **kwargs):
        assert raw == "explicit-disposable-test-connection"
        return BorrowedConnection(_legacy_local.connection)

    def normalize_test(raw):
        assert raw == "explicit-disposable-test-connection"
        return raw

    with _legacy_lock:
        if _legacy_active == 0:
            _legacy_original = (tournament_writer.psycopg,
                                tournament_writer.normalize_dsn)
            tournament_writer.psycopg = SimpleNamespace(**{**vars(psycopg), "connect": connect_test})
            tournament_writer.normalize_dsn = normalize_test
        _legacy_active += 1
        _legacy_local.connection = conn
    try:
        return tournament_writer.import_facts("explicit-disposable-test-connection", value)
    finally:
        with _legacy_lock:
            _legacy_active -= 1
            del _legacy_local.connection
            if _legacy_active == 0:
                tournament_writer.psycopg, tournament_writer.normalize_dsn = _legacy_original
                _legacy_original = None


def _wait_blocked(dsn, waiter_pid, holder_pid):
    deadline = time.monotonic() + 3
    with psycopg.connect(dsn, autocommit=True) as conn:
        while time.monotonic() < deadline:
            row = conn.execute(
                "SELECT pg_blocking_pids(pid),wait_event_type FROM pg_stat_activity"
                " WHERE pid=%s", (waiter_pid,)).fetchone()
            if row and holder_pid in row[0] and row[1] == "Lock":
                return
            Event().wait(0.01)
    raise AssertionError("no observed PostgreSQL lock wait on the intended holder")


def _run(dsn, writer, value, pids=None, checkpoint=None):
    with _connect(dsn, checkpoint) as conn:
        if pids is not None:
            pids.put(conn.info.backend_pid)
        return writer(conn, value)


def _overlap(dsn, first, second, *, holder_checkpoint=None):
    ready, release = Event(), Event()
    holder_pids, waiter_pids = Queue(), Queue()

    def after_source(query):
        matches = (holder_checkpoint(query) if holder_checkpoint is not None else
                   re.search(r"\binsert\s+into\s+public\.source(?:\s|\()", query, re.I))
        if matches:
            ready.set()
            assert release.wait(8), "holder release was not signalled"

    with ThreadPoolExecutor(max_workers=2) as pool:
        holder = pool.submit(_run, dsn, *first, holder_pids, after_source)
        waiter = None
        try:
            holder_pid = holder_pids.get(timeout=3)
            assert ready.wait(3), "actual writer did not reach its scheduling checkpoint"
            waiter = pool.submit(_run, dsn, *second, waiter_pids)
            waiter_pid = waiter_pids.get(timeout=3)
            _wait_blocked(dsn, waiter_pid, holder_pid)
        finally:
            release.set()
        first_result = holder.result(timeout=15)
        # Return waiter so callers can assert the exact contract rejection.
        return first_result, waiter


def _aliases(dsn, file_id):
    with psycopg.connect(dsn, autocommit=True) as conn:
        return conn.execute(
            "SELECT source_id::text,canonical_locator,source_type,title FROM public.source"
            " WHERE school_id=%s AND canonical_locator=ANY(%s) ORDER BY source_id",
            (_school(dsn), [
                "drive:" + file_id, "gdrive:file:" + file_id,
                "https://drive.google.com/file/d/" + file_id + "/view"])).fetchall()


def test_book_video_source_before_asset_prevents_actual_deadlock(local_dsn):
    book, video = _book(local_dsn), _video(local_dsn)
    book_pids, video_pids = Queue(), Queue()
    sha = book[0]["source"]["rendition_sha256"]
    # Hold the actual book advisory key. Book reaches it after acquiring source
    # SRE, while the old video writer takes asset ROW EXCLUSIVE first.
    with _connect(local_dsn) as gate, ThreadPoolExecutor(max_workers=2) as pool:
        gate.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                     ("book-source-asset:" + sha,))
        first = pool.submit(_run, local_dsn, _book_call, book, book_pids)
        book_pid = book_pids.get(timeout=3)
        _wait_blocked(local_dsn, book_pid, gate.info.backend_pid)
        second = pool.submit(_run, local_dsn, _video_call, video, video_pids)
        _wait_blocked(local_dsn, video_pids.get(timeout=3), book_pid)
        gate.commit()
        outcomes = []
        for future in (first, second):
            try:
                outcomes.append(future.result(timeout=15))
            except Exception as exc:
                outcomes.append((type(exc).__name__, getattr(exc, "sqlstate", None)))
    assert all(isinstance(result, dict) for result in outcomes), outcomes
    assert outcomes[0]["committed"] and outcomes[1]["persisted"]


def test_tournament_later_alias_cannot_duplicate_committed_book(local_dsn, tmp_path):
    file_id = "SyntheticAlias_" + uuid4().hex
    book, tournament = _book(local_dsn, file_id), _tournament(local_dsn, tmp_path, file_id)
    _run(local_dsn, _book_call, book)
    before, original = _snapshot(local_dsn), _aliases(local_dsn, file_id)
    with pytest.raises(ValueError, match=IDENTITY_CONFLICT):
        _run(local_dsn, _tournament_call, tournament)
    assert _snapshot(local_dsn) == before
    assert _aliases(local_dsn, file_id) == original
    assert len(original) == 1


@pytest.mark.parametrize("first_kind", ["book", "tournament"])
def test_concurrent_book_tournament_conflict_is_atomic_in_both_orders(
        local_dsn, tmp_path, first_kind):
    file_id = "SyntheticConcurrentAlias_" + uuid4().hex
    book, tournament = _book(local_dsn, file_id), _tournament(local_dsn, tmp_path, file_id)
    calls = {"book": (_book_call, book), "tournament": (_tournament_call, tournament)}
    second_kind = "tournament" if first_kind == "book" else "book"
    before = _snapshot(local_dsn)
    first, second = _overlap(local_dsn, calls[first_kind], calls[second_kind])
    with pytest.raises(ValueError, match=IDENTITY_CONFLICT):
        second.result(timeout=1)
    aliases = _aliases(local_dsn, file_id)
    assert len(aliases) == 1
    assert aliases[0][2] == ("book" if first_kind == "book" else "tournament_report")
    after = _snapshot(local_dsn)
    assert after["source"] - before["source"] == 1
    if first_kind == "book":
        assert after["asset"] - before["asset"] == 1
        assert after["tournament"] == before["tournament"]
    else:
        assert first["student_observation_writes"] == 0
        assert after["asset"] == before["asset"]


@pytest.mark.parametrize("kind", ["video", "tournament"])
def test_equal_actual_writer_concurrency_and_replay_add_no_identity_rows(
        local_dsn, tmp_path, kind):
    value = _video(local_dsn) if kind == "video" else _tournament(local_dsn, tmp_path)
    call = (_video_call, value) if kind == "video" else (_tournament_call, value)
    first, second = _overlap(local_dsn, call, call)
    assert second.result(timeout=1) == first
    committed = _snapshot(local_dsn)
    assert _run(local_dsn, *call) == first
    assert _snapshot(local_dsn) == committed


@pytest.mark.parametrize("first_kind", ["video", "tournament"])
def test_video_tournament_same_file_cannot_change_source_meaning(
        local_dsn, tmp_path, first_kind):
    file_id = "SyntheticCrossWriter_" + uuid4().hex
    video, tournament = _video(local_dsn, file_id), _tournament(local_dsn, tmp_path, file_id)
    calls = {"video": (_video_call, video), "tournament": (_tournament_call, tournament)}
    second_kind = "tournament" if first_kind == "video" else "video"
    _run(local_dsn, *calls[first_kind])
    before, original = _snapshot(local_dsn), _aliases(local_dsn, file_id)
    with pytest.raises(ValueError, match=IDENTITY_CONFLICT):
        _run(local_dsn, *calls[second_kind])
    assert _snapshot(local_dsn) == before
    assert _aliases(local_dsn, file_id) == original
    assert len(original) == 1


@pytest.mark.parametrize("first_kind", ["video", "tournament"])
def test_concurrent_video_tournament_alias_conflict_preserves_first_source(
        local_dsn, tmp_path, first_kind):
    file_id = "SyntheticConcurrentCross_" + uuid4().hex
    video, tournament = _video(local_dsn, file_id), _tournament(local_dsn, tmp_path, file_id)
    calls = {"video": (_video_call, video), "tournament": (_tournament_call, tournament)}
    second_kind = "tournament" if first_kind == "video" else "video"
    before = _snapshot(local_dsn)
    first, second = _overlap(local_dsn, calls[first_kind], calls[second_kind])
    with pytest.raises(ValueError, match=IDENTITY_CONFLICT):
        second.result(timeout=1)
    aliases = _aliases(local_dsn, file_id)
    assert len(aliases) == 1
    assert aliases[0][2] == ("lesson_video" if first_kind == "video" else "tournament_report")
    after = _snapshot(local_dsn)
    assert after["source"] - before["source"] == 1
    if first_kind == "video":
        assert first["persisted"]
        assert after["tournament"] == before["tournament"]
    else:
        assert first["student_observation_writes"] == 0
        assert after["asset"] == before["asset"]


class SyntheticAbort(RuntimeError):
    pass


@pytest.mark.parametrize("kind", ["video", "tournament"])
def test_interrupted_writer_rolls_back_all_identity_and_dependent_rows(
        local_dsn, tmp_path, kind):
    """Early rollback after source INSERT; late dependent writes are tested below."""
    value = _video(local_dsn) if kind == "video" else _tournament(local_dsn, tmp_path)
    call = (_video_call, value) if kind == "video" else (_tournament_call, value)

    def abort_after_source(query):
        if re.search(r"\binsert\s+into\s+public\.source(?:\s|\()", query, re.I):
            raise SyntheticAbort("synthetic interruption after actual source INSERT")

    before = _snapshot(local_dsn)
    with pytest.raises(SyntheticAbort):
        _run(local_dsn, *call, checkpoint=abort_after_source)
    assert _snapshot(local_dsn) == before


def _writer_case(dsn, tmp_path, kind):
    if kind == "video":
        value = _video(dsn)
        source = value[0]["source"]
        return ((_video_call, value), source["driveId"], source["name"], "lesson_video")
    value = _tournament(dsn, tmp_path)
    return ((_tournament_call, value), value["source"]["drive_id"],
            value["source"]["title"], "tournament_report")


def _expected_source_id(file_id):
    # Both existing public writers use this stable identity contract. Probe
    # conflicts using their existing UUID, rather than introducing a new ID.
    return tournament_writer._stable_uuid("source", "google-drive", file_id)


def _seed_source(dsn, *, source_id, locator, source_type, title,
                 school_id=None, status="active"):
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO public.source(source_id,school_id,canonical_locator,"
            "source_type,title,status) VALUES(%s,%s,%s,%s,%s,%s)",
            (source_id, school_id or _school(dsn), locator, source_type, title, status))


def test_actual_video_composes_with_safe_read_committed_outer_transaction(local_dsn):
    value = _video(local_dsn)
    with _connect(local_dsn) as conn, conn.transaction():
        assert conn.execute("SELECT 1").fetchone() == (1,)
        result = _video_call(conn, value)
        assert result["persisted"]
    with psycopg.connect(local_dsn, autocommit=True) as read:
        assert read.execute("SELECT count(*) FROM public.source WHERE source_id=%s",
                            (result["source_id"],)).fetchone() == (1,)


@pytest.mark.parametrize("held", ["source", "asset", "source_asset", "advisory"])
def test_actual_video_rejects_prior_identity_or_advisory_locks(local_dsn, held):
    value = _video(local_dsn)
    before = _snapshot(local_dsn)
    with _connect(local_dsn) as conn:
        with pytest.raises(ValueError, match="^SOURCE_REGISTRATION_LOCK_ORDER_REQUIRED$"):
            with conn.transaction():
                if held == "advisory":
                    conn.execute("SELECT pg_advisory_xact_lock(%s)",
                                 (int(uuid4().hex[:14], 16),))
                else:
                    conn.execute(f"LOCK TABLE public.{held} IN ROW EXCLUSIVE MODE")
                _video_call(conn, value)
    assert _snapshot(local_dsn) == before


def test_actual_video_allows_already_ordered_source_then_asset_composition(local_dsn):
    value = _video(local_dsn)
    with _connect(local_dsn) as conn, conn.transaction():
        conn.execute("LOCK TABLE public.source IN SHARE ROW EXCLUSIVE MODE")
        conn.execute("LOCK TABLE public.asset IN ROW EXCLUSIVE MODE")
        result = _video_call(conn, value)
        assert result["persisted"]
    assert len(_aliases(local_dsn, value[0]["source"]["driveId"])) == 1


def test_actual_video_rejects_repeatable_read_snapshot_without_partial_rows(local_dsn):
    value = _video(local_dsn)
    before = _snapshot(local_dsn)
    with _connect(local_dsn) as conn:
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        with pytest.raises(ValueError, match="^SOURCE_REGISTRATION_REQUIRES_READ_COMMITTED$"):
            with conn.transaction():
                conn.execute("SELECT 1")
                _video_call(conn, value)
    assert _snapshot(local_dsn) == before


@pytest.mark.parametrize("kind", ["video", "tournament"])
@pytest.mark.parametrize("stored_form", ["https", "drive", "gdrive"])
def test_actual_writers_reject_other_uuid_in_every_drive_alias_form(
        local_dsn, tmp_path, kind, stored_form):
    call, file_id, title, source_type = _writer_case(local_dsn, tmp_path, kind)
    locators = {"https": "https://drive.google.com/file/d/" + file_id + "/view",
                "drive": "drive:" + file_id, "gdrive": "gdrive:file:" + file_id}
    _seed_source(local_dsn, source_id=uuid4(), locator=locators[stored_form],
                 source_type=source_type, title=title)
    before, original = _snapshot(local_dsn), _aliases(local_dsn, file_id)
    with pytest.raises(ValueError, match="^SOURCE_REGISTRATION_CONFLICT$"):
        _run(local_dsn, *call)
    assert _snapshot(local_dsn) == before
    assert _aliases(local_dsn, file_id) == original


@pytest.mark.parametrize("kind", ["video", "tournament"])
@pytest.mark.parametrize("conflict", ["other-school", "different-locator"])
def test_actual_writers_reject_expected_uuid_bound_to_other_identity(
        local_dsn, tmp_path, kind, conflict):
    call, file_id, title, source_type = _writer_case(local_dsn, tmp_path, kind)
    source_id = _expected_source_id(file_id)
    school = _school(local_dsn)
    locator = "drive:" + file_id
    if conflict == "other-school":
        school = str(uuid4())
        with psycopg.connect(local_dsn) as conn:
            conn.execute("INSERT INTO public.school(school_id,stable_name) VALUES(%s,%s)",
                         (school, "synthetic-other-school-" + uuid4().hex))
    else:
        locator = "drive:SyntheticDifferent_" + uuid4().hex
    _seed_source(local_dsn, source_id=source_id, school_id=school,
                 locator=locator, source_type=source_type, title=title)
    before = _snapshot(local_dsn)
    with pytest.raises(ValueError, match="^SOURCE_REGISTRATION_CONFLICT$"):
        _run(local_dsn, *call)
    assert _snapshot(local_dsn) == before
    with psycopg.connect(local_dsn, autocommit=True) as read:
        assert read.execute(
            "SELECT school_id::text,canonical_locator,source_type,title"
            " FROM public.source WHERE source_id=%s", (source_id,)).fetchone() == (
                school, locator, source_type, title)


@pytest.mark.parametrize("kind", ["video", "tournament"])
@pytest.mark.parametrize("conflict", ["inactive", "duplicate-inactive"])
def test_actual_writers_reject_inactive_and_duplicate_aliases(
        local_dsn, tmp_path, kind, conflict):
    call, file_id, title, source_type = _writer_case(local_dsn, tmp_path, kind)
    _seed_source(
        local_dsn, source_id=_expected_source_id(file_id), locator="drive:" + file_id,
        source_type=source_type, title=title,
        status="inactive" if conflict == "inactive" else "active")
    if conflict == "duplicate-inactive":
        _seed_source(
            local_dsn, source_id=uuid4(),
            locator="https://drive.google.com/file/d/" + file_id + "/view",
            source_type=source_type, title=title, status="inactive")
    before, original = _snapshot(local_dsn), _aliases(local_dsn, file_id)
    code = ("SOURCE_REGISTRATION_CONFLICT" if conflict == "inactive" else
            "SOURCE_REGISTRATION_DUPLICATE_ALIAS")
    with pytest.raises(ValueError, match="^" + code + "$"):
        _run(local_dsn, *call)
    assert _snapshot(local_dsn) == before
    assert _aliases(local_dsn, file_id) == original


@pytest.mark.parametrize("kind", ["video", "tournament"])
def test_actual_writer_replay_preserves_existing_source_title(local_dsn, tmp_path, kind):
    call, file_id, _, _ = _writer_case(local_dsn, tmp_path, kind)
    result = _run(local_dsn, *call)
    revised_title = "Synthetic preserved catalog title " + uuid4().hex
    with psycopg.connect(local_dsn) as conn:
        conn.execute("UPDATE public.source SET title=%s WHERE source_id=%s",
                     (revised_title, result["source_id"]))
    before, original = _snapshot(local_dsn), _aliases(local_dsn, file_id)
    assert _run(local_dsn, *call) == result
    assert _snapshot(local_dsn) == before
    assert _aliases(local_dsn, file_id) == original
    assert original[0][3] == revised_title


@pytest.mark.parametrize("kind", ["video", "tournament"])
@pytest.mark.parametrize("stored_form", ["https", "drive", "gdrive"])
def test_actual_writers_accept_matching_legacy_alias_without_rewriting_identity(
        local_dsn, tmp_path, kind, stored_form):
    call, file_id, _, source_type = _writer_case(local_dsn, tmp_path, kind)
    locators = {"https": "https://drive.google.com/file/d/" + file_id + "/view",
                "drive": "drive:" + file_id, "gdrive": "gdrive:file:" + file_id}
    preserved_title = "Synthetic legacy catalog title " + uuid4().hex
    source_id = _expected_source_id(file_id)
    _seed_source(local_dsn, source_id=source_id, locator=locators[stored_form],
                 source_type=source_type, title=preserved_title)
    original = _aliases(local_dsn, file_id)
    first = _run(local_dsn, *call)
    assert first["source_id"] == str(source_id)
    assert _aliases(local_dsn, file_id) == original
    committed = _snapshot(local_dsn)
    assert _run(local_dsn, *call) == first
    assert _snapshot(local_dsn) == committed
    assert _aliases(local_dsn, file_id) == original


@pytest.mark.parametrize("kind", ["book", "video", "tournament"])
def test_actual_writers_use_existing_worker_grants_without_privilege_changes(
        local_dsn, tmp_path, kind):
    # Existing migrations create this principal as NOLOGIN. Assume it only
    # inside the disposable superuser session, changing effective session/current
    # identity without GRANT, ALTER ROLE, membership, credentials or DDL.
    # This verifies existing capabilities, not worker login/deployment readiness.
    principal = "bridge_school_worker_principal"
    if kind == "book":
        value = _book(local_dsn)
        bundle, _, args = value
        _seed_source(local_dsn, source_id=args["source_id"],
                     locator=bundle["source"]["locator"], source_type="book",
                     title=bundle["source"]["title"])
        args["register_missing_source"] = False
        call = (_book_call, value)
        file_id = bundle["source"]["locator"].split("/d/", 1)[1].split("/", 1)[0]
    else:
        call, file_id, _, _ = _writer_case(local_dsn, tmp_path, kind)
    before = _snapshot(local_dsn)
    # The admin-only scheduling helper's deadlock_timeout setting is not applied.
    with psycopg.connect(local_dsn) as conn:
        conn.execute("SET SESSION AUTHORIZATION bridge_school_worker_principal")
        assert conn.execute("SELECT session_user,current_user").fetchone() == (
            principal, principal)
        conn.commit()  # book contract requires an idle caller-owned connection
        result = call[0](conn, call[1])
    assert len(_aliases(local_dsn, file_id)) == 1
    after = _snapshot(local_dsn)
    assert after["source"] - before["source"] == (0 if kind == "book" else 1)
    if kind == "book":
        assert result["committed"] and result["new_source_rows"] == 0
        assert after["asset"] - before["asset"] == 1
        assert after["source_asset"] - before["source_asset"] == 1
    elif kind == "video":
        assert result["persisted"]
        assert after["asset"] - before["asset"] == 2
        assert after["source_asset"] - before["source_asset"] == 1
    else:
        assert result["status"] == "TOURNAMENT_FACTS_IMPORTED"
        assert result["student_observation_writes"] == 0
        assert after["asset"] == before["asset"]


def test_actual_video_rejects_prior_ddl_in_pgcustom_application_schema(local_dsn):
    value = _video(local_dsn)
    schema = "pgcustom_" + uuid4().hex
    before = _snapshot(local_dsn)
    # pgcustom_* is an ordinary application name, not PostgreSQL's reserved
    # pg_ prefix. SQL LIKE 'pg_%' incorrectly hides this schema's write locks.
    with pytest.raises(ValueError, match="^SOURCE_REGISTRATION_LOCK_ORDER_REQUIRED$"):
        with _connect(local_dsn) as conn, conn.transaction():
            conn.execute(f"CREATE SCHEMA {schema}")
            conn.execute(f"CREATE TABLE {schema}.synthetic_gate(value integer)")
            conn.execute(f"INSERT INTO {schema}.synthetic_gate(value) VALUES(1)")
            _video_call(conn, value)
    assert _snapshot(local_dsn) == before
    with psycopg.connect(local_dsn, autocommit=True) as read:
        assert read.execute("SELECT to_regnamespace(%s)", (schema,)).fetchone() == (None,)


@pytest.mark.parametrize("register", [False, True], ids=["existing-only", "registration"])
def test_book_existing_only_keeps_legacy_type_policy_while_registration_is_strict(
        local_dsn, register):
    value = _book(local_dsn)
    bundle, _, args = value
    source_id = args["source_id"]
    locator = bundle["source"]["locator"]
    title = bundle["source"]["title"]
    _seed_source(local_dsn, source_id=source_id, locator=locator,
                 source_type="tournament_report", title=title)
    args["register_missing_source"] = register
    before = _snapshot(local_dsn)
    if register:
        with pytest.raises(ValueError, match="^BOOK_SOURCE_REGISTRY_CONFLICT$"):
            _run(local_dsn, _book_call, value)
        assert _snapshot(local_dsn) == before
    else:
        # The old receipt-bound existing-source API did not require source_type
        # book. Preserve that compatibility without broadening create admission.
        result = _run(local_dsn, _book_call, value)
        assert result["committed"] and result["new_source_rows"] == 0
        after = _snapshot(local_dsn)
        assert after["source"] == before["source"]
        assert after["asset"] - before["asset"] == 1
        assert after["source_asset"] - before["source_asset"] == 1
    with psycopg.connect(local_dsn, autocommit=True) as read:
        assert read.execute(
            "SELECT source_type,canonical_locator,title FROM public.source"
            " WHERE source_id=%s", (source_id,)).fetchone() == (
                "tournament_report", locator, title)


@pytest.mark.parametrize("register", [False, True], ids=["existing-only", "registration"])
def test_shared_alias_detection_does_not_authorize_gdrive_book_receipt(
        local_dsn, register):
    value = _book(local_dsn)
    bundle, _, args = value
    file_id = bundle["source"]["locator"].split("/d/", 1)[1].split("/", 1)[0]
    stored = "gdrive:file:" + file_id
    _seed_source(local_dsn, source_id=args["source_id"], locator=stored,
                 source_type="book", title=bundle["source"]["title"])
    args["register_missing_source"] = register
    args["identity_receipt"]["stored_locator"] = stored
    before = _snapshot(local_dsn)
    with pytest.raises(ValueError, match="^BOOK_SOURCE_IDENTITY_REVIEW_BINDING$"):
        _run(local_dsn, _book_call, value)
    assert _snapshot(local_dsn) == before
    assert _aliases(local_dsn, file_id) == [
        (str(args["source_id"]), stored, "book", bundle["source"]["title"])]


@pytest.mark.parametrize("kind", ["video", "tournament"])
def test_late_interruption_rolls_back_observed_identity_and_dependent_writes(
        local_dsn, tmp_path, kind):
    value = _video(local_dsn) if kind == "video" else _tournament(local_dsn, tmp_path)
    call = (_video_call, value) if kind == "video" else (_tournament_call, value)
    before = _snapshot(local_dsn)
    observed, active = {}, {}

    def interrupt_after_completed_write_phase(query):
        normalized = " ".join(query.lower().split())
        checkpoint = (
            normalized.startswith("update public.changeset ")
            if kind == "video" else
            normalized.startswith(
                "select count(*) from public.tournament_identity_attribution "))
        if not checkpoint or active.get("probing"):
            return
        active["probing"] = True
        # Read on the actual writer connection before raising: this proves
        # dependent rows and trigger side effects really existed uncommitted.
        with active["connection"].cursor() as probe:
            for table in TABLES:
                observed[table] = probe.execute(
                    f"SELECT count(*) FROM public.{table}").fetchone()[0]
        raise SyntheticAbort("synthetic late interruption after completed write phase")

    with pytest.raises(SyntheticAbort, match="synthetic late interruption"):
        with _connect(local_dsn, interrupt_after_completed_write_phase) as conn:
            active["connection"] = conn
            call[0](conn, call[1])
    assert observed, "actual writer never reached its late interruption checkpoint"
    assert observed["source"] - before["source"] == 1
    if kind == "video":
        for table, rows in {
            "asset": 2, "source_asset": 1, "media_asset": 1,
            "asset_location": 2, "storage_verification": 2,
            "transcript": 1, "analysis_run": 1, "artifact": 1,
            "artifact_version": 1, "analysis_run_output": 1,
            "changeset": 1, "domain_event": 1, "outbox_message": 1,
        }.items():
            assert observed[table] - before[table] == rows, table
    else:
        for table, rows in {
            "source_identity": 3, "tournament": 1, "tournament_participation": 1,
            "tournament_participant_member": 2,
            "tournament_identity_attribution": 1,
            "entity_resolution_decision": 1, "evidence": 1,
            "source_observation": 24, "deal": 24,
            "tournament_board": 24, "table_result": 22,
        }.items():
            assert observed[table] - before[table] == rows, table
    assert _snapshot(local_dsn) == before


def _source_registration_case(dsn, tmp_path, kind):
    if kind == "book":
        value = _book(dsn)
        bundle, _, args = value
        locator = bundle["source"]["locator"]
        file_id = locator.split("/d/", 1)[1].split("/", 1)[0]
        return ((_book_call, value), file_id, bundle["source"]["title"],
                "book", args["source_id"], locator)
    call, file_id, title, source_type = _writer_case(dsn, tmp_path, kind)
    locator = ("gdrive:file:" if kind == "video" else "drive:") + file_id
    return call, file_id, title, source_type, _expected_source_id(file_id), locator


def _seed_native_identity(dsn, source_id, native_key):
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO public.source_identity(source_identity_id,source_id,"
            "source_native_key,display_name,attributes) VALUES(%s,%s,%s,%s,%s)",
            (uuid4(), source_id, native_key, "Synthetic provider identity",
             Jsonb({"provider": "synthetic", "identity_scope": "synthetic-test"})))


@pytest.mark.parametrize("kind", ["book", "video", "tournament"])
@pytest.mark.parametrize("key_prefix", ["google-drive:", "google-drive:file:"])
def test_actual_registration_rejects_native_key_hidden_under_other_source_uuid(
        local_dsn, tmp_path, kind, key_prefix):
    call, file_id, _, source_type, _, _ = _source_registration_case(
        local_dsn, tmp_path, kind)
    other_id = uuid4()
    _seed_source(local_dsn, source_id=other_id,
                 locator="synthetic:unrelated-canonical:" + uuid4().hex,
                 source_type=source_type, title="Synthetic different source")
    _seed_native_identity(local_dsn, other_id, key_prefix + file_id)
    before = _snapshot(local_dsn)
    with pytest.raises(ValueError, match=IDENTITY_CONFLICT):
        _run(local_dsn, *call)
    assert _snapshot(local_dsn) == before
    assert _aliases(local_dsn, file_id) == []


@pytest.mark.parametrize("kind", ["book", "video", "tournament"])
def test_actual_registration_deduplicates_both_native_keys_for_same_source(
        local_dsn, tmp_path, kind):
    call, file_id, title, source_type, source_id, locator = _source_registration_case(
        local_dsn, tmp_path, kind)
    _seed_source(local_dsn, source_id=source_id, locator=locator,
                 source_type=source_type, title=title)
    for prefix in ("google-drive:", "google-drive:file:"):
        _seed_native_identity(local_dsn, source_id, prefix + file_id)
    original, before = _aliases(local_dsn, file_id), _snapshot(local_dsn)
    result = _run(local_dsn, *call)
    assert result["source_id"] == str(source_id)
    assert _aliases(local_dsn, file_id) == original
    assert _snapshot(local_dsn)["source"] == before["source"]
    committed = _snapshot(local_dsn)
    replay = _run(local_dsn, *call)
    if kind == "book":
        assert replay["source_id"] == result["source_id"]
        assert replay["asset_id"] == result["asset_id"]
        assert tuple(replay[key] for key in
                     ("new_source_rows", "new_asset_rows", "new_link_rows")) == (0, 0, 0)
    else:
        assert replay == result
    assert _snapshot(local_dsn) == committed


@pytest.mark.parametrize("kind", ["book", "video", "tournament"])
@pytest.mark.parametrize("noise", [
    "participant-key", "other-school-google-drive:", "other-school-google-drive:file:",
])
def test_actual_registration_ignores_unrelated_participant_and_other_school_keys(
        local_dsn, tmp_path, kind, noise):
    call, file_id, _, source_type, _, _ = _source_registration_case(
        local_dsn, tmp_path, kind)
    school = _school(local_dsn)
    if noise.startswith("other-school-"):
        school = str(uuid4())
        with psycopg.connect(local_dsn) as conn:
            conn.execute("INSERT INTO public.school(school_id,stable_name) VALUES(%s,%s)",
                         (school, "synthetic-native-key-other-school-" + uuid4().hex))
        native = noise.removeprefix("other-school-") + file_id
    else:
        native = "bridge.co.il:event:" + file_id + ":round:1:participant:1"
    other_id = uuid4()
    _seed_source(local_dsn, source_id=other_id, school_id=school,
                 locator="synthetic:unrelated-native-scope:" + uuid4().hex,
                 source_type=source_type, title="Synthetic non-conflicting source")
    _seed_native_identity(local_dsn, other_id, native)
    before = _snapshot(local_dsn)
    result = _run(local_dsn, *call)
    assert result.get("committed", result.get("persisted", True))
    assert len(_aliases(local_dsn, file_id)) == 1
    assert _snapshot(local_dsn)["source"] - before["source"] == 1


def _drive_identity_call(conn, file_id):
    return ensure_drive_source_identity_on_connection(
        conn, source_drive_id=file_id, display_name="Synthetic Drive identity",
        job_id="synthetic-source-identity-race")


@pytest.mark.parametrize("first_kind", ["registration", "identity"])
def test_standalone_drive_identity_serializes_with_actual_video_registration(
        local_dsn, tmp_path, first_kind):
    video, file_id, title, source_type, source_id, locator = _source_registration_case(
        local_dsn, tmp_path, "video")
    _seed_source(local_dsn, source_id=source_id, locator=locator,
                 source_type=source_type, title=title)
    original, before = _aliases(local_dsn, file_id), _snapshot(local_dsn)
    calls = {"registration": video, "identity": (_drive_identity_call, file_id)}
    second_kind = "identity" if first_kind == "registration" else "registration"

    def holder_checkpoint(query):
        normalized = " ".join(query.lower().split())
        return (normalized.startswith("lock table public.source ")
                if first_kind == "registration" else
                normalized.startswith("insert into public.source_identity "))

    first, second = _overlap(
        local_dsn, calls[first_kind], calls[second_kind],
        holder_checkpoint=holder_checkpoint)
    results = {first_kind: first, second_kind: second.result(timeout=1)}
    assert results["registration"]["persisted"]
    assert results["identity"]["source_id"] == str(source_id)
    assert results["identity"]["source_native_key"] == "google-drive:" + file_id
    after = _snapshot(local_dsn)
    assert after["source"] == before["source"]
    assert after["source_identity"] - before["source_identity"] == 1
    assert after["asset"] - before["asset"] == 2
    assert after["source_asset"] - before["source_asset"] == 1
    assert _aliases(local_dsn, file_id) == original


def test_standalone_drive_identity_rejects_mismatched_canonical_source(local_dsn):
    file_id = "SyntheticMismatchedNative_" + uuid4().hex
    _seed_source(
        local_dsn, source_id=_expected_source_id(file_id),
        locator="synthetic:unrelated-canonical:" + uuid4().hex,
        source_type="lesson_video", title="Synthetic mismatched source")
    before = _snapshot(local_dsn)
    with pytest.raises(ValueError, match="^SOURCE_REGISTRATION_CONFLICT$"):
        _run(local_dsn, _drive_identity_call, file_id)
    assert _snapshot(local_dsn) == before


def test_book_registration_missing_existing_worker_privilege_rolls_back(local_dsn):
    value = _book(local_dsn)
    before = _snapshot(local_dsn)
    # The unchanged migration principal may reconcile an existing source, but
    # lacks the school UPDATE privilege required by create's SELECT FOR SHARE.
    # Preserve that boundary; do not add grants/ALTER ROLE for a green result.
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with psycopg.connect(local_dsn) as conn:
            conn.execute("SET SESSION AUTHORIZATION bridge_school_worker_principal")
            assert conn.execute("SELECT session_user,current_user").fetchone() == (
                "bridge_school_worker_principal", "bridge_school_worker_principal")
            conn.commit()
            _book_call(conn, value)
    assert _snapshot(local_dsn) == before


def test_standalone_identity_rejects_file_prefix_native_namespace_collision(local_dsn):
    real_id = "SyntheticRealNative_" + uuid4().hex
    book = _book(local_dsn, real_id)
    real = _run(local_dsn, _book_call, book)
    # The legitimate book already owns the file identity. A malformed metadata
    # ID file:<realID> would emit its google-drive:file:<realID> native key.
    _seed_native_identity(local_dsn, real["source_id"], "google-drive:file:" + real_id)
    malformed = "file:" + real_id
    malformed_source = _expected_source_id(malformed)
    _seed_source(
        local_dsn, source_id=malformed_source, locator="gdrive:file:" + malformed,
        source_type="lesson_video", title="Synthetic malformed historical locator")
    before, queries = _snapshot(local_dsn), []
    with _connect(local_dsn, queries.append) as conn:
        queries.clear()  # scheduling helper SET statements are test setup only
        with pytest.raises(
                ValueError, match="^SOURCE_REGISTRATION_(?:DRIVE_ID|LOCATOR)_INVALID$"):
            _drive_identity_call(conn, malformed)
    assert queries == [], "invalid raw file ID must fail before actual writer SQL"
    assert _snapshot(local_dsn) == before


@pytest.mark.parametrize("kind", ["video", "tournament"])
@pytest.mark.parametrize("bad_shape", [
    "file-prefix", "list", "null", "integer", "boolean", "whitespace", "url", "empty",
])
def test_actual_metadata_writers_reject_malformed_drive_id_before_sql(
        local_dsn, tmp_path, kind, bad_shape):
    call, file_id, _, _ = _writer_case(local_dsn, tmp_path, kind)
    invalid = {
        "file-prefix": "file:" + file_id,
        "list": [file_id], "null": None, "integer": 314159, "boolean": True,
        "whitespace": " " + file_id + " ",
        "url": "https://drive.google.com/file/d/" + file_id + "/view",
        "empty": "",
    }[bad_shape]
    if kind == "video":
        call[1][0]["source"]["driveId"] = invalid
    else:
        call[1]["source"]["drive_id"] = invalid
    before, queries = _snapshot(local_dsn), []
    # Preserve the existing video missing-required-field contract for null/empty;
    # all other malformed metadata must fail the central Drive identity boundary.
    error = "^SOURCE_REGISTRATION_(?:DRIVE_ID|LOCATOR)_INVALID$"
    if kind == "video" and bad_shape in {"null", "empty"}:
        error = "(?:^SOURCE_REGISTRATION_(?:DRIVE_ID|LOCATOR)_INVALID$|"                 "^missing required result field: driveId$)"
    with _connect(local_dsn, queries.append) as conn:
        queries.clear()
        with pytest.raises(ValueError, match=error):
            call[0](conn, call[1])
    assert queries == [], "invalid metadata ID must fail before actual writer SQL"
    assert _snapshot(local_dsn) == before
