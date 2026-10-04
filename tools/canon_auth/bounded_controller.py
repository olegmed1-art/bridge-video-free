"""Executable fixed coordinator with injected authenticated observation/dispatch channels.

No credential lookup or implicit live channel. Caller supplies existing authorized
channels; synthetic observers never become live proof. All HTTP polls are retained.
"""
from datetime import datetime, timedelta, timezone
from .launch_contract import Permit, ORIGIN, digest, require, timestamp
from .resident_preflight import Refused
from .fixed_adapter import RecoveryUnproven
from .build_once import assessment
from .vercel_validator import POSITION

PATH = "/v1/ai/positions/" + POSITION + "/teacher-evidence"


class BoundedController:
    def __init__(self, launch, api, observer, normal, recovery, clock=None, sleep=None):
        require(normal.channel_id != recovery.channel_id, "independent_recovery_channel_required")
        self.launch, self.api, self.observer = launch, api, observer
        self.normal, self.recovery = normal, recovery
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleep = sleep or __import__("time").sleep
        self.receipts, self.used, self.calls, self.attempted = [], set(), 0, False
        self.started = None
        self.last_readback = None

    def deadline(self):
        now = self.clock()
        self.launch.normal(now)
        require(self.started is not None and timedelta(0) <= now-self.started < timedelta(seconds=420),
                "controller_process_deadline")

    def correlate(self, response):
        self.deadline()
        receipt = response["receipt"]
        require(set(receipt) == {"request_id", "path", "status_code", "at"}
                and receipt["request_id"] not in self.used, "unique_receipt_required")
        require(response["status"] == receipt["status_code"], "response_status_binding_required")
        import re
        require(bool(re.fullmatch(r"[A-Za-z0-9]{3,16}-[0-9]{13}-[a-f0-9]{12}", receipt["request_id"])),
                "provider_request_id_required")
        at = timestamp(receipt["at"])
        require(self.started <= at <= self.clock(), "receipt_time_refused")
        before = self.observer.deployment(self.launch)
        require(before == {"deployment_id": self.launch.deployment_id,
                           "sha": self.launch.deployment_sha, "origin": ORIGIN, "state": "READY"},
                "deployment_binding_refused")
        records = self.observer.request(self.launch, receipt)
        # Observer queries exact requestId + deploymentId + teamId/projectId.
        # Original records are retained by that authenticated observer.
        require(isinstance(records, list) and len(records) > 0, "request_correlation_missing")
        required = {"deployment_id": self.launch.deployment_id, "request_id": receipt["request_id"],
                    "path": receipt["path"], "status_code": receipt["status_code"]}
        invocation_ids = set()
        for record in records:
            require(all(record.get(k) == v for k, v in required.items())
                    and isinstance(record.get("invocation_id"), str)
                    and bool(__import__("re").fullmatch(r"[a-f0-9]{64}", record.get("original_record_sha256", "")))
                    and self.started <= timestamp(record["at"]) <= self.clock(),
                    "request_correlation_refused")
            invocation_ids.add(record["invocation_id"])
        require(len(invocation_ids) == 1, "multiple_invocations_refused")
        require(self.observer.deployment(self.launch) == before, "deployment_changed")
        self.used.add(receipt["request_id"])
        self.receipts.append(receipt)
        return digest({"receipt": receipt, "originals": [r["original_record_sha256"] for r in records]})

    def request(self, path, body=None):
        self.deadline()
        require(self.calls < 40 and path in ("/healthz", "/v1/overview",
                "/v1/knowledge/validation/runtime-identity", PATH), "http_budget_or_path_refused")
        self.calls += 1
        response = self.api.request(path, body)
        require(response["receipt"]["path"] == path, "response_path_refused")
        proof = self.correlate(response)
        return response, proof

    def recovery_ready(self):
        self.deadline()
        proof = self.observer.recovery(self.launch, self.recovery.channel_id)
        expected = {"channel_id": self.recovery.channel_id, "program_hash": self.launch.module_hash,
            "runtime_sha": self.launch.runtime_sha, "contract_hash": self.launch.fingerprint,
            "status": "RECOVERY_READY_READ_ONLY", "independent": True, "attempt": 1,
            "event": "workflow_dispatch", "ref": "refs/heads/main", "actor": "olegmed1-art",
            "source_transport": "authenticated_github", "owned_revoke_privileges": True}
        require(all(proof.get(k) == v for k, v in expected.items())
                and type(proof.get("run_id")) is int and proof["run_id"] > 0
                and bool(__import__("re").fullmatch(r"[a-f0-9]{64}", proof.get("original_record_sha256", "")))
                and timedelta(0) <= self.clock()-timestamp(proof["observed_at"]) <= timedelta(seconds=300),
                "independent_recovery_not_ready")
        return digest(proof)

    def permit(self, stage, phase_hash):
        return Permit(self.launch.fingerprint, stage,
            {"baseline":"preflight", "initial":"baseline", "revoke":"active", "reactivate":"revoked"}[stage],
            phase_hash, self.recovery_ready(), self.clock())

    def assess(self, phase, desired, waiting, attempts):
        from tools.tournament_pilot.package import envelope
        proofs, answers = [], []
        for attempt in range(attempts):
            response, proof = self.request(PATH, envelope("3H"))
            require(response["status"] == 200, "teacher_phase_status_refused")
            observed = assessment(response["data"], "3H")
            proofs.append(proof)
            if observed == desired:
                answers.append({"call": "3H", "status": observed})
                if desired == "SUPPORTED":
                    negative, other = self.request(PATH, envelope("3S"))
                    require(negative["status"] == 200 and assessment(negative["data"], "3S") == "CONTRADICTED",
                            "negative_phase_refused")
                    proofs.append(other)
                    answers.append({"call":"3S", "status":"CONTRADICTED"})
                return digest({"phase":phase,"proofs":proofs,"answers":answers})
            require(observed == waiting, "phase_order_refused")
            if attempt + 1 < attempts:
                self.sleep(8)
        raise Refused("phase_poll_deadline")

    def stage(self, name, permit):
        receipt = self.normal.execute(name, permit)
        expected = {"baseline": ("baseline", 2, 0), "initial": ("initial", 34, 4),
                    "revoke": ("revoked", 35, 0), "reactivate": ("reactivated", 40, 4)}[name]
        require(receipt.get("status") == "STAGE_COMMITTED" and receipt.get("stage") == name
                and receipt.get("contract_hash") == self.launch.fingerprint
                and receipt.get("plan_hash") == self.launch.plan_hash
                and receipt.get("db_committed") is True
                and (receipt.get("state"), receipt.get("rows"), receipt.get("active")) == expected,
                "stage_receipt_refused")
        return receipt

    def run(self):
        # POSIX main-thread supervisor interrupts blocking normal channels.
        # Independently invoked recovery receives its own 60-second alarm.
        import signal, threading, math
        require(hasattr(signal, "SIGALRM") and threading.current_thread() is threading.main_thread(),
                "posix_main_thread_supervisor_required")
        require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), "existing_alarm_refused")
        previous = signal.getsignal(signal.SIGALRM)
        def stop(*args):
            raise Refused("controller_wall_deadline")
        signal.signal(signal.SIGALRM, stop)
        seconds = min(420, (self.launch.stage_until-self.clock()).total_seconds())
        require(seconds > 0, "stage_window_refused")
        signal.setitimer(signal.ITIMER_REAL, seconds)
        try:
            return self._run()
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)

    def _run(self):
        require(not self.attempted, "single_controller_attempt_required")
        self.launch.admit(self.clock())
        # A must be the sole immutable validation-build ID; normal channel owns
        # a durable claim tied to the controller run ID and first workflow attempt.
        self.normal.claim(self.launch)
        self.attempted, self.started = True, self.clock()
        mutation_possible = False
        try:
            self.recovery_ready()  # Before baseline or any credential-dependent write.
            behavior = self.api.behavior()
            require(behavior.get("status") == "teacher_readonly_behavior"
                    and all(behavior.get(k) is True for k in ("application_login_identity",
                        "read_privileges","owned_revoke_denied","internal_gate_execute_denied",
                        "absent_school_catalog_empty","transaction_read_only"))
                    and behavior.get("mutations") is False and behavior.get("write_admission") is False,
                    "app_behavior_refused")
            health, h = self.request("/healthz")
            require(health["status"] == 200 and health["data"] == {"status":"ok"}, "health_refused")
            overview, o = self.request("/v1/overview")
            require(overview["status"] == 200 and self.api.school_matches(overview["data"]),
                    "app_school_binding_refused")
            from tools.tournament_pilot.package import envelope
            absent, a = self.request(PATH, envelope("3H"))
            require(absent["status"] == 404 and absent["data"] == {"detail":{"code":"POSITION_NOT_FOUND"}},
                    "first_position_absence_required")
            preflight = digest({"health":h,"overview":o,"absent":a,"behavior":digest(behavior)})
            mutation_possible = True  # Includes commit-with-missing-receipt uncertainty.
            self.last_readback = self.stage("baseline", self.permit("baseline", preflight))
            baseline = self.assess("baseline", "ABSTAIN", "ABSTAIN", 10)
            self.last_readback = self.stage("initial", self.permit("initial", baseline))
            original = self.last_readback["original_expiry"]
            active = self.assess("active", "SUPPORTED", "ABSTAIN", 8)
            self.last_readback = self.stage("revoke", self.permit("revoke", active))
            revoked = self.assess("revoked", "ABSTAIN", "SUPPORTED", 8)
            self.last_readback = self.stage("reactivate", self.permit("reactivate", revoked))
            require(self.last_readback["original_expiry"] == original and self.last_readback["rows"] == 40,
                    "original_expiry_or_budget_refused")
            final = self.assess("reactivated", "SUPPORTED", "ABSTAIN", 8)
            # Reconcile every request/poll again before final acceptance.
            for receipt in self.receipts:
                require(self.observer.reverify(self.launch, receipt) is True,
                        "final_request_correlation_refused")
            self.deadline()
            final_db = self.normal.inspect(self.launch)
            require(final_db.get("status") == "READ_ONLY_PLAN"
                    and final_db.get("contract_hash") == self.launch.fingerprint
                    and final_db.get("plan_hash") == self.launch.plan_hash
                    and (final_db.get("state"), final_db.get("rows"), final_db.get("active"))
                        == ("reactivated", 40, 4)
                    and final_db.get("original_expiry") == original
                    and final_db.get("owned_outputs") ==
                        {"teacher_output":0, "search_run":0, "final_decision":0},
                    "final_owned_database_readback_refused")
            self.deadline()
            return {"status":"BOUNDED_ACCEPTANCE", "contract_hash":self.launch.fingerprint,
                "requests":self.calls,"receipts":list(self.receipts),"phase_hash":final,
                "normal_rows":40,"original_expiry":original,"evidence_class":self.observer.evidence_class}
        except BaseException:
            if mutation_possible:
                try:
                    import signal
                    signal.setitimer(signal.ITIMER_REAL, 60)
                    receipt = self.recovery.recover(self.launch)
                    require(receipt["status"] == "OWNED_REVOKE_CONFIRMED"
                            and receipt.get("contract_hash") == self.launch.fingerprint
                            and receipt.get("plan_hash") == self.launch.plan_hash and receipt["active"] == 0
                            and receipt["rows"] <= 42, "owned_recovery_readback_refused")
                except BaseException:
                    raise RecoveryUnproven("emergency_revoke_unproven") from None
            raise Refused("acceptance_stopped_owned_recovery_confirmed" if mutation_possible
                          else "acceptance_stopped_before_writes") from None
