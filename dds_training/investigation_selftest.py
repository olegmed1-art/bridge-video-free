from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from investigations import (
    ensure_investigation_table,
    open_investigations,
    reopen_investigation,
    resolve_investigation,
    sync_required_investigations,
)
from storage import connect, upsert_prediction, upsert_result


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        con = connect(Path(td) / "training.sqlite3")
        ensure_investigation_table(con)
        task = {
            "task_id": "INV-1",
            "deal_id": "INV-DEAL",
            "task_type": "contract_tricks",
            "split": "train",
        }
        prediction = {"task_id": "INV-1", "tricks": 11, "confidence": "high", "locked": True}
        result = {
            "dds_tricks": 10,
            "predicted_tricks": 11,
            "delta_pred_minus_dds": 1,
            "prediction_error": 1,
            "dd_regret": None,
            "investigation_required": True,
            "error_code": "D_OVER_DDS_CLAIM",
        }
        upsert_prediction(con, task, prediction)
        upsert_result(con, task, result)
        con.commit()

        first = sync_required_investigations(con, "selftest")
        second = sync_required_investigations(con, "selftest")
        assert first["opened_now"] == 1 and first["open_total"] == 1
        assert second["opened_now"] == 0 and second["open_total"] == 1

        resolve_investigation(
            con,
            task_id="INV-1",
            cause="The proposed line assumes a defensive continuation that is not forced.",
            first_refutation="Optimal defense switches suit at the first critical decision.",
            lesson="Before claiming more than DDS, explicitly test the best defensive switch.",
            run_id="selftest",
        )
        assert open_investigations(con) == []
        assert sync_required_investigations(con, "selftest")["open_total"] == 0

        reopen_investigation(con, task_id="INV-1", reason="New line evidence requires review", run_id="selftest")
        assert len(open_investigations(con)) == 1
        resolve_investigation(
            con,
            task_id="INV-1",
            cause="Rechecked cause",
            first_refutation="Rechecked first refutation",
            lesson="Rechecked lesson",
            run_id="selftest",
        )
        assert not open_investigations(con)

        try:
            con.execute("UPDATE investigation_events SET event_type='opened' WHERE task_id='INV-1'")
        except sqlite3.IntegrityError:
            immutable = True
        else:
            immutable = False
        assert immutable

        print(json.dumps({
            "ok": True,
            "sync_idempotent": True,
            "resolution_required_fields": True,
            "reopen_supported": True,
            "append_only": True,
            "events": con.execute("SELECT COUNT(*) FROM investigation_events").fetchone()[0],
        }, indent=2))


if __name__ == "__main__":
    main()
