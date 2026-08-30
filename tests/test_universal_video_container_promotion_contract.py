from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "ops/oracle_universal_video_container_promote.sh").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github/workflows/oracle-universal-video-container-promote.yml").read_text(encoding="utf-8")


def test_promotion_is_evidence_bound_serialized_and_reversible() -> None:
    assert "assert x.get('conclusion') == 'success'" in WORKFLOW
    assert "assert x.get('head_sha') == os.environ['EXPECTED_COMMIT']" in WORKFLOW
    assert "group: oracle-instance-workload-mutation" in WORKFLOW
    assert "rollback" in SCRIPT
    assert "UV_CONTAINER_PROMOTION_ROLLED_BACK" in SCRIPT
    assert "UV_CONTAINER_PROMOTION_JOB_RUNNING" in SCRIPT
    assert "UNIVERSAL_VIDEO_CONTAINER_BUILD=0" in SCRIPT
    assert "contents/ops/oracle_universal_video_container_promote.sh?ref=$EXPECTED_COMMIT" in WORKFLOW
    assert "git hash-object -- /opt/bridge-school/universal-video-src/ops/oracle_universal_video_container_promote.sh" in WORKFLOW
    assert "UV_CONTAINER_PROMOTION_ENTRYPOINT_PASS" in WORKFLOW
    assert "UV_CONTAINER_PROMOTION_SOURCE_MISMATCH" in WORKFLOW
    assert "UV_CONTAINER_PROMOTION_ENTRYPOINT_MISSING" in WORKFLOW
    assert "UV_CONTAINER_PROMOTION_BLOB_MISMATCH" in WORKFLOW
    assert " /bin/bash /opt/bridge-school/universal-video-src/ops/oracle_universal_video_container_promote.sh" in WORKFLOW


def test_promotion_selects_exact_image_and_excludes_legacy_worker() -> None:
    assert '"$(docker inspect --format \'{{.Image}}\' universal-video-container)" == "$EXPECTED_DIGEST"' in SCRIPT
    assert 'systemctl is-active --quiet "$OLD_SERVICE" && fail UV_CONTAINER_PROMOTION_LEGACY_ACTIVE' in SCRIPT
    assert "x.get('active_jobs') == []" in SCRIPT
    assert "observed_at_unix" in SCRIPT
    assert "fallback_used=false active_jobs=0" in SCRIPT


def test_promotion_requires_a_fresh_status_from_the_new_resident() -> None:
    assert "fresh_status=0" in SCRIPT
    assert "fresh_status=1" in SCRIPT
    assert "(( fresh_status != 1 ))" in SCRIPT
    assert "UV_CONTAINER_PROMOTION_STATUS_MISSING" in SCRIPT
    assert "UV_CONTAINER_PROMOTION_STATUS_STALE" in SCRIPT
    assert SCRIPT.count("float(x.get('observed_at_unix') or 0) >= int(os.environ['STARTED_UNIX'])") == 2


def test_promotion_failure_reports_only_bounded_stage_and_runtime_code() -> None:
    assert "failure_stage='SWITCH_INSTALL'" in SCRIPT
    assert "failure_stage='SERVICE_ACTIVE'" in SCRIPT
    assert "failure_stage='STATUS_FRESH'" in SCRIPT
    assert "UV_CONTAINER_PROMOTION_STAGE_%s" in SCRIPT
    assert "journalctl -u \"$NEW_SERVICE\" -n 80 --no-pager -o cat" in SCRIPT
    assert "UV_CONTAINER_[A-Z0-9_]+" in SCRIPT
    assert "tail -n1 || true" in SCRIPT
