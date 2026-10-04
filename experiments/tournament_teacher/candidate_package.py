"""Build a non-activatable candidate package; no DB access or private IDs."""
from hashlib import sha256
import json
from pathlib import Path

from bridge_school_api.tournament_teacher_test_adapter import RULES_SHA256

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_CHECKSUM = "1737eedba6110440220b814c2d104c06e5ced1050f558fd9fcdab256bde70557"
SCOPE = "tournament-candidate-rehearsal-only-v1"
METHOD = "tournament-candidate-import-v1"


def migration_checksum():
    path = ROOT / "database/migrations/0200_bidding_knowledge_v0.sql"
    content = "FILE:" + path.name + "\n" + path.read_text(encoding="utf-8")
    for part in sorted(path.with_suffix("").glob("*.sql")):
        content += "\nFILE:" + part.relative_to(path.parent).as_posix() + "\n"
        content += part.read_text(encoding="utf-8")
    return sha256(content.encode()).hexdigest()


def package():
    snapshot = Path(__file__).with_name("rules.json").read_text(encoding="utf-8")
    assert sha256(snapshot.encode()).hexdigest() == RULES_SHA256
    assert migration_checksum() == MIGRATION_CHECKSUM
    rules = json.loads(snapshot)
    candidates = []
    for ordinal, rule in enumerate(rules):
        blockers = ["PRODUCTION_FORMALIZATION_AND_SCOPE_REVIEW_REQUIRED",
                    "DEPLOYED_TEACHER_CALLER_UNESTABLISHED"]
        if rule["predicate"] != "singleton_minors":
            blockers.append("SCHOOL_POINT_METHOD_UNRESOLVED")
        if rule["rule_status"] != "ACTIVE":
            blockers.append("SOURCE_STATUS_" + rule["rule_status"])
        if rule["sync_status"] != "DRIVE_CANON_ONLY":
            blockers.append("SOURCE_SYNC_" + rule["sync_status"])
        candidates.append(dict(ordinal=ordinal, rule_key=rule["rule_id"],
                               lifecycle_status="candidate", authority_class="research_candidate",
                               review_status="unreviewed", runtime_eligible=False,
                               blockers=blockers, source_rule=rule))
    return dict(format=METHOD, scope=SCOPE, source_sha256=RULES_SHA256,
                migration_0200_sha256=MIGRATION_CHECKSUM,
                school_id=None, database_bindings=None, create_activations=False,
                candidates=candidates)


if __name__ == "__main__":
    print(json.dumps(package(), ensure_ascii=False, indent=2))
