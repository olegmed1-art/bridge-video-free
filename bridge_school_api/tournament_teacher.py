"""Read-only, SQL-gated assessment of the two formalized 1NT response meanings.

No scheduler, worker, activation side effect, L1 change, or recommendation policy.
The stored position supplies school and hand; callers cannot override either.
"""
from hashlib import sha256
import json

from fastapi import APIRouter, HTTPException
from pydantic import ConfigDict
from uuid import UUID

from .bidding_catalog_reader import read_school_catalog
from .db import connect
from .ai_teacher import TeacherEvidence as LegacyTeacherEvidence, record_teacher_evidence

router = APIRouter(prefix="/v1/ai",tags=["bridge-ai-teacher"])


class TeacherEvidence(LegacyTeacherEvidence):
    model_config = ConfigDict(extra="allow")
    canon_request: dict | None = None


@router.post("/positions/{position_id}/teacher-evidence")
def assess_or_record(position_id: UUID, evidence: TeacherEvidence):
    if ("canon_request" in evidence.model_fields_set
            or evidence.teacher_key.startswith("school-tournament-shape")
            or (evidence.teacher_version or "").startswith("tour-1nt-shape-")
            or any(key.lower().replace("_", "").startswith("canon") for key in (evidence.model_extra or {}))):
        return answer(position_id,evidence)
    return record_teacher_evidence(position_id,evidence)

KEY = "school-tournament-shape"
VERSION = "tour-1nt-shape-assessment-v1"
PROFILE = "SCHOOL_TOURNAMENT_CURRENT_V1"
SCOPE = "school-tournament-1nt-shape-assessment-v1"
CANARY_KEY = "tournament-shape-canary-20261004-v1"
CANARY_HAND = "234.2.2345.23456"
PAYLOAD_HASHES = {'3H': '4127a40bfd62d7fa9d5d5b6e1f5b72b3c751881f2765cef5f690265382dcdb43', '3S': '2bb321fc675343a8dd3228cd06497d54ba66727be1a104512808338d9ffd2989'}


