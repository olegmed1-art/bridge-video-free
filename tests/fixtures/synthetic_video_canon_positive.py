"""Complete *shape* of a positive 0329 rehearsal, never a real attestation.

The caller must obtain the two state hashes from its disposable database after
staging the synthetic rule/tests. All evidence digests here refer only to
fictional labels; production promotion must not consume this fixture.
"""
from copy import deepcopy
import hashlib
import re
from typing import Mapping

from bridge_contracts.video_canon_ai_promotion import (
    REQUIRED_CHECKS,
    STATE_DEPENDENT_CHECKS,
    build_ai_canon_promotion,
)
from tests.test_video_canon_ai_promotion import _bundle, _candidate


SCHOOL_ID = "11111111-1111-4111-8111-111111111111"
SOURCE_ID = "22222222-2222-4222-8222-222222222222"
CANDIDATE_ID = "33333333-3333-4333-8333-333333333333"
ITEM_ID = "44444444-4444-4444-8444-444444444444"
VERSION_ID = "55555555-5555-4555-8555-555555555555"
RULE_ID = "66666666-6666-4666-8666-666666666666"
BUNDLE_ID = "77777777-7777-4777-8777-777777777777"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LOGIN = re.compile(r"^canon_rehearsal_[a-z0-9_]+_login$")
SYNTHETIC_LOGIN_PRINCIPALS = {
    "control": "canon_rehearsal_control_login",
    "semantic": "canon_rehearsal_semantic_login",
    "bridge": "canon_rehearsal_bridge_login",
    "firewall": "canon_rehearsal_firewall_login",
}
CAPABILITIES = {
    "control": "bridge_school_canon_control_verifier",
    "semantic": "bridge_school_canon_semantic_verifier",
    "bridge": "bridge_school_canon_bridge_verifier",
    "firewall": "bridge_school_canon_firewall_verifier",
}


def _synthetic_digest(check_id: str) -> str:
    return hashlib.sha256(("SYNTHETIC-NOT-ATTESTED:" + check_id).encode()).hexdigest()


