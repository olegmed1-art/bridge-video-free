"""Bounded in-memory teaching session over teaching_step and its existing graph.

No catalog/profile projection, global selector replacement, database, network or
prose grader. Selection is provisional formative routing, not skill readiness.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from . import teaching_step as steps

VERSION = "local-teacher-routing-v1"
POLICY = Path(__file__).resolve().parents[1] / "data/research/local_teacher_routing_policy_v1.json"
POLICY_SHA256 = "871526f3a68d00c02a65d6ecf957c55657b7abb77883fac3384172ec8e431249"
BOUNDARY = {"persisted": False, "student_profile_written": False,
            "curriculum_activated": False, "mastery_event_created": False,
            "actual_mastery": None, "criterion_met": None,
            "learner_error": None,
            "explanation_review": "HUMAN_REVIEW_REQUIRED"}


class TeacherRoutingError(ValueError):
    pass


def _digest(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TeacherRoutingError("finite JSON command required") from exc
    if len(raw) > 24000:
        raise TeacherRoutingError("command exceeds local size bound")
    return hashlib.sha256(raw).hexdigest()


class TeacherLesson:
    """Private teacher-side object: only current()/command receipts are public.

    record_response ACKs without correctness or a result-dependent route.
    A learner can revise before requesting hint/advance. advance is the explicit
    feedback-release boundary; a new issuance cannot erase prior help/exposure.
    IDs are local bindings, not authenticated identities. Process loss loses the
    journal. A future durable service must preserve the same separation.
    """

    def __init__(self, session_id):
        if type(session_id) is not str or not 1 <= len(session_id) <= 120:
            raise TeacherRoutingError("local session id required")
        raw = POLICY.read_bytes()
        if hashlib.sha256(raw).hexdigest() != POLICY_SHA256:
            raise TeacherRoutingError("routing policy changed; review and repin required")
        self._policy = json.loads(raw)
        self._graph = steps.candidate_dependencies()["nodes"]
        if self._policy["source_graph"]["fixture_sha256"] != steps.FIXTURE_SHA256:
            raise TeacherRoutingError("policy bound to another skill graph")
        self._nodes = {n["id"]: n for n in self._graph}
        if len(self._nodes) != len(self._graph):
            raise TeacherRoutingError("duplicate source graph node")
        order, active = [], set()

        def visit(node):
            if node in active or node not in self._nodes:
                raise TeacherRoutingError("invalid source dependency graph")
            if node in order:
                return
            active.add(node)
            for parent in sorted(self._nodes[node]["requires"]):
                visit(parent)
            active.remove(node)
            order.append(node)

        for node in sorted(self._nodes):
            visit(node)
        self._rank = {node: i for i, node in enumerate(order)}
        for key in ("criterion_skill", "activity_focus"):
            if not set(self._policy[key].values()) <= set(self._nodes):
                raise TeacherRoutingError("policy references unknown graph node")
        self._session_id = session_id
        self._receipts = {}
        self._runs = []
        self._help = []
        self._journal = []
        self._current = None
        self._source_hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                               for path in (POLICY, steps.FIXTURE, steps.COUNTEREX_FIXTURE,
                                            Path(steps.__file__), Path(__file__))}
        for binding in self._policy["pedagogical_basis"]["existing_video_context"]["source_bindings"]:
            path = Path(__file__).resolve().parents[1] / binding["path"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != binding["sha256"]:
                raise TeacherRoutingError("historical context source changed; reconcile before use")
            self._source_hashes[str(path)] = binding["sha256"]
        for sid in [self._policy["initial_step"], *self._policy["remaining_order"]]:
            prompt = steps.student_view(sid)
            if any(q["id"] not in self._policy["criterion_skill"] for q in prompt["presentation"]["questions"]):
                raise TeacherRoutingError("question has no source-graph mapping")
        self._issue(self._policy["initial_step"], "FIRST_RESPONSE_OPPORTUNITY")

    def _fresh(self):
        if any(hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest
               for path, digest in self._source_hashes.items()):
            raise TeacherRoutingError("session source changed; open a reviewed new session")

    def source_bindings(self):
        """Portable source pins for an external journal; no session state export."""
        self._fresh()
        root = Path(__file__).resolve().parents[1]
        return {Path(path).resolve().relative_to(root).as_posix(): digest
                for path, digest in sorted(self._source_hashes.items())}

    def _issue(self, sid, reason):
        ordinal = len(self._runs) + 1
        seen = any(r["public"]["prompt"]["exercise_id"] == sid for r in self._runs)
        public = {"session_id": self._session_id, "issuance_id": f"{self._session_id}:{ordinal}",
                  "ordinal": ordinal, "method_version": VERSION, "policy_sha256": POLICY_SHA256,
                  "prompt": steps.student_view(sid), "reason": reason,
                  "content_origin": "AUTHORED_EXERCISE_NOT_VIDEO_EPISODE",
                  "context_exposure": "REPEATED" if seen else "NEW_IN_LOCAL_SESSION",
                  "support_history": "PRIOR_LOCAL_SUPPORT" if self._help else "NO_LOCAL_SUPPORT_RECORDED",
                  "opportunity": "TRANSFER_PROBE" if sid == "retry.r1" and not seen else "FORMATIVE_PRACTICE",
                  "external_experience_and_help": "UNKNOWN", **BOUNDARY}
        self._current = {"public": public, "responses": [], "local_help": [], "closed": False}
        self._runs.append(self._current)
        self._journal.append({"kind": "ISSUED", "issuance": deepcopy(public)})

    def current(self):
        self._fresh()
        return deepcopy(self._current["public"]) if self._current else {
            "status": "AWAIT_TEACHER_REVIEW", "remaining_authored_activity": None, **BOUNDARY}

    def _begin(self, kind, command_id, issuance_id, payload):
        self._fresh()
        if type(command_id) is not str or not 1 <= len(command_id) <= 120:
            raise TeacherRoutingError("bounded command id required")
        digest = _digest({"kind": kind, "issuance_id": issuance_id, "payload": payload})
        if command_id in self._receipts:
            old = self._receipts[command_id]
            if old["sha256"] != digest:
                raise TeacherRoutingError("conflicting command replay")
            return digest, deepcopy(old["result"])
        if len(self._receipts) >= 128:
            raise TeacherRoutingError("local session command bound reached")
        if self._current is None or issuance_id != self._current["public"]["issuance_id"]:
            raise TeacherRoutingError("response/action is not for the current issuance")
        return digest, None

    def _save(self, command_id, digest, result):
        self._receipts[command_id] = {"sha256": digest, "result": deepcopy(result)}
        return deepcopy(result)

    def record_response(self, *, command_id, issuance_id, response):
        digest, old = self._begin("RESPONSE", command_id, issuance_id, response)
        if old is not None:
            return old
        run = self._current
        if len(run["responses"]) >= self._policy["max_responses_per_issuance"]:
            raise TeacherRoutingError("response bound reached; request feedback/review")
        sid = run["public"]["prompt"]["exercise_id"]
        if type(response) is dict and (
                "exercise_id" in response and response["exercise_id"] != sid
                or "prompt_sha256" in response and response["prompt_sha256"] != run["public"]["prompt"]["prompt_sha256"]):
            raise TeacherRoutingError("stale or wrong prompt response")
        # Unsupported/free text/'не знаю' is still retained as UNASSESSED.
        assessment = steps.assess_response(sid, response)
        record = {"command_id": command_id, "raw_response": deepcopy(response),
                  "response_sha256": _digest(response), "assessment": assessment,
                  "assessment_status": "ABSTAIN" if assessment["status"] in {"UNASSESSED", "NEEDS_CLARIFICATION"} else "PARTIAL",
                  "local_help_before_response": deepcopy(run["local_help"]),
                  "prior_session_help_count": len(self._help)}
        run["responses"].append(record)
        self._journal.append({"kind": "RESPONSE_RECORDED", "issuance_id": issuance_id, **deepcopy(record)})
        result = {"status": "RECORDED_PENDING_FEEDBACK", "issuance_id": issuance_id,
                  "response_no": len(run["responses"]), "response_sha256": record["response_sha256"],
                  "message": "Ответ сохранён. До обратной связи можно самостоятельно перепроверить и прислать новую версию.",
                  **BOUNDARY}
        return self._save(command_id, digest, result)

    def _focus(self, run):
        sid = run["public"]["prompt"]["exercise_id"]
        last = run["responses"][-1]["assessment"] if run["responses"] else None
        failed = [self._policy["criterion_skill"][c["criterion_id"]]
                  for c in (last or {}).get("criteria", []) if c["status"] == "FAIL"]
        if failed:
            return min(failed, key=lambda node: self._rank[node])
        shown_hints = [h for h in run["local_help"] if h["kind"] == "HINT_SHOWN"]
        return shown_hints[-1]["skill_focus"] if shown_hints else self._policy["activity_focus"][sid]

    def _support(self, kind, focus):
        entry = {"kind": kind, "issuance_id": self._current["public"]["issuance_id"], "skill_focus": focus}
        self._help.append(entry)
        self._current["local_help"].append(deepcopy(entry))
        self._journal.append(deepcopy(entry))

    def request_hint(self, *, command_id, issuance_id):
        digest, old = self._begin("HINT", command_id, issuance_id, None)
        if old is not None:
            return old
        focus = self._focus(self._current)
        self._support("HINT_SHOWN", focus)
        result = {"status": "HINT_SHOWN", "skill_focus": focus,
                  "hint": self._policy["hints_by_skill"][focus],
                  "support_history": "GUIDED", **BOUNDARY}
        return self._save(command_id, digest, result)

    def _choose(self, run, matched, focus):
        sid = run["public"]["prompt"]["exercise_id"]
        count = lambda target: sum(r["public"]["prompt"]["exercise_id"] == target for r in self._runs)
        limit = self._policy["max_issuances_per_step"]
        # A focus is a routing hypothesis, never a diagnosis or skill state.
        if not matched or any(h["kind"] == "HINT_SHOWN" for h in run["local_help"]):
            target = self._policy["counterexample_by_skill"][focus]
            prior_matches = any(r.get("typed_match") is True and r["public"]["prompt"]["exercise_id"] == target for r in self._runs)
            if target != sid and not prior_matches and count(target) < limit:
                return target, "TARGETED_COUNTEREXAMPLE"
            if not matched:
                return (sid, "GUIDED_SAME_CONTEXT_RETRY") if count(sid) < limit else (None, "REPEATED_DIFFICULTY_TEACHER_REVIEW")
        for target in self._policy["remaining_order"]:
            if count(target) == 0:
                return target, "NEW_AUTHORED_CONTEXT"
        return None, "AUTHORED_SET_EXHAUSTED_TEACHER_REVIEW"

    def advance(self, *, command_id, issuance_id):
        """Release feedback and choose once. This ends the uncued revision window."""
        digest, old = self._begin("ADVANCE", command_id, issuance_id, None)
        if old is not None:
            return old
        run = self._current
        if not run["responses"]:
            return self._save(command_id, digest, {"status": "WAIT_FIRST_RESPONSE", "assessment_status": "ABSTAIN", **BOUNDARY})
        last = run["responses"][-1]["assessment"]
        if last["status"] in {"UNASSESSED", "NEEDS_CLARIFICATION"}:
            return self._save(command_id, digest, {"status": "CLARIFY_RESPONSE", "assessment_status": "ABSTAIN", "issues": deepcopy(last["issues"]),
                "message": "Ответ сохранён для преподавателя. Нужны структурированные поля и собственное объяснение; можно запросить подсказку.", **BOUNDARY})
        matched = last["status"] == "TYPED_FIELDS_MATCH_REVIEW_EXPLANATION"
        before_help = bool(run["local_help"])
        earlier_error = any(r["assessment"]["status"] == "TYPED_FIELDS_NEED_WORK" for r in run["responses"][:-1])
        repeated = run["public"]["context_exposure"] == "REPEATED"
        evidence_kind = ("CORRECTION_AFTER_SUPPORT" if matched and earlier_error and before_help else
                         "CORRECTION_AFTER_PRIOR_CONTEXT_FEEDBACK" if matched and earlier_error and repeated else
                         "SELF_CORRECTION_BEFORE_LOCAL_FEEDBACK" if matched and earlier_error else
                         "GUIDED_TYPED_MATCH" if matched and before_help else
                         "REPEATED_CONTEXT_TYPED_MATCH" if matched and repeated else
                         "FIRST_TYPED_MATCH" if matched and len(run["responses"]) == 1 else
                         "REVISED_TYPED_MATCH" if matched else "TYPED_FIELDS_NEED_WORK")
        focus = self._focus(run)
        sid, reason = self._choose(run, matched, focus)
        run.update({"closed": True, "typed_match": matched, "evidence_kind": evidence_kind})
        self._support("FEEDBACK_SHOWN", focus)
        result = {"status": "NEXT_ACTIVITY" if sid else "AWAIT_TEACHER_REVIEW",
                  "assessment_status": "PARTIAL", "assessment_basis": "AUTHORED_TYPED_FIELDS_ONLY",
                  "from_issuance_id": issuance_id, "evidence_kind": evidence_kind,
                  "context_exposure": run["public"]["context_exposure"],
                  "support_before_feedback": "LOCAL_SUPPORT_SHOWN" if before_help else run["public"]["support_history"],
                  "external_experience_and_help": "UNKNOWN",
                  "local_first_response_preserved": True, "skill_focus": focus,
                  "skill_dependencies": deepcopy(self._nodes[focus]["requires"]),
                  "focus_is_diagnosis": False, "independent_mastery_evidence": False,
                  "selection_is_provisional": True, "reason": reason,
                  "feedback": deepcopy(last["feedback"]), **BOUNDARY}
        if sid:
            self._issue(sid, reason)
            result["next_assignment"] = deepcopy(self._current["public"])
        else:
            self._current = None
            result["next_assignment"] = None
            result["gap"] = "Teacher prose review and unseen independent/delayed observations remain required."
        self._journal.append({"kind": "ADVANCED", "decision": deepcopy(result)})
        return self._save(command_id, digest, result)

    def teacher_snapshot(self):
        """Private read-only journal; raw prose is preserved, never auto-understood."""
        self._fresh()
        return {"audience": "TEACHER_ONLY", "session_id": self._session_id,
                "policy_sha256": POLICY_SHA256, "source_graph": deepcopy(self._policy["source_graph"]),
                "pedagogical_basis": deepcopy(self._policy["pedagogical_basis"]),
                "runs": deepcopy(self._runs), "help_history": deepcopy(self._help),
                "journal": deepcopy(self._journal), "external_help_verified": False, **BOUNDARY}
