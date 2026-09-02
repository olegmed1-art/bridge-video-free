#!/usr/bin/env python3
from __future__ import annotations

from bridge_worker_3_1_free import stable_job_id
from database.backfill_video_results import _verify_master_identity


def expect_reject(master: dict, job_id: str) -> None:
    try:
        _verify_master_identity(master, job_id)
    except RuntimeError:
        return
    raise AssertionError("expected backfill identity rejection")


def main() -> None:
    source_id = "synthetic-drive-file-id"
    job_id = stable_job_id("drive", source_id)
    master = {"job_id": job_id, "source": {"driveId": source_id}}
    _verify_master_identity(master, job_id)

    expect_reject({"job_id": "0" * 32, "source": {"driveId": source_id}}, job_id)
    expect_reject({"job_id": job_id, "source": {}}, job_id)
    expect_reject({"job_id": job_id, "source": {"driveId": "different-file"}}, job_id)

    print("BACKFILL_IDENTITY: PASS")


if __name__ == "__main__":
    main()
