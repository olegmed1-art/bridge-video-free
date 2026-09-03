from __future__ import annotations

from pathlib import Path

import pytest

from ops.validate_video_queue_dsn import QueueDsnError, validate_dsn_text


ROOT = Path(__file__).resolve().parents[1]


def test_queue_dsn_parser_accepts_one_complete_postgresql_uri():
    validate_dsn_text("postgresql://worker:secret@db.example/neondb?sslmode=require")


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://[",
        "postgresql://worker:secret@db.example/neondb\n",
        "\npostgresql://worker:secret@db.example/neondb",
        "postgresql://first postgresql://second",
        "postgresql://db.example/neondb",
        "https://worker:secret@db.example/neondb",
        "postgresql://worker:secret@db.example/",
    ],
)
def test_queue_dsn_parser_fails_closed(value):
    with pytest.raises(QueueDsnError):
        validate_dsn_text(value)


def test_installer_binds_validated_queue_credential_without_disclosure():
    installer = (ROOT / "ops/oracle_universal_video_container_install.sh").read_text(
        encoding="utf-8"
    )
    credential_gate = installer.index('if [[ "$ACTIVATE" == 1 ]]; then')
    service_activation = installer.rindex('if [[ "$ACTIVATE" == 1 ]]; then')
    gate = installer[credential_gate:service_activation]
    assert 'queue_dsn_file="$BASE_DIR/secrets/video-queue-dsn"' in installer
    assert '[[ -f "$queue_dsn_file" && ! -L "$queue_dsn_file" ]]' in gate
    assert "protected video queue credential metadata invalid" in gate
    assert "BASH_REMATCH[1] <= 4096" in gate
    assert 'validate_video_queue_dsn.py" "$queue_dsn_file"' in gate
    assert "BRIDGE_VIDEO_QUEUE_DATABASE_URL_FILE=/run/secrets/video-queue-dsn" in installer
    assert "cat \"$queue_dsn_file\"" not in installer
    assert "printf '%s' \"$queue_dsn" not in installer


def test_credential_only_workflow_is_owner_gated_and_never_mutates_services():
    workflow = (
        ROOT / ".github/workflows/oracle-universal-video-queue-credential-install.yml"
    ).read_text(encoding="utf-8")
    assert "director_go:" in workflow
    assert "github.actor == github.repository_owner" in workflow
    assert "github.triggering_actor == github.repository_owner" in workflow
    assert 'ref: ${{ env.EXACT_SHA }}' in workflow
    assert "BRIDGE_WORKER_DATABASE_URL" in workflow
    assert "validate_video_queue_dsn.py" in workflow
    assert "root:universal-video:640" in workflow
    assert "service_mutation=false" in workflow
    assert "docker_mutation=false" in workflow
    assert "media_execution=false" in workflow
    assert "systemctl start" not in workflow
    assert "systemctl stop" not in workflow
    assert "systemctl restart" not in workflow
    assert "docker run" not in workflow
