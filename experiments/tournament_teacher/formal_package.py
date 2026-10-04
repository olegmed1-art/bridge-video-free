"""Two-rule first-import package: shape-meaning assessment only."""
from .candidate_package import package
from bridge_school_api.tournament_teacher import VERSION,SCOPE,digest


def formal_package():
    result = []
    for candidate in package()["candidates"]:
        r = candidate["source_rule"]
        if r["rule_id"] not in {"RULE-TOUR-1NT-RSP-3H","RULE-TOUR-1NT-RSP-3S"}:
            continue
        assert r["rule_status"]=="ACTIVE" and r["sync_status"]=="DRIVE_CANON_ONLY"
        catalog = dict(rule_kind="bid",auction_pattern={"calls":["1NT","PASS"],"actor":"responder"},
            hand_constraints={"shape_sorted":[1,3,4,5],"singleton":r["call"][-1],"minor_lengths":[4,5]},
            public_context_constraints={"system_profile":"SCHOOL_TOURNAMENT_CURRENT_V1","decision_type":"BIDDING"},
            action={"call":r["call"],"assessment_only":True},meaning={"text":r["meaning"]},
            public_inference={},alert_semantics={"status":"UNRESOLVED"},forcing_semantics={"status":"FG"},
            priority=0,specificity=0,explanation={"source":r["original_excerpt"],"decision_ids":r["decision_ids"]},
            condition_schema_version=VERSION,method_version=VERSION)
        payload = dict(contract=VERSION,scope_key=SCOPE,assessment_scope="shape_meaning_only",
                       recommend_supported=False,source_rule=r,catalog=catalog)
        result.append(dict(rule_key=r["rule_id"],payload=payload,payload_sha256=digest(payload)))
    assert len(result)==2
    return dict(format=VERSION,scope_key=SCOPE,school_id=None,
                create_activations=False,source_status="PRESERVED",rules=result)


if __name__ == "__main__":
    import json
    print(json.dumps(formal_package(),ensure_ascii=False,indent=2))
