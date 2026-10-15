"""Runtime checks for 0060 on the disposable PostgreSQL 18 CI database.

The fixture never accepts an arbitrary DSN and makes no external service calls.
042 exercises an admitted canary with the actual capability roles; 043 evaluates
the installed terminal-size constraint at its documented boundaries.
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


ROLES = frozenset({
    "video_subset_approver", "video_subset_collector",
    "video_subset_admitter", "video_subset_worker_v2",
})
PROFILE = "bridge_3_1_free"
REVISION = "3.1-free-r25.16"


def _admin_dsn() -> str:
    admin = os.environ.get("ADMIN_DATABASE_URL", "")
    owner = os.environ.get("DATABASE_URL", "")
    a, o = urlsplit(admin), urlsplit(owner)
    if not (
        a.scheme in ("postgres", "postgresql")
        and o.scheme in ("postgres", "postgresql")
        and a.hostname in ("localhost", "127.0.0.1")
        and o.hostname in ("localhost", "127.0.0.1")
        and a.port == o.port == 5432
        and a.path == o.path
        and a.path in ("/bridge_school_ci", "/bridge_school_preflight")
        and not (a.query or o.query or a.fragment or o.fragment)
        and a.username == "postgres"
        and o.username == "bridge_ci_owner"
    ):
        raise RuntimeError("DISPOSABLE_POSTGRES_TARGET_REQUIRED")
    return admin


def _connect(role: str | None = None):
    db = psycopg.connect(_admin_dsn(), connect_timeout=5, row_factory=dict_row)
    if role is not None:
        if role not in ROLES:
            db.close()
            raise ValueError("unknown synthetic capability")
        # The local superuser assumes an existing NOLOGIN capability inside its
        # session; session_user remains the tested capability in definer calls.
        db.execute(f"SET SESSION AUTHORIZATION {role}")
    return db


def _sha(db, value) -> str:
    return db.execute("SELECT video_queue.subset_sha(%s::jsonb) AS digest",
                      (Jsonb(value),)).fetchone()["digest"]


def _canonical(db, value) -> str:
    return db.execute("SELECT video_queue.subset_json(%s::jsonb) AS body",
                      (Jsonb(value),)).fetchone()["body"]


def _admit_two_file_canary():
    suffix = uuid4().hex[:12]
    folder = "source_folder_" + suffix
    output_folder = "output_folder_" + suffix
    source_rows = []
    observed = []
    queue_files = []
    with _connect() as db:
        for sequence in (1, 2):
            file_id = f"synthetic_video_{suffix}_{sequence:02d}"
            revision = dict(
                source_identity="google_drive:" + file_id, file_id=file_id,
                parent=folder, version="1",
                modifiedTime="2026-10-15T00:00:00Z", mime_type="video/mp4",
                size_bytes=1048576, checksum=None, checksums={},
            )
            source_rows.append(dict(
                revision, name=f"Synthetic {sequence}.mp4", trashed=False,
                source_fingerprint=_sha(db, revision), duration_ms=None,
                estimated_cost_usd=None,
            ))
            observed.append(dict(
                id=file_id, parents=[folder], name=f"Synthetic {sequence}.mp4",
                version="1", modifiedTime="2026-10-15T00:00:00Z",
                mimeType="video/mp4", size="1048576", trashed=False,
            ))
            queue_files.append(dict(
                sequence=sequence, file_id=file_id,
                name=f"Synthetic {sequence}.mp4", mime_type="video/mp4",
                size_bytes=1048576, checksum=None,
            ))
        manifest = dict(
            source_folder_id=folder, output_folder_id=output_folder,
            work_folder_id="work_folder_" + suffix, processing_profile=PROFILE,
            algorithm_revision=REVISION,
            canary_source_file_id=queue_files[0]["file_id"],
            inventory_sha256=_sha(db, queue_files), expected_count=2,
            total_size_bytes=2097152, files=queue_files,
            result_mode="SHADOW_REVIEW_ONLY",
            canonical_promotion_allowed=False, database_persistence_allowed=False,
        )
        revision_key = dict(
            plan_sha256="a" * 64, subset_sha256=_sha(db, source_rows),
            subset_sequence=1, processing_profile=PROFILE,
            algorithm_revision=REVISION,
            canary_source_file_id=queue_files[0]["file_id"],
        )
        request_key = "subset-v1:" + _sha(db, revision_key)
        manifest["request_key"] = request_key
        contract = dict(
            schema="universal-video-subset-contract-v1", **revision_key,
            files=source_rows, enqueue_manifest=manifest,
            queue_manifest_sha256=_sha(db, manifest), execution_allowed=False,
            release_allowed=False, database_persistence_allowed=False,
            canonical_promotion_allowed=False,
        )
        contract["contract_sha256"] = _sha(db, contract)
        contract_bytes = _canonical(db, contract)

    approval_id, observation_id = uuid4(), uuid4()
    with _connect("video_subset_approver") as db:
        db.execute(
            "SELECT video_queue.approve_subset(%s,%s,%s,%s)",
            (approval_id, contract_bytes, Jsonb(dict(
                approved_by="disposable-ci",
                approval_reference="runtime-042",
                plan_sha256="a" * 64,
            )), datetime.now(timezone.utc) + timedelta(minutes=20)),
        )
        db.execute("SELECT video_queue.set_subset_runtime(%s,true)", (approval_id,))
    with _connect("video_subset_collector") as db:
        db.execute(
            "SELECT video_queue.observe_subset(%s,%s,'ADMISSION',%s,%s,%s)",
            (observation_id, approval_id, Jsonb(observed),
             datetime.now(timezone.utc), uuid4()),
        )
    with _connect("video_subset_admitter") as db:
        result = db.execute(
            "SELECT video_queue.admit_approved_subset(%s,%s,%s,%s,%s) AS admitted",
            (approval_id, request_key, contract["contract_sha256"],
             contract["queue_manifest_sha256"], observation_id),
        ).fetchone()["admitted"]
    assert result["status"] == "ADMITTED", result
    return approval_id, result["binding"]["batch_id"], queue_files


def _expect_error(role: str, expected: str, sql: str, params: tuple):
    with _connect(role) as db:
        try:
            db.execute(sql, params).fetchall()
        except psycopg.Error as exc:
            db.rollback()
            assert expected in str(exc), str(exc)
        else:
            raise AssertionError(f"expected {expected}")


def scenario_042():
    approval_id, batch_id, files = _admit_two_file_canary()
    with _connect("video_subset_worker_v2") as db:
        claim = db.execute(
            "SELECT * FROM video_queue.subset_claim_job(%s,%s,%s,%s,%s)",
            (batch_id, "fixture-worker", 900, PROFILE, REVISION),
        ).fetchone()
        assert claim and claim["is_canary"] and claim["source_file_id"] == files[0]["file_id"]
        assert db.execute(
            "SELECT * FROM video_queue.subset_claim_job(%s,%s,%s,%s,%s)",
            (batch_id, "fixture-worker", 900, PROFILE, REVISION),
        ).fetchone() is None

    job_id, lease_token = claim["job_id"], claim["lease_token"]
    finish = "SELECT * FROM video_queue.finish_subset_job(%s,%s,%s,%s,%s,%s,%s)"
    _expect_error(
        "video_subset_worker_v2", "SUBSET_LEASE_LOST", finish,
        (job_id, uuid4(), "fixture-worker", "REVIEW_READY", Jsonb({}), uuid4(), None),
    )
    _expect_error(
        "video_subset_worker_v2", "SUBSET_PREWORK_OBSERVATION_REQUIRED", finish,
        (job_id, lease_token, "fixture-worker", "REVIEW_READY",
         Jsonb({}), uuid4(), None),
    )
    with _connect() as db:
        state = db.execute(
            "SELECT status FROM video_queue.job WHERE batch_id=%s ORDER BY sequence",
            (batch_id,),
        ).fetchall()
        assert [row["status"] for row in state] == ["LEASED", "PENDING_CANARY"], state
    with _connect("video_subset_approver") as db:
        db.execute("SELECT video_queue.revoke_subset_approval(%s)", (approval_id,))
    _expect_error(
        "video_subset_worker_v2", "SUBSET_RUNTIME_HOLD",
        "SELECT video_queue.authorize_subset_attempt(%s,%s,%s,%s)",
        (job_id, lease_token, "fixture-worker", uuid4()),
    )
    with _connect() as db:
        state = db.execute(
            "SELECT status FROM video_queue.job WHERE batch_id=%s ORDER BY sequence",
            (batch_id,),
        ).fetchall()
        assert [row["status"] for row in state] == ["LEASED", "PENDING_CANARY"], state
    print("FIXED_SUBSET_CANARY_DENIAL_PASS")


def scenario_043():
    with _connect() as db:
        row = db.execute(
            """SELECT pg_get_expr(c.conbin,c.conrelid) AS expression,c.convalidated
               FROM pg_constraint c
               WHERE c.conrelid='video_queue.job'::regclass
                 AND c.conname='video_job_terminal_size_check'
                 AND c.contype='c'"""
        ).fetchone()
        assert row and row["convalidated"] is False
        expression = row["expression"]
        cases = (
            ("REVIEW_READY", 1, 1, True),
            ("REVIEW_READY", 536870912, 8388608, True),
            ("REVIEW_READY", 536870913, 1024, False),
            ("REVIEW_READY", 2048, 8388609, False),
            ("REVIEW_READY", 1.5, 1024, False),
            ("REVIEW_READY", 2048, 1.5, False),
            ("REVIEW_READY", None, 1024, False),
            ("FAILED", None, None, True),
        )
        for status, pdf_size, ai_size, expected in cases:
            output = dict(artifact_manifest=dict(artifacts=[
                dict(size_bytes=pdf_size), dict(size_bytes=ai_size),
            ]))
            actual = db.execute(
                f"SELECT ({expression}) IS TRUE AS allowed "
                "FROM (SELECT %s::text AS status,%s::jsonb AS output) AS job",
                (status, Jsonb(output)),
            ).fetchone()["allowed"]
            assert actual is expected, (status, pdf_size, ai_size, actual)
    print("FIXED_SUBSET_TERMINAL_SIZE_PASS")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=("042", "043"), required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        raise RuntimeError("EXPLICIT_DISPOSABLE_EXECUTION_REQUIRED")
    _admin_dsn()
    if args.scenario == "042":
        scenario_042()
    else:
        scenario_043()


if __name__ == "__main__":
    main()
