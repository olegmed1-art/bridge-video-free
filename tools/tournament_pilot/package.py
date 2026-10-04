"""Only the two previously approved excerpts; no private registry or identities."""
from hashlib import sha256
import json
from pathlib import Path
from uuid import UUID
from bridge_school_api import tournament_teacher as teacher

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_CHECKSUM = "1737eedba6110440220b814c2d104c06e5ced1050f558fd9fcdab256bde70557"


def formal_package():
    text = Path(__file__).with_name("package.json").read_text(encoding="utf-8")
    assert sha256(text.encode()).hexdigest() == "089bdf9a68e1fb221b3b92d70e9c7d2dd437ca68659c03be13dd2290d23f9b57"
    pkg = json.loads(text)
    assert len(pkg["rules"]) == 2
    for entry in pkg["rules"]:
        assert teacher.digest(entry["payload"]) == teacher.PAYLOAD_HASHES[entry["payload"]["source_rule"]["call"]]
    return pkg


def check_schema():
    path = ROOT / "database/migrations/0200_bidding_knowledge_v0.sql"
    text = "FILE:" + path.name + "\n" + path.read_text(encoding="utf-8")
    for part in sorted(path.with_suffix("").glob("*.sql")):
        text += "\nFILE:" + part.relative_to(path.parent).as_posix() + "\n" + part.read_text(encoding="utf-8")
    assert sha256(text.encode()).hexdigest() == MIGRATION_CHECKSUM


def envelope(call):
    return dict(teacher_key=teacher.KEY,teacher_version=teacher.VERSION,teacher_system=teacher.PROFILE,
        canon_request=dict(task="assess_call",scope_key=teacher.SCOPE,version=teacher.VERSION,proposed_call=call))


def position(school,shape=(3,1,4,5),changes=None):
    return dict(school_id=school,school_status="active",input_status="COMPLETE",decision_type="BIDDING",
        system_us=teacher.PROFILE,dealer="N",seat="S",stable_key=teacher.CANARY_KEY,source_id=UUID(int=301),
        hand_pbn=".".join("23456789TJQKA"[:n] or "-" for n in shape),auction_json=["1NT","PASS"],
        cards_played_json=[],dummy_pbn=None) | (changes or {})


def catalog(call):
    e=next(r for r in formal_package()["rules"] if r["payload"]["source_rule"]["call"]==call)
    return dict(school_id=UUID(int=101),scope_key=teacher.SCOPE,rule_key=e["rule_key"],
        rule_id=UUID(int=201),knowledge_version_id=UUID(int=202),runtime_activation_id=UUID(int=203),
        compiled_payload=e["payload"],**e["payload"]["catalog"])


def cases(call):
    good=(3,1,4,5) if call=="3H" else (1,3,4,5)
    other=(1,3,4,5) if call=="3H" else (3,1,4,5)
    return [
        ("positive","positive",good,{},"SUPPORTED"),
        ("minor_orientation","positive",(*good[:2],5,4),{},"SUPPORTED"),
        ("wrong_singleton","negative",other,{},"CONTRADICTED"),
        ("singleton_boundary","boundary",(2,2,4,5),{},"CONTRADICTED"),
        ("short_minor","boundary",(*good[:2],3,6),{},"CONTRADICTED"),
        ("hidden_context","hidden_information",good,{"dummy_pbn":"AKQ.JT9.876.5432"},"ABSTAIN"),
        ("wrong_turn","negative",good,{"seat":"E"},"ABSTAIN"),
        ("interference","negative",good,{"auction_json":["1NT","X"]},"ABSTAIN"),
        ("missing_hand","negative",good,{"hand_pbn":None},"ABSTAIN")]
