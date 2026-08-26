from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops/probe_uv003_runtime_env_shape.py"


def load_module():
    spec = importlib.util.spec_from_file_location("uv003_env_shape", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exact_read_only_contract():
    module = load_module()
    assert str(module.ENV_FILE) == "/opt/bridge-school/universal-video/universal-video.env"
    assert module.SOURCE_KEY == "UNIVERSAL_VIDEO_SOURCE_COMMIT="
    text = SCRIPT.read_text(encoding="utf-8")
    for forbidden in (
        "write_text",
        "os.replace",
        "os.rename",
        "systemctl",
        "spool/inbox",
        "submit-bridge",
        "drive_results",
        "GOOGLE_DRIVE",
        "ffmpeg",
        "faster_whisper",
    ):
        assert forbidden not in text
    assert 'print(f"UV003_RUNTIME_ENV_SHAPE_CODE={code}")' in text


def test_source_pin_cardinality_is_coarse_and_deterministic():
    module = load_module()
    base = b"A=1\n# comment\nB=2\n"
    assert module.classify_bytes(base) == "SOURCE_KEY_ZERO"
    one = base + b"UNIVERSAL_VIDEO_SOURCE_COMMIT=hidden\n"
    assert module.classify_bytes(one) == "SOURCE_KEY_ONE"
    multiple = one + b"UNIVERSAL_VIDEO_SOURCE_COMMIT=also-hidden\n"
    assert module.classify_bytes(multiple) == "SOURCE_KEY_MULTIPLE"


def test_encoding_and_structure_failures_are_fixed_codes():
    module = load_module()
    assert module.classify_bytes(b"") == "EMPTY"
    assert module.classify_bytes(b"A=1\x00B=2\n") == "NUL_PRESENT"
    assert module.classify_bytes(b"\xff") == "UTF8_INVALID"
    assert module.classify_bytes("\ufeffA=1\n".encode("utf-8")) == "UTF8_BOM_PRESENT"
    assert module.classify_bytes(b"not-an-assignment\n") == "MALFORMED_NONCOMMENT_LINE"
    long_line = ("A=" + "x" * module.MAX_LINE_CHARS + "\n").encode("utf-8")
    assert module.classify_bytes(long_line) == "LINE_TOO_LONG"


def test_probe_classifies_safe_files_without_disclosing_values(tmp_path: Path):
    module = load_module()
    target = tmp_path / "runtime.env"
    target.write_text(
        "UNIVERSAL_VIDEO_SOURCE_COMMIT=secret-value\n"
        "UNIVERSAL_VIDEO_WHISPER_MODEL=small\n",
        encoding="utf-8",
    )
    assert module.probe(target) == "SOURCE_KEY_ONE"
    target.unlink()
    assert module.probe(target) == "MISSING"


def test_all_external_codes_are_allowlisted():
    module = load_module()
    assert module.ALLOWED_CODES == {
        "MISSING",
        "SYMLINK",
        "NOT_REGULAR",
        "STAT_FAILED",
        "TOO_LARGE",
        "READ_FAILED",
        "EMPTY",
        "NUL_PRESENT",
        "UTF8_INVALID",
        "UTF8_BOM_PRESENT",
        "LINE_TOO_LONG",
        "MALFORMED_NONCOMMENT_LINE",
        "SOURCE_KEY_ZERO",
        "SOURCE_KEY_ONE",
        "SOURCE_KEY_MULTIPLE",
        "INTERNAL_FAILURE",
    }
