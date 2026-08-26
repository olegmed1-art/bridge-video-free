from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops/probe_uv003_ready_before.py"


def load_module():
    spec = importlib.util.spec_from_file_location("uv003_ready_before", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def encoded(value) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def test_exact_local_read_only_surface():
    module = load_module()
    assert module.ASSISTANT_SERVICE == "assistant-lab.service"
    assert module.VIDEO_SERVICE == "universal-video.service"
    assert module.READY_HOST == "127.0.0.1"
    assert module.READY_PORT == 8080
    assert module.READY_PATH == "/readyz"
    text = SCRIPT.read_text(encoding="utf-8")
    for forbidden in (
        "spool/inbox",
        "submit-bridge",
        "drive_results",
        "GOOGLE_DRIVE_OAUTH",
        "ffmpeg",
        "faster_whisper",
        "os.environ",
        "print(document",
        "print(body",
    ):
        assert forbidden not in text
    assert 'print(f"UV003_READY_BEFORE_CODE={code}")' in text


def test_complete_ready_document_passes():
    module = load_module()
    body = encoded(
        {
            "status": "ready",
            "engine": "DDS3",
            "fallback_used": False,
            "position_solver": "ready",
        }
    )
    assert module.classify_document(200, body) == "PASS"


def test_each_required_field_has_a_fixed_missing_or_bad_code():
    module = load_module()
    base = {
        "status": "ready",
        "engine": "DDS3",
        "fallback_used": False,
        "position_solver": "ready",
    }
    cases = (
        ("status", "STATUS_MISSING", "degraded", "STATUS_NOT_READY"),
        ("engine", "ENGINE_MISSING", "OTHER", "ENGINE_NOT_DDS3"),
        ("fallback_used", "FALLBACK_MISSING", True, "FALLBACK_NOT_FALSE"),
        ("position_solver", "POSITION_SOLVER_MISSING", "loading", "POSITION_SOLVER_NOT_READY"),
    )
    for key, missing_code, bad_value, bad_code in cases:
        missing = dict(base)
        missing.pop(key)
        assert module.classify_document(200, encoded(missing)) == missing_code
        bad = dict(base)
        bad[key] = bad_value
        assert module.classify_document(200, encoded(bad)) == bad_code


def test_transport_and_document_failures_are_coarsely_classified():
    module = load_module()
    assert module.classify_document(503, b"{}") == "HTTP_NOT_2XX"
    assert module.classify_document(200, b"not-json") == "JSON_INVALID"
    assert module.classify_document(200, encoded([])) == "JSON_NOT_OBJECT"
    assert module.classify_document(200, b"\xff") == "UTF8_INVALID"
    assert module.classify_document(200, b"x" * (module.MAX_BODY_BYTES + 1)) == "BODY_TOO_LARGE"


def test_all_possible_external_codes_are_allowlisted():
    module = load_module()
    expected = {
        "ASSISTANT_SERVICE_INACTIVE",
        "VIDEO_SERVICE_INACTIVE",
        "SYSTEMCTL_UNAVAILABLE",
        "CONNECT_REFUSED",
        "FETCH_TIMEOUT",
        "FETCH_FAILED",
        "HTTP_NOT_2XX",
        "BODY_TOO_LARGE",
        "UTF8_INVALID",
        "JSON_INVALID",
        "JSON_NOT_OBJECT",
        "STATUS_MISSING",
        "STATUS_NOT_READY",
        "ENGINE_MISSING",
        "ENGINE_NOT_DDS3",
        "FALLBACK_MISSING",
        "FALLBACK_NOT_FALSE",
        "POSITION_SOLVER_MISSING",
        "POSITION_SOLVER_NOT_READY",
        "INTERNAL_FAILURE",
        "PASS",
    }
    assert module.ALLOWED_CODES == expected
