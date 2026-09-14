from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "ops"
    / "oracle_universal_video_container_missing_image_recover.sh"
)
SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")


def test_recovery_is_exact_runtime_fenced_and_nonactivating_before_swap() -> None:
    assert '[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]] || die INVALID_SHA' in SCRIPT
    assert 'flock --exclusive --nonblock 9 || die WORKLOAD_LOCKED' in SCRIPT
    assert SCRIPT.count('find "$BASE_DIR/spool/running"') >= 2
    assert 'UNIVERSAL_VIDEO_CONTAINER_ACTIVATE=0' in SCRIPT
    assert "'^UNIVERSAL_VIDEO_IMAGE=sha256:[0-9a-f]{64}$'" in SCRIPT
    assert 'IMAGE_REVISION_MISMATCH' in SCRIPT
    assert 'CONTAINER_IMAGE_MISMATCH' in SCRIPT


def test_recovery_repairs_only_runtime_pinned_speaker_models() -> None:
    assert "pyannote-segmentation-3.0.onnx" in SCRIPT
    assert "220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079" in SCRIPT
    assert "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx" in SCRIPT
    assert "1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b" in SCRIPT
    assert "hashlib.sha256(path.read_bytes()).hexdigest()" in SCRIPT
    assert "model digest mismatch" in SCRIPT
    assert "path.replace(quarantine)" in SCRIPT


def test_recovery_arms_rollback_before_service_stop() -> None:
    readiness = SCRIPT.index("UNIVERSAL_VIDEO_CONTAINER_ACTIVATE=0")
    backup = SCRIPT.index('install -o root -g root -m 0600 "$ENV_FILE" "$backup"')
    armed = SCRIPT.index("activated=1", backup)
    stop = SCRIPT.index('systemctl stop "$SERVICE"')
    candidate_install = SCRIPT.index(
        'install -o root -g root -m 0640 "$CANDIDATE_ENV" "$ENV_FILE"'
    )
    restart = SCRIPT.index('systemctl restart "$SERVICE"', stop)
    disarmed = SCRIPT.rindex("activated=0")
    assert readiness < backup < armed < stop < candidate_install < restart < disarmed
    assert "if (( rc != 0 && activated == 1 )); then" in SCRIPT
    assert 'install -o root -g root -m 0640 "$backup" "$ENV_FILE"' in SCRIPT
    assert "rollback=attempted" in SCRIPT
