"""Synthetic PostgreSQL source-registration concurrency and rollback regressions.

Opt-in disposable database only; reuse the WORLD engine tests' explicit endpoint.
No product connector or assertion is patched.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import os
from pathlib import Path
from queue import Queue
from threading import Event
import time
from uuid import uuid4

import psycopg
import pytest

from bridge_contracts.book_material import identity
from database.book_source_reconciliation import (
    new_book_source_id, reconcile_book_source_asset,
)
import test_book_world_postgres as world_pg
from test_book_world import reviewed_fixture, reviewed_identity_fixture

pytestmark = pytest.mark.skipif(
    os.environ.get("BOOK_WORLD_PG_TEST") != "1",
    reason="explicit disposable synthetic PostgreSQL opt-in required",
)


def _fixture(existing_alias=False):
    bundle, receipt = reviewed_fixture()
    file_id = "SyntheticConcurrency_" + uuid4().hex
    bundle["source"].update(
        locator="https://drive.google.com/file/d/" + file_id + "/view",
        edition_id=str(uuid4()), rendition_sha256=uuid4().hex + uuid4().hex,
    )
    bundle["run"]["run_id"] = str(uuid4())
    _rebind(bundle, receipt)
    school_id = str(uuid4())
    source_id = (str(uuid4()) if existing_alias else
                 new_book_source_id(school_id, bundle["source"]["locator"]))
    stored = "drive:" + file_id if existing_alias else bundle["source"]["locator"]
    with psycopg.connect(world_pg.DSN, autocommit=True) as setup:
        assert setup.execute("SELECT current_database()").fetchone()[0] == "book_world_test"
        setup.execute((Path(__file__).parent / "fixtures/book_world_postgres.sql").read_text())
        setup.execute("INSERT INTO public.school(school_id,stable_name) VALUES(%s,%s)",
                      (school_id, "synthetic-concurrency-" + uuid4().hex))
        if existing_alias:
            setup.execute(
                "INSERT INTO public.source(source_id,school_id,canonical_locator,title) "
                "VALUES(%s,%s,%s,%s)",
                (source_id, school_id, stored, bundle["source"]["title"]),
            )
    return bundle, receipt, school_id, source_id, stored


def _rebind(bundle, receipt):
    receipt["rendition_sha256"] = bundle["source"]["rendition_sha256"]
    for obj, claim in zip(bundle["objects"], receipt["claims"], strict=True):
        obj["object_id"] = "BOOK-" + identity(bundle["source"], obj)
        claim["object_id"] = obj["object_id"]


def _args(bundle, receipt, school_id, source_id, stored, register=True):
    return dict(
        school_id=school_id, source_id=source_id,
        identity_receipt=reviewed_identity_fixture(
            bundle, receipt, school_id, source_id, stored),
        register_missing_source=register,
    )


def _snapshot(school_id):
    with psycopg.connect(world_pg.DSN, autocommit=True) as read:
        result = {}
        for table in ("source", "asset", "knowledge_item", "changeset", "domain_event"):
            result[table] = read.execute(
                f"SELECT count(*) FROM public.{table} WHERE school_id=%s",
                (school_id,),
            ).fetchone()[0]
        result["links"] = read.execute(
            "SELECT count(*) FROM public.source_asset sa JOIN public.source s "
            "ON s.source_id=sa.source_id WHERE s.school_id=%s", (school_id,)
        ).fetchone()[0]
        return result


def _counts(result):
    return tuple(result[key] for key in
                 ("new_source_rows", "new_asset_rows", "new_link_rows"))


def _wait_for_blocked(waiter_pid, holder_pid):
    deadline = time.monotonic() + 3
    with psycopg.connect(world_pg.DSN, autocommit=True) as observe:
        while time.monotonic() < deadline:
            row = observe.execute(
                "SELECT pg_catalog.pg_blocking_pids(pid), wait_event_type "
                "FROM pg_catalog.pg_stat_activity WHERE pid=%s", (waiter_pid,)
            ).fetchone()
            if row and holder_pid in row[0] and row[1] == "Lock":
                return
            Event().wait(0.02)
    raise AssertionError("waiter did not observably block on the holder")


def _overlap(first, second, before_release, rollback_first=False):
    """Pause holder before commit, require observed lock wait, then release."""
    ready, release = Event(), Event()
    holder_pids, waiter_pids = Queue(), Queue()
    isolation_seen = []

    def holder():
        with psycopg.connect(world_pg.DSN) as conn:
            conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            holder_pids.put(conn.info.backend_pid)

            def pause():
                isolation_seen.append(conn.execute(
                    "SHOW transaction_isolation").fetchone()[0])
                ready.set()
                if not release.wait(8):
                    raise AssertionError("holder release was not signalled")
                if rollback_first:
                    raise RuntimeError("synthetic holder rollback")

            bundle, receipt, args = first
            return reconcile_book_source_asset(
                conn, bundle, receipt, **args, before_commit=pause)

    def waiter():
        with psycopg.connect(world_pg.DSN) as conn:
            conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            waiter_pids.put(conn.info.backend_pid)
            bundle, receipt, args = second
            return reconcile_book_source_asset(conn, bundle, receipt, **args)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(holder)
        second_future = None
        try:
            holder_pid = holder_pids.get(timeout=3)
            assert ready.wait(3), "holder did not reach before_commit"
            second_future = pool.submit(waiter)
            waiter_pid = waiter_pids.get(timeout=3)
            _wait_for_blocked(waiter_pid, holder_pid)
            before_release()
        finally:
            release.set()
        if rollback_first:
            with pytest.raises(RuntimeError, match="synthetic holder rollback"):
                first_future.result(timeout=10)
            first_result = None
        else:
            first_result = first_future.result(timeout=10)
        assert isolation_seen == ["read committed"]
        # Return the future so callers can assert a precise domain exception.
        return first_result, second_future


def _assert_final(school_id, source_id, stored, sha, expected_asset_id):
    snap = _snapshot(school_id)
    assert snap == dict(source=1, asset=1, knowledge_item=0,
                        changeset=0, domain_event=0, links=1)
    with psycopg.connect(world_pg.DSN, autocommit=True) as read:
        assert read.execute(
            "SELECT canonical_locator FROM public.source WHERE source_id=%s",
            (source_id,),
        ).fetchone() == (stored,)
        assert read.execute(
            "SELECT a.asset_id::text,a.checksum_value FROM public.source_asset sa "
            "JOIN public.asset a ON a.asset_id=sa.asset_id "
            "WHERE sa.source_id=%s AND sa.relation_type='embodies'", (source_id,)
        ).fetchall() == [(expected_asset_id, sha)]


def test_equal_concurrent_registration_commits_once_then_replays():
    bundle, receipt, school, source, stored = _fixture()
    args = _args(bundle, receipt, school, source, stored)
    call = (bundle, receipt, args)
    before = _snapshot(school)
    first, second = _overlap(call, call, lambda: _assert_snapshot(school, before))
    assert _counts(first) == (1, 1, 1)
    assert _counts(second.result(timeout=1)) == (0, 0, 0)
    _assert_final(school, source, stored, bundle["source"]["rendition_sha256"],
                  args["identity_receipt"]["asset_id"])


def _assert_snapshot(school, expected):
    assert _snapshot(school) == expected


@pytest.mark.parametrize("contender_register", [True, False],
                         ids=["registration", "existing-source-mode"])
def test_alias_x_commit_rejects_concurrent_y_without_partial_rows(contender_register):
    x, rx, school, source, stored = _fixture(existing_alias=True)
    y, ry = deepcopy(x), deepcopy(rx)
    y["source"]["rendition_sha256"] = uuid4().hex + uuid4().hex
    y["run"]["run_id"] = str(uuid4())
    _rebind(y, ry)
    ax = _args(x, rx, school, source, stored)
    ay = _args(y, ry, school, source, stored, register=contender_register)
    before = _snapshot(school)
    first, second = _overlap(
        (x, rx, ax), (y, ry, ay), lambda: _assert_snapshot(school, before))
    assert _counts(first) == (0, 1, 1)
    with pytest.raises(ValueError, match="BOOK_SOURCE_ASSET_CONFLICT"):
        second.result(timeout=1)
    _assert_final(school, source, stored, x["source"]["rendition_sha256"],
                  ax["identity_receipt"]["asset_id"])
    with psycopg.connect(world_pg.DSN, autocommit=True) as read:
        assert read.execute(
            "SELECT count(*) FROM public.asset WHERE checksum_value=%s",
            (y["source"]["rendition_sha256"],),
        ).fetchone()[0] == 0


def test_rollback_holder_unblocks_waiter_and_leaves_one_complete_registration():
    bundle, receipt, school, source, stored = _fixture()
    args = _args(bundle, receipt, school, source, stored)
    call = (bundle, receipt, args)
    before = _snapshot(school)
    first, second = _overlap(
        call, call, lambda: _assert_snapshot(school, before), rollback_first=True)
    assert first is None
    assert _counts(second.result(timeout=1)) == (1, 1, 1)
    _assert_final(school, source, stored, bundle["source"]["rendition_sha256"],
                  args["identity_receipt"]["asset_id"])


def test_dry_run_and_interruption_restore_full_identity_snapshot():
    bundle, receipt, school, source, stored = _fixture()
    args = _args(bundle, receipt, school, source, stored)
    before = _snapshot(school)
    assert before == dict(source=0, asset=0, knowledge_item=0,
                          changeset=0, domain_event=0, links=0)
    observed = []

    def inspect_pending():
        # A different connection sees no source, asset, link, or audit writes.
        _assert_snapshot(school, before)
        observed.append("pending invisible")

    def interrupt():
        inspect_pending()
        raise RuntimeError("synthetic independent interruption")

    with psycopg.connect(world_pg.DSN) as conn:
        dry = reconcile_book_source_asset(
            conn, bundle, receipt, **args, dry_run=True,
            before_commit=inspect_pending)
        assert _counts(dry) == (1, 1, 1)
        assert dry["dry_run"] is True and dry["committed"] is False
        _assert_snapshot(school, before)
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
        with pytest.raises(RuntimeError, match="synthetic independent interruption"):
            reconcile_book_source_asset(
                conn, bundle, receipt, **args, before_commit=interrupt)
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
    _assert_snapshot(school, before)
    assert observed == ["pending invisible", "pending invisible"]
