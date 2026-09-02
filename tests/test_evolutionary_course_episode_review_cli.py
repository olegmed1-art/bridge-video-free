import json
from pathlib import Path
import subprocess
import sys

from evolutionary_course.episode_review import build_episode_review_request
from evolutionary_course.video31_adapter import adapt_video31_quality
from test_evolutionary_course_video31_adapter import _lesson, _quality, _reviewed_catalog, _source


def _inputs(tmp_path: Path):
    catalog = _reviewed_catalog()
    episode = adapt_video31_quality(
        _quality(), lesson_identity=_lesson(), source=_source(),
        skill_catalog=catalog, require_catalog_binding=True,
    )["episodes"][0]
    request = build_episode_review_request(episode, catalog=catalog)
    paths = [tmp_path / name for name in ("episode.json", "catalog.json", "request.json")]
    for path, value in zip(paths, (episode, catalog, request)):
        path.write_text(json.dumps(value), encoding="utf-8")
    return paths


def _run(episode: Path, catalog: Path, request: Path, output: Path):
    return subprocess.run([
        sys.executable, "-m", "evolutionary_course.episode_review_cli",
        "--episode", str(episode), "--catalog", str(catalog),
        "--request", str(request), "--output", str(output),
    ], text=True, capture_output=True, check=False)


def test_blank_private_review_request_cannot_create_receipt(tmp_path):
    episode, catalog, request = _inputs(tmp_path)
    output = tmp_path / "receipt.json"
    result = _run(episode, catalog, request, output)
    assert result.returncode != 0
    assert "invalid private episode review decision" in result.stderr
    assert not output.exists()


def test_explicit_private_accept_creates_non_persisting_receipt(tmp_path):
    episode, catalog, request = _inputs(tmp_path)
    value = json.loads(request.read_text(encoding="utf-8"))
    value["decision_input"] = {
        "decision": "ACCEPT", "reviewer_id": "director",
        "reviewer_authority": "SCHOOL_DIRECTOR",
        "reviewed_at": "2026-08-30T18:00:00Z", "rationale": "Episode reviewed.",
    }
    request.write_text(json.dumps(value), encoding="utf-8")
    output = tmp_path / "receipt.json"
    result = _run(episode, catalog, request, output)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["disposition"] == "PRIVATE_RESEARCH_ACCEPTED"
    assert receipt["episode_persisted"] is False


def test_tampered_private_review_request_fails_closed(tmp_path):
    episode, catalog, request = _inputs(tmp_path)
    value = json.loads(request.read_text(encoding="utf-8"))
    value["episode_sha256"] = "0" * 64
    request.write_text(json.dumps(value), encoding="utf-8")
    output = tmp_path / "receipt.json"
    result = _run(episode, catalog, request, output)
    assert result.returncode != 0
    assert "private review request binding mismatch" in result.stderr
    assert not output.exists()
