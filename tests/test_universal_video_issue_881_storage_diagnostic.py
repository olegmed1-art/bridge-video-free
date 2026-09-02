from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_docker_storage_diagnostic_has_one_numeric_argument():
    installer = (
        ROOT / "ops/oracle_universal_video_container_install.sh"
    ).read_text(encoding="utf-8")
    expected = (
        "printf 'UNIVERSAL_VIDEO_CONTAINER_STORAGE area=docker used_kb=%s\\n' "
        '"$storage_used_kb"'
    )
    stale_two_argument_form = (
        "printf 'UNIVERSAL_VIDEO_CONTAINER_STORAGE area=docker used_kb=%s\\n' "
        '"$storage_area" "$storage_used_kb"'
    )
    assert installer.count(expected) == 1
    assert stale_two_argument_form not in installer
