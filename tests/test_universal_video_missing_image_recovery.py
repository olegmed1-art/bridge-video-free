from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "ops/oracle_universal_video_container_missing_image_recover.sh"
).read_text(encoding="utf-8")


def test_recovery_repairs_only_digest_pinned_speaker_assets_before_readiness() -> None:
    model_repair = SCRIPT.index('speaker_cache="$BASE_DIR/model-cache/speaker"')
    image_install = SCRIPT.index('image_tag="bridge-school/universal-video:$EXPECTED_SHA"')
    assert model_repair < image_install
    assert "915e0573bc4e17197a7a893d0eb98e1a851abb64451b2e1a8ad51f5f99040360" in SCRIPT
    assert "1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b" in SCRIPT
    assert 'if target.is_file() and not target.is_symlink() and digest(target) == expected:' in SCRIPT
    assert 'if digest(candidate) != expected:' in SCRIPT
    assert "tempfile.TemporaryDirectory(dir=cache)" in SCRIPT
    assert "os.replace(candidate, target)" in SCRIPT


def test_recovery_retains_fail_closed_activation_order() -> None:
    readiness = SCRIPT.index("UNIVERSAL_VIDEO_CONTAINER_ACTIVATE=0")
    backup = SCRIPT.index('install -o root -g root -m 0600 "$ENV_FILE" "$backup"')
    stop = SCRIPT.index('systemctl stop "$SERVICE"')
    assert readiness < backup < stop
    assert 'flock --exclusive --nonblock 9 || die WORKLOAD_LOCKED' in SCRIPT
    assert SCRIPT.count('find "$BASE_DIR/spool/running"') >= 2