def build_synthetic_positive_rehearsal(
    canon_snapshot_sha256: str, rule_test_state_sha256: str,
    login_principals: Mapping[str, str],
) -> dict:
    """Assemble every required handoff row without touching a database.

    A disposable executor must independently insert/read the rows under the
    correct test roles, obtain genuine *local* state hashes, and run 0329. This
    builder is not a verifier, evidence source, or permission grant.
    """
    if not all(_SHA256.fullmatch(value or "") for value in (
        canon_snapshot_sha256, rule_test_state_sha256
    )):
        raise ValueError("disposable state hashes must be SHA-256")
    if (
        not isinstance(login_principals, Mapping)
        or set(login_principals) != set(CAPABILITIES)
        or not all(isinstance(name, str) and _LOGIN.fullmatch(name)
                   for name in login_principals.values())
        or len(set(login_principals.values())) != 4
    ):
        raise ValueError("four distinct synthetic LOGIN principals required")
    candidate = _candidate()
    payload = candidate["payload"]
    review = _bundle(candidate)
    review["canon_snapshot_sha256"] = canon_snapshot_sha256
    review["rule_test_state_sha256"] = rule_test_state_sha256
    roles = {
        "SEMANTIC_PARSE": ("semantic-model-a", login_principals["semantic"], "I2"),
        "BRIDGE_LOGIC": ("bridge-engine-b", login_principals["bridge"], "I3"),
        "HIDDEN_INFORMATION_FIREWALL": ("taint-analyzer", login_principals["firewall"], "I2"),
    }
    for check in review["checks"]:
        family, principal, assurance = roles.get(check["check_id"], (
            "formal-checker", login_principals["control"], "I1"
        ))
        check.update(
            verifier_family=family,
            execution_principal=principal,
            assurance_level=assurance,
            evidence_sha256=_synthetic_digest(check["check_id"]),
            canon_snapshot_sha256=(
                canon_snapshot_sha256 if check["check_id"] in STATE_DEPENDENT_CHECKS else None
            ),
        )
    sealed = build_ai_canon_promotion(candidate, review)
    authorization = payload["source_authorization"]
    source_policy = {
        "school_id": SCHOOL_ID,
        "source_id": SOURCE_ID,
        "source_sha256": payload["source"]["source_sha256"],
        "video_file_id": payload["source"]["video_file_id"],
        "teacher_ids": authorization["authorized_teacher_ids"],
        "semantic_scopes": authorization["approved_semantic_scopes"],
        "system_profile": review["system_profile"],
        "learner_level": review["learner_level"],
        "policy_version": review["policy_version"],
        "authorization_evidence_sha256": authorization["authorization_evidence_sha256"],
        "status": "active",
        "valid_from": "2026-09-01T00:00:00Z",
    }
    normalized_rule = deepcopy(payload["normalized_rule"])
    normalized_rule["compiled_payload"]["video_candidate_payload_hash"] = candidate["payload_hash"]
    rule_tests = [
        {
            "test_key": f"video-canon:{candidate['payload_hash']}:{kind}:{number}",
            "test_type": kind,
            "fixture": {key: value for key, value in case.items() if key != "expect"},
            "expected": {"expect": case["expect"]},
            "enabled": True,
            "latest_result": "pass",  # asserted only inside a disposable rehearsal
        }
        for kind, cases in payload["tests"].items()
        for number, case in enumerate(cases, start=1)
    ]
    rule_tests.extend([
        {"test_key": "synthetic:hidden-information", "test_type": "hidden_information",
         "fixture": {"synthetic": True}, "expected": {"expect": "NO_LEAK"},
         "enabled": True, "latest_result": "pass"},
        {"test_key": "synthetic:regression", "test_type": "regression",
         "fixture": {"synthetic": True}, "expected": {"expect": "PASS"},
         "enabled": True, "latest_result": "pass"},
    ])
    return {
        "fixture_class": "SYNTHETIC_NON_AUTHORITATIVE",
        "real_rights_or_review_proven": False,
        "login_principals": dict(login_principals),
        "capability_grants": {login_principals[key]: capability
                              for key, capability in CAPABILITIES.items()},
        "synthetic_role_warning": "teacher:diana is a fictional test label; real teacher is Oleg",
        "ids": {"school_id": SCHOOL_ID, "source_id": SOURCE_ID,
                "analysis_candidate_id": CANDIDATE_ID, "knowledge_item_id": ITEM_ID,
                "knowledge_version_id": VERSION_ID, "rule_id": RULE_ID,
                "verification_bundle_id": BUNDLE_ID},
        "school_claim": {"school_id": SCHOOL_ID, "stable_name": "synthetic-canon-rehearsal"},
        "source_claim": {"source_id": SOURCE_ID, "school_id": SCHOOL_ID,
                         "source_type": "video", "title": "Synthetic lesson, no real media",
                         "status": "active"},
        "candidate": candidate,
        "analysis_candidate_claim": {
            "analysis_candidate_id": CANDIDATE_ID, "school_id": SCHOOL_ID,
            "source_id": SOURCE_ID, "candidate_type": candidate["candidate_type"],
            "stable_key": candidate["stable_key"],
            "input_fingerprint": candidate["payload_hash"],
            "quality_status": candidate["quality_status"],
            "promotion_status": "staging", "payload": deepcopy(payload),
            "payload_hash": candidate["payload_hash"],
            "method_version": candidate["method_version"],
        },
        "review_input": deepcopy(review),
        "source_policy_claim": source_policy,
        "knowledge_item_claim": {"knowledge_item_id": ITEM_ID, "school_id": SCHOOL_ID,
                                 "stable_key": "video-canon:" + candidate["payload_hash"],
                                 "knowledge_type": "bidding_rule", "status": "active"},
        "knowledge_version_claim": {
            "knowledge_version_id": VERSION_ID, "knowledge_item_id": ITEM_ID,
            "authority_class": "school_canon", "review_status": "unreviewed",
            "status": "candidate", "content": deepcopy(payload),
            "bidding_system_key": review["system_profile"],
            "agreement_scope": {"scope_key": review["activation_scope"]},
            "level_scope": {"level_key": review["learner_level"]},
            "effective_from": review["effective_period"]["valid_from"],
            "effective_to": review["effective_period"]["valid_to"],
            "method_version": candidate["method_version"],
            "provenance": {
                "promotion_mode": "AI_VERIFIED_TEACHER_VIDEO",
                "analysis_candidate_id": CANDIDATE_ID,
                "candidate_payload_hash": candidate["payload_hash"],
                "source_id": SOURCE_ID,
                "source_sha256": payload["source"]["source_sha256"],
                "video_file_id": payload["source"]["video_file_id"],
                "teacher_statement_sha256": payload["teacher_assertion"]["statement_sha256"],
                "transcript_locators": payload["teacher_assertion"]["transcript_locators"],
            },
        },
        "knowledge_version_source_claim": {
            "knowledge_version_id": VERSION_ID, "relation_type": "derived_from",
            "source_id": SOURCE_ID,
            "source_locator": {"transcript_locators": payload["teacher_assertion"]["transcript_locators"]},
        },
        "rule_claim": {**normalized_rule, "rule_id": RULE_ID,
                       "school_id": SCHOOL_ID, "knowledge_version_id": VERSION_ID,
                       "lifecycle_status": "validated",
                       "explanation": deepcopy(payload["explanation"])},
        "rule_test_claims": rule_tests,
        "verification_bundle_claim": {
            "video_canon_ai_verification_bundle_id": BUNDLE_ID,
            "school_id": SCHOOL_ID, "analysis_candidate_id": CANDIDATE_ID,
            "candidate_payload_hash": candidate["payload_hash"],
            "verification_bundle_sha256": sealed["verification_bundle_sha256"],
            "bundle_canonical_json": sealed["verification_bundle_canonical_json"],
            "bundle_payload": sealed["verification_bundle"],
        },
        "verification_rows": sealed["verification_bundle"]["checks"],
        "sealed_command": sealed,
    }


assert len(REQUIRED_CHECKS) == 16
