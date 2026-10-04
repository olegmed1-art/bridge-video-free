"""Execute the exact staged SQL compiler only in the fixed disposable DB."""
import json
from uuid import uuid4
import psycopg

from bridge_school_api import tournament_teacher as t
from tools.tournament_pilot.rehearsal import local_connect, scalar, api_client, DB, TABLE_BUDGET
from tools.tournament_pilot.package import envelope
from .pilot_sql import plan


def rehearsal(code_sha):
    with local_connect() as conn:
        assert scalar(conn, "SELECT current_database()") == DB
        school = scalar(conn, "INSERT INTO public.school(stable_name) VALUES (%s) RETURNING school_id",
                        ("synthetic-staged-sql-" + str(uuid4()),))
        p = plan(school, code_sha)
        initial_counts = {table: scalar(conn, "SELECT count(*) FROM " + table) for table in TABLE_BUDGET}
        outputs = scalar(conn, "SELECT count(*) FROM ai.teacher_output")
        def execute(stage):
            with conn.transaction():
                for sql in p[stage]:
                    conn.execute(sql)
        def total():
            return {table: scalar(conn, "SELECT count(*) FROM " + table) - initial_counts[table] for table in TABLE_BUDGET}
        def prove_emergency_terminal(next_stage):
            # Disposable-only rollback of the test fixture, never operational SQL.
            with conn.transaction(force_rollback=True):
                execute("emergency")
                try:
                    execute(next_stage)
                except psycopg.Error as exc:
                    assert "PILOT_EMERGENCY_TERMINAL" in str(exc)
                else:
                    raise AssertionError("Emergency stop was not terminal")
        path = f"/v1/ai/positions/{p['ids']['position']}/teacher-evidence"
        observed = []
        with api_client() as client:
            def assess(call="3H"):
                response = client.post(path, json=envelope(call))
                assert response.status_code == 200, response.text
                result = response.json()
                assert all(result[k] is False for k in ("persisted", "queued", "finalized"))
                assert result["action"] is None
                return result
            assert client.post(path, json=envelope("3H")).status_code == 404
            execute("baseline")
            assert sum(total().values()) == 2
            observed.append(assess()["status"])
            assert observed[-1] == "ABSTAIN"
            prove_emergency_terminal("initial")
            # Duplicate and out-of-order stages cannot extend or partially import.
            for stage in ("baseline", "reactivate", "revoke"):
                try:
                    execute(stage)
                except psycopg.Error:
                    pass
                else:
                    raise AssertionError("Unexpected stage accepted: " + stage)
            assert sum(total().values()) == 2
            execute("initial")
            assert sum(total().values()) == 34
            observed.append(assess()["status"])
            assert observed[-1] == "SUPPORTED"
            assert assess("3S")["status"] == "CONTRADICTED"
            source = assess()["source_bindings"][0]
            assert source["decision_ids"] == ["TDEC-20261003-002"]
            expiry = scalar(conn, "SELECT metadata->>'original_expiry' FROM bidding.ingestion_run WHERE ingestion_run_id=%s", (p["ids"]["run"],))
            execute("revoke")
            assert sum(total().values()) == 35
            observed.append(assess()["status"])
            assert observed[-1] == "ABSTAIN"
            prove_emergency_terminal("reactivate")
            execute("reactivate")
            observed.append(assess()["status"])
            assert observed[-1] == "SUPPORTED"
            assert assess("3S")["status"] == "CONTRADICTED"
            assert total() == TABLE_BUDGET
            active_expiries = conn.execute("SELECT valid_to=%s::timestamptz FROM bidding.runtime_activation WHERE school_id=%s AND status='active'", (expiry, school)).fetchall()
            assert active_expiries == [(True,), (True,)]
            for stage in ("initial", "reactivate", "revoke"):
                try:
                    execute(stage)
                except psycopg.Error:
                    pass
                else:
                    raise AssertionError("Duplicate stage accepted: " + stage)
            assert sum(total().values()) == 40
            execute("emergency")
            assert assess()["status"] == "ABSTAIN"
            assert sum(total().values()) == 42
            execute("emergency")
            assert sum(total().values()) == 42
            assert assess()["status"] == "ABSTAIN"
        assert scalar(conn, "SELECT count(*) FROM ai.teacher_output") == outputs
        assert scalar(conn, "SELECT count(*) FROM ai.search_run WHERE position_id=%s", (p["ids"]["position"],)) == 0
        assert scalar(conn, "SELECT count(*) FROM ai.final_decision WHERE position_id=%s", (p["ids"]["position"],)) == 0
        return {"status": "PASS", "code_sha": code_sha, "staged_http": observed, "normal_rows": 40,
                "emergency_rows": 42, "emergency_repeat_rows": 42, "semantic_cases": len(p["semantic_cases"]),
                "unchanged_expiry": True, "teacher_output_writes": 0, "search_runs": 0, "final_decisions": 0,
                "duplicate_stages_rejected": True, "extra_disposable_school_fixture": 1}


if __name__ == "__main__":
    import subprocess
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    print(json.dumps(rehearsal(sha), indent=2))
