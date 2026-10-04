"""Fixed SQL stages on an already verified dedicated owner connection.

No env/credentials/role changes, caller SQL, migrations or workflow dispatch.
All42 IDs are inventoried inside the original transaction/advisory lock.
"""
from datetime import datetime, timezone
from .pilot_sql import plan
from .launch_contract import PHASES, STAGES, digest, require
from .resident_preflight import Refused, idle, catalog_path
from .ownership import inventory

PREVIOUS = {"baseline": "absent", "initial": "baseline",
            "revoke": "initial", "reactivate": "revoked"}
NEXT = {"baseline": "baseline", "initial": "initial",
        "revoke": "revoked", "reactivate": "reactivated"}


class CommitUncertain(Refused):
    """Never imply rollback after uncertain commit or a missing external receipt."""
    pass


class RecoveryUnproven(Refused):
    pass


def compile_plan(school, code_sha):
    compiled = plan(school, code_sha)
    fingerprint = digest({key: compiled[key] for key in STAGES})
    return compiled, fingerprint


class FixedAdapter:
    def __init__(self, conn, school, launch, source_check, clock=None):
        idle(conn)
        self.conn, self.school, self.launch = conn, school, launch
        self.source_check = source_check
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.compiled, fingerprint = compile_plan(school, launch.code_sha)
        require(fingerprint == launch.plan_hash, "original_plan_hash_required")

    def _begin(self, readonly=False):
        self.conn.execute("SET TRANSACTION ISOLATION LEVEL " +
                          ("REPEATABLE READ READ ONLY" if readonly else "READ COMMITTED READ WRITE"))
        self.conn.execute("SET LOCAL statement_timeout='15s'")
        self.conn.execute("SET LOCAL lock_timeout='2s'")
        catalog_path(self.conn)
        self.conn.execute("SELECT pg_catalog.pg_advisory_xact_lock(20261004,201)")

    def _write_path(self):
        # Existing immutable source-scope trigger resolves public tables by
        # unqualified names. Permit only that trusted schema AFTER pg_catalog;
        # explicit pg_temp last prevents temporary-table shadowing.
        trusted = self.conn.execute("""
            SELECT NOT EXISTS (
                SELECT 1 FROM pg_catalog.pg_namespace n,
                     LATERAL pg_catalog.aclexplode(COALESCE(n.nspacl,
                         pg_catalog.acldefault('n',n.nspowner))) a
                WHERE n.nspname='public' AND a.privilege_type='CREATE'
                  AND a.grantee NOT IN (n.nspowner,
                      (SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user))
            ) AND pg_catalog.pg_my_temp_schema()=0
        """).fetchone()
        require(trusted == (True,), "trusted_trigger_schema_required")
        self.conn.execute("SET LOCAL search_path=pg_catalog,public,pg_temp")
        require(self.conn.execute("SELECT pg_catalog.current_setting('search_path')").fetchone()
                == ("pg_catalog, public, pg_temp",), "trusted_trigger_path_required")

    def inspect(self):
        idle(self.conn)
        self.source_check()
        with self.conn.transaction(force_rollback=True):
            self._begin(readonly=True)
            # FOR UPDATE isn't permitted in READ ONLY; read-only readiness uses
            # the same snapshot but no row locks. Mutations recheck with locks.
            state = inventory(self.conn, self.compiled, self.school, lock=False)
            zero = {table: self.conn.execute("SELECT count(*) FROM ai." + table +
                " WHERE position_id=%s", (self.compiled["ids"]["position"],)).fetchone()[0]
                for table in ("teacher_output", "search_run", "final_decision")}
        self.source_check()
        return {"status": "READ_ONLY_PLAN", "contract_hash": self.launch.fingerprint,
                "plan_hash": self.launch.plan_hash, **state, "db_committed": False, "owned_outputs": zero}

    def execute(self, stage, permit):
        require(stage in PHASES, "fixed_stage_required")
        idle(self.conn)
        self.launch.normal(self.clock())
        permit.verify(self.launch, stage, self.clock())
        self.source_check()
        commit_started = False
        try:
            with self.conn.transaction():
                self._begin()
                # Deadline/permit checked AGAIN after any lock wait.
                self.launch.normal(self.clock())
                permit.verify(self.launch, stage, self.clock())
                school = self.conn.execute("SELECT status FROM public.school WHERE school_id=%s FOR SHARE",
                                           (self.school,)).fetchone()
                require(school == ("active",), "active_school_required")
                before = inventory(self.conn, self.compiled, self.school)
                require(before["state"] == PREVIOUS[stage], "duplicate_or_out_of_order_stage")
                self.launch.normal(self.clock())
                permit.verify(self.launch, stage, self.clock())
                self._write_path()
                for sql in self.compiled[stage]:
                    self.conn.execute(sql)
                after = inventory(self.conn, self.compiled, self.school)
                require(after["state"] == NEXT[stage] and after["rows"] <= 40,
                        "stage_postcondition_refused")
                commit_started = True
            self.source_check()
        except Refused:
            if commit_started:
                raise CommitUncertain("committed_external_evidence_missing") from None
            raise
        except BaseException:
            raise CommitUncertain("database_commit_or_transport_unconfirmed") from None
        return {"status": "STAGE_COMMITTED", "stage": stage,
                "contract_hash": self.launch.fingerprint, "plan_hash": self.launch.plan_hash,
                "db_committed": True, **after}

    def recover(self):
        """Independent invocation; normal window/alias/phase permit do not gate revoke."""
        try:
            idle(self.conn)
            self.source_check()
            with self.conn.transaction():
                self._begin()
                before = inventory(self.conn, self.compiled, self.school)
                if before["state"] == "absent":
                    after = before
                else:
                    self._write_path()
                    for sql in self.compiled["emergency"]:
                        self.conn.execute(sql)
                    after = inventory(self.conn, self.compiled, self.school)
                    require(after["state"] == "emergency" and after["active"] == 0
                            and after["rows"] <= 42, "owned_revoke_readback_required")
            # A separate transaction confirms persisted state after commit.
            with self.conn.transaction(force_rollback=True):
                self._begin(readonly=True)
                persisted = inventory(self.conn, self.compiled, self.school, lock=False)
            require(persisted == after, "owned_revoke_readback_required")
            self.source_check()
            return {"status": "OWNED_REVOKE_CONFIRMED", "plan_hash": self.launch.plan_hash,
                    "contract_hash": self.launch.fingerprint, "db_committed": before["state"] != "absent",
                    "no_op": before["state"] == "absent", **after}
        except BaseException:
            raise RecoveryUnproven("emergency_revoke_unproven") from None
