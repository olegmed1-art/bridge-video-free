import json
from pathlib import Path
import subprocess
import sys


CANDIDATE = Path("data/research/evolutionary_course_diana2_club_split_skill_candidate_v1.json")
CATALOG = Path("data/research/evolutionary_course_skill_catalog_v1.json")
REQUEST = Path("data/research/evolutionary_course_diana2_methodology_review_request_v1.json")
def _run(request: Path, output: Path):
    return subprocess.run([
        sys.executable, "-m", "evolutionary_course.methodology_review_cli",
        "--candidate", str(CANDIDATE),
        "--catalog", str(CATALOG), "--request", str(request),
        "--output", str(output),
    ], text=True, capture_output=True, check=False)


def test_blank_request_cannot_create_decision_receipt(tmp_path):
    output = tmp_path / "receipt.json"
    result = _run(REQUEST, output)
    assert result.returncode != 0
    assert "invalid candidate review decision" in result.stderr
    assert not output.exists()


def test_explicit_authorized_decision_creates_non_mutating_receipt(tmp_path):
    request = json.loads(REQUEST.read_text(encoding="utf-8"))
    request["decision_input"] = {
        "decision": "APPROVE",
        "reviewer_id": "methodist-17",
        "reviewer_authority": "AUTHORIZED_METHODOLOGY_REVIEWER",
        "reviewed_at": "2026-08-30T20:00:00+03:00",
        "rationale": "Карточка и критерии проверены.",
    }
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    output = tmp_path / "receipt.json"
    result = _run(request_path, output)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["decision"] == "APPROVE"
    assert receipt["proposed_review_state"] == "APPROVED_CANDIDATE"
    assert receipt["catalog_mutated"] is False
    assert receipt["follow_up_required"] is True


def test_tampered_request_binding_fails_closed(tmp_path):
    request = json.loads(REQUEST.read_text(encoding="utf-8"))
    request["candidate_sha256"] = "0" * 64
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    output = tmp_path / "receipt.json"
    result = _run(request_path, output)
    assert result.returncode != 0
    assert "review request binding mismatch" in result.stderr
    assert not output.exists()