def digest(payload):
    return sha256(json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()


def evaluate(position, call, catalog):
    result = dict(status="ABSTAIN",action=None,proposed_call=call,
                  assessment_scope="shape_meaning_only",scope_key=SCOPE,
                  teacher_version=VERSION,teacher_system=PROFILE,
                  persisted=False,queued=False,finalized=False,source_bindings=[])
    def stop(reason):
        result["reason"] = reason
        return result
    if call not in ("3H","3S"):
        return stop("UNSUPPORTED_CALL")
    if (position.get("school_status") != "active" or position.get("input_status") != "COMPLETE" or position.get("decision_type") != "BIDDING"
            or position.get("system_us") != PROFILE):
        return stop("POSITION_PROFILE_OR_COMPLETENESS_MISMATCH")
    dealer,seat = position.get("dealer"),position.get("seat")
    if (dealer not in ("N","E","S","W") or seat not in ("N","E","S","W")
            or seat != "NESW"[("NESW".index(dealer)+2)%4]
            or position.get("auction_json") != ["1NT","PASS"]
            or position.get("cards_played_json") != [] or position.get("dummy_pbn") not in (None,"")):
        return stop("UNSUPPORTED_OR_INCOMPLETE_PUBLIC_CONTEXT")
    hand = position.get("hand_pbn")
    if not isinstance(hand,str):
        return stop("INVALID_ACTING_HAND")
    parts = hand.upper().split(".")
    if len(parts) != 4:
        return stop("INVALID_ACTING_HAND")
    parts = ["" if part=="-" else part for part in parts]
    if (sum(map(len,parts)) != 13 or any(len(set(part)) != len(part) for part in parts)
            or any(rank not in "23456789TJQKA" for part in parts for rank in part)):
        return stop("INVALID_ACTING_HAND")
    rule_key = "RULE-TOUR-1NT-RSP-" + call
    rows = [row for row in catalog if row.get("rule_key")==rule_key]
    if len(rows) != 1:
        return stop("NO_UNIQUE_ELIGIBLE_CANON_BINDING")
    row = rows[0]
    payload = row.get("compiled_payload")
    if (row.get("school_id") != position.get("school_id") or row.get("scope_key") != SCOPE
            or row.get("method_version") != VERSION or not isinstance(payload,dict)
            or digest(payload) != PAYLOAD_HASHES.get(call)):
        return stop("CANON_VERSION_OR_PAYLOAD_MISMATCH")
    # Protect all evaluated catalog columns as well as the compiled source.
    for field,value in payload["catalog"].items():
        if digest(row.get(field)) != digest(value):
            return stop("CANON_COLUMN_BINDING_MISMATCH")
    shape = dict(zip("SHDC",map(len,parts)))
    fits = (shape[call[-1]]==1 and sorted(shape.values())==[1,3,4,5]
            and sorted((shape["D"],shape["C"]))==[4,5])
    source = payload["source_rule"]
    result.update(status="SUPPORTED" if fits else "CONTRADICTED",observed_shape=shape,
                  forcing_status="FG",point_threshold=None,
                  explanation=source["meaning"] + (" Рука соответствует этому значению." if fits else
                      " Рука не соответствует этому значению.") +
                      " Это проверка значения заявки, не рекомендация единственного выбора.",
                  source_bindings=[dict(rule_id=str(row["rule_id"]),rule_key=rule_key,
                      knowledge_version_id=str(row["knowledge_version_id"]),
                      runtime_activation_id=str(row["runtime_activation_id"]),
                      payload_sha256=digest(payload),source_id=source["source_id"],
                      source_url=source["source_url"],decision_ids=source["decision_ids"],
                      original_excerpt=source["original_excerpt"],teacher_excerpt=source["teacher_excerpt"])])
    return stop("SHAPE_MEANING_ONLY_POINTS_AND_PRIORITY_NOT_ASSERTED")


def answer(position_id, evidence):
    request = evidence.canon_request
    if (evidence.model_extra or not isinstance(request,dict)
            or set(request) != {"task","scope_key","version","proposed_call"}
            or request.get("task") != "assess_call"
            or {"test_request","test_source_version"} & evidence.model_fields_set
            or evidence.action is not None or evidence.confidence is not None
            or evidence.explanation is not None or evidence.candidate_scores or evidence.raw_output):
        raise HTTPException(422,detail={"code":"CANON_REQUEST_INPUT_ONLY"})
    if (evidence.teacher_key != KEY or evidence.teacher_version != VERSION
            or evidence.teacher_system != PROFILE or request["scope_key"] != SCOPE
            or request["version"] != VERSION):
        raise HTTPException(409,detail={"code":"CANON_SCOPE_OR_VERSION_MISMATCH"})
    if not isinstance(request["proposed_call"],str):
        raise HTTPException(422,detail={"code":"CANON_CALL_REQUIRED"})
    with connect() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        position = conn.execute("""SELECT p.position_id,p.school_id,p.stable_key,p.source_id,p.input_status,p.decision_type,p.system_us,
            p.dealer,p.seat,p.auction_json,p.hand_pbn,p.cards_played_json,p.dummy_pbn,s.status AS school_status
            FROM ai.decision_position p JOIN public.school s USING(school_id)
            WHERE p.position_id=%s""",(position_id,)).fetchone()
        if position is None:
            raise HTTPException(404,detail={"code":"POSITION_NOT_FOUND"})
        if (position["stable_key"] != CANARY_KEY or position["hand_pbn"] != CANARY_HAND
                or position["dealer"] != "N" or position["seat"] != "S"
                or position["source_id"] is None):
            result = evaluate(position,request["proposed_call"],[])
            result["reason"] = "PILOT_POSITION_ONLY"
            return dict(position_id=str(position_id),teacher_key=KEY,**result)
        rows = read_school_catalog(conn,position["school_id"],SCOPE)
        result = evaluate(position,request["proposed_call"],rows)
        if result["source_bindings"]:
            binding = result["source_bindings"][0]
            sources = conn.execute("""SELECT s.source_id,s.canonical_locator,kvs.source_locator
                FROM public.knowledge_version_source kvs JOIN public.source s USING(source_id)
                WHERE kvs.knowledge_version_id=%s AND s.school_id=%s AND s.status='active'""",
                (binding["knowledge_version_id"],position["school_id"])).fetchall()
            matches = [s for s in sources if s["source_id"]==position["source_id"]
                and s["canonical_locator"]==binding["source_url"]
                and isinstance(s["source_locator"],dict)
                and s["source_locator"].get("original_excerpt")==binding["original_excerpt"]
                and s["source_locator"].get("teacher_excerpt")==binding["teacher_excerpt"]]
            if len(matches) != 1:
                result.update(status="ABSTAIN",reason="SOURCE_BINDING_MISMATCH",source_bindings=[])
                result.pop("explanation",None)
                result.pop("forcing_status",None)
            else:
                binding["database_source_id"] = str(matches[0]["source_id"])
    return dict(position_id=str(position_id),teacher_key=KEY,**result)
