from pathlib import Path

WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github/workflows/school-systems-steward-drive-audit.yml"
).read_text(encoding="utf-8")


def test_detailed_drive_inventory_is_never_uploaded_as_public_artifact():
    assert "actions/upload-artifact" not in WORKFLOW
    assert "detailed_artifact_uploaded=0" in WORKFLOW
    assert "drive-inventory.json" in WORKFLOW
    assert "drive-videos.csv" in WORKFLOW


def test_legacy_cleanup_is_exactly_scoped_to_steward_artifacts():
    assert 'prefix = "school-systems-steward-drive-"' in WORKFLOW
    assert 'if not name.startswith(prefix):' in WORKFLOW
    assert 'method="DELETE"' in WORKFLOW
    assert "actions: write" in WORKFLOW
    assert "contents: write" not in WORKFLOW


def test_drive_audit_remains_read_only_and_prs_receive_no_drive_secret():
    assert "github.event_name != 'pull_request'" in WORKFLOW
    assert "https://www.googleapis.com/upload" not in WORKFLOW
    assert "files.create" not in WORKFLOW
    assert "files.update" not in WORKFLOW
    assert "permissions.create" not in WORKFLOW
