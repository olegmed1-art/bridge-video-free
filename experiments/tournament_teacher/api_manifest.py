"""Manifest for the verified source-level ASGI target; no deployment identities."""
import json

from bridge_school_api.tournament_teacher_test_adapter import (
    CONTRACT,NAMESPACE,PROFILE,RULES_SHA256,SOURCE_VERSION,TARGET,TEACHER_KEY,
    TEACHER_VERSION,rule_binding_uuid,position_uuid,
)
from .consumer import RULES_PATH
from .scenarios import CASES,make_request


def manifest():
    rules = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    example = make_request(next(c for c in CASES if c[0]=="1H-1NT-REBID-2NT"))
    example.pop("proposed_call")
    example["task"] = "recommend"
    return dict(contract_version=CONTRACT,target=TARGET,
                target_status="SOURCE_TARGET_CONFIRMED_DEPLOYED_BINDING_UNESTABLISHED",
                entrypoint="app:app",method="POST",route="/v1/ai/positions/{position_id}/teacher-evidence",
                auth_dependency="bridge_school_api.main.require_api_token",
                deployed_target=None,database_position_uuid=None,database_version_uuid=None,
                teacher_key=TEACHER_KEY,teacher_version=TEACHER_VERSION,system_profile=PROFILE,
                source_version=SOURCE_VERSION,rules_sha256=RULES_SHA256,
                synthetic_uuid_namespace=str(NAMESPACE),
                synthetic_example_position_uuid=str(position_uuid(example)),
                synthetic_example_input=example,
                rule_bindings=[dict(rule_id=r["rule_id"],binding_uuid=str(rule_binding_uuid(r["rule_id"])),
                                    database_rule_uuid=None,decision_ids=r["decision_ids"],
                                    source_id=r["source_id"],rule_status=r["rule_status"],sync_status=r["sync_status"])
                               for r in rules])


if __name__ == "__main__":
    print(json.dumps(manifest(),indent=2))
