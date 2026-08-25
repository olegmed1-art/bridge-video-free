from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops/universal_video_diana11_shadow_preflight.py"


def load_module():
    spec = importlib.util.spec_from_file_location("uv003_preflight", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fresh_shadow_identity_is_exact_and_not_legacy():
    module = load_module()
    assert module.EXPERIMENT_ID == "UV-DIANA11-DURABLE-003"
    assert module.JOB_ID == "diana11-shadow-20260826-001"
    assert module.JOB_ID not in {
        "diana11-transcript-20260825-01",
        "diana11-bridge-20260825-01",
    }
    assert module.PROFILE == "bridge_lesson"
    assert module.SOURCE_FILE_ID == "1PGRLozLJKG8tl-JYGPTCcS_lT-nn-T7C"
    assert module.SOURCE_SIZE_BYTES == 740_292_560
    assert module.DESTINATION_FOLDER_ID == "1I8cSuA-p0MpaZIbA33slks19KyvfJDMK"
    assert module.canonical_job_hash() == module.EXPECTED_JOB_HASH
    assert module.EXPECTED_JOB_HASH == "a43e11beb0765aa91551d4c4a69767f02c4dcb3b5e485cd5bb0f2996e734d73d"


def test_preflight_has_no_execution_or_publication_surface():
    text = SCRIPT.read_text(encoding="utf-8")
    forbidden = (
        "spool/inbox",
        "enqueue(",
        "submit_for",
        "submit-bridge",
        "drive_results",
        "GOOGLE_DRIVE_OAUTH",
        "ffmpeg",
        "faster_whisper",
        "WhisperModel(",
        "shutil.move",
        "os.rename",
    )
    for marker in forbidden:
        assert marker not in text
    assert "UV003_EXECUTION_AUTHORIZED=NO" in text
    assert "UV003_PUBLICATION_AUTHORIZED=NO" in text
    assert "UV003_AUTOMATIC_RETRIES=0" in text


def test_read_only_preflight_derives_runtime_processing_identity(tmp_path: Path):
    module = load_module()
    source = tmp_path / "source"
    spool = tmp_path / "spool"
    env = tmp_path / "universal-video.env"
    source.mkdir()
    for leaf in ("inbox", "running", "done", "failed", "results"):
        (spool / leaf).mkdir(parents=True, exist_ok=True)

    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "test"], check=True)
    (source / "sentinel.txt").write_text("pinned\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "sentinel.txt"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-q", "-m", "pinned"], check=True)
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    env.write_text(
        f"UNIVERSAL_VIDEO_SOURCE_COMMIT={revision}\n"
        "UNIVERSAL_VIDEO_WHISPER_MODEL=small\n"
        "IGNORED_SECRET=do-not-print\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--runtime-env",
            str(env),
            "--source-dir",
            str(source),
            "--spool-root",
            str(spool),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert "UV003_PREFLIGHT_PASS" in proc.stdout
    assert f"UV003_PROCESSING_REVISION={revision}" in proc.stdout
    expected = module.fingerprint(
        {
            "contract": module.CONTRACT_VERSION,
            "source_revision": revision,
            "whisper_model": "small",
        }
    )
    assert f"UV003_PROCESSING_FINGERPRINT={expected}" in proc.stdout
    assert "do-not-print" not in proc.stdout
    assert proc.stderr == ""


def test_existing_fresh_job_identity_fails_closed(tmp_path: Path):
    module = load_module()
    source = tmp_path / "source"
    spool = tmp_path / "spool"
    env = tmp_path / "env"
    source.mkdir()
    for leaf in ("inbox", "running", "done", "failed", "results"):
        (spool / leaf).mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "test"], check=True)
    (source / "sentinel").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "sentinel"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-q", "-m", "x"], check=True)
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    env.write_text(
        f"UNIVERSAL_VIDEO_SOURCE_COMMIT={revision}\nUNIVERSAL_VIDEO_WHISPER_MODEL=small\n",
        encoding="utf-8",
    )
    (spool / "done" / f"{module.JOB_ID}.json").write_text("{}\n", encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--runtime-env",
            str(env),
            "--source-dir",
            str(source),
            "--spool-root",
            str(spool),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.returncode != 0
    assert "fresh job identity already exists in spool" in proc.stderr
    assert "UV003_PREFLIGHT_PASS" not in proc.stdout
