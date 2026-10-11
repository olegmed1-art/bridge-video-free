"""Pure, version-pinned tournament shape assessment; no I/O or activation."""
from hashlib import sha256
import json

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
