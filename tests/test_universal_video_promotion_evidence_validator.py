from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "ops/validate_universal_video_promotion_evidence.py"
SPEC = importlib.util.spec_from_file_location("promotion_evidence_validator", MODULE_PATH)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)

COMMIT = "a" * 40
IMAGE_DIGEST = "sha256:" + "b" * 64


def _run() -> dict[str, object]:
    return {
        "id": 12345,
        "name": VALIDATOR.AUTHORITATIVE_WORKFLOW_NAME,
        "path": VALIDATOR.AUTHORITATIVE_WORKFLOW_PATH,
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "success",
        "head_sha": COMMIT,
        "actor": {"login": VALIDATOR.DIRECTOR_LOGIN},
        "triggering_actor": {"login": VALIDATOR.DIRECTOR_LOGIN},
        "repository": {"full_name": VALIDATOR.REPOSITORY_FULL_NAME},
    }


def _artifact(digest: str = "sha256:" + "c" * 64) -> dict[str, object]:
    return {
        "id": 67890,
        "name": f"{VALIDATOR.ARTIFACT_PREFIX}{COMMIT}",
        "expired": False,
        "digest": digest,
        "size_in_bytes": 4096,
        "workflow_run": {"id": 12345, "head_sha": COMMIT},
    }


def _gate(gate: str, **extra: object) -> str:
    value: dict[str, object] = {
        "gate": gate,
        "status": "PASS",
        "canonical_promotion_allowed": False,
        "publication_state": "NOT_PUBLISHED",
    }
    value.update(extra)
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _evidence() -> str:
    return "\n".join(
        [
            f"runtime_sha={COMMIT}",
            "UNIVERSAL_VIDEO_PRECANARY_WINDOW source_service_before=active "
            "container_service_before=inactive workload_fence=exclusive "
            "services_quiescent=true restore_on_exit=true",
            f"UNIVERSAL_VIDEO_PRECANARY_RUNTIME commit={COMMIT} image_digest={IMAGE_DIGEST}",
            f"UNIVERSAL_VIDEO_CONTAINER_INSTALL_PASS commit={COMMIT} "
            f"image_digest={IMAGE_DIGEST} activated=0",
            f"UNIVERSAL_VIDEO_PRECANARY_ATTEST_PASS commit={COMMIT} "
            f"image_digest={IMAGE_DIGEST} video_job_submitted=false "
            "drive_write_performed=false canonical_promotion_allowed=false "
            "publication_state=NOT_PUBLISHED",
            "UNIVERSAL_VIDEO_PRECANARY_RESTORE_PASS source_service_before=active "
            "source_service=active container_service_before=inactive "
            "container_target=inactive container_service=inactive prior_container_recovery=0",
            _gate("IMPORT_CLOSURE"),
            _gate(
                "SYNTHETIC_RESULT_CONTRACT",
                drive_write_performed=False,
                video_job_submitted=False,
            ),
            _gate(
                "SOURCE_IDENTITY_METADATA_ONLY",
                source_media_downloaded=False,
                video_job_submitted=False,
            ),
            f"image_digest={IMAGE_DIGEST}",
            "real_media_canary_run=false",
            "source_media_downloaded=false",
            "drive_write_performed=false",
            "automatic_batch_release=false",
            "canonical_promotion_allowed=false",
            "publication_state=NOT_PUBLISHED",
        ]
    ) + "\n"


def _archive_bytes(evidence: str | None = None, filename: str | None = None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(filename or VALIDATOR.EVIDENCE_FILENAME, evidence or _evidence())
    return output.getvalue()


def test_selects_only_exact_authoritative_director_run_artifact() -> None:
    artifact = _artifact()
    selected = VALIDATOR.select_authoritative_artifact(
        _run(), [{"artifacts": [artifact]}], COMMIT
    )
    assert selected == {
        "artifact_id": artifact["id"],
        "artifact_digest": artifact["digest"],
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "Oracle Universal Video Container Evidence"),
        ("path", ".github/workflows/oracle-universal-video-container-evidence.yml"),
        ("event", "pull_request"),
        ("conclusion", "failure"),
        ("head_sha", "d" * 40),
        ("actor", {"login": "not-the-director"}),
        ("triggering_actor", {"login": "not-the-director"}),
        ("repository", {"full_name": "other/repository"}),
    ],
)
def test_rejects_non_authoritative_or_unapproved_run(field: str, value: object) -> None:
    run = _run()
    run[field] = value
    with pytest.raises(VALIDATOR.EvidenceValidationError):
        VALIDATOR.select_authoritative_artifact(run, {"artifacts": [_artifact()]}, COMMIT)


def test_rejects_missing_ambiguous_or_unbound_artifacts() -> None:
    artifact = _artifact()
    for artifacts in (
        [],
        [artifact, copy.deepcopy(artifact)],
        [{**artifact, "workflow_run": {"id": 99999}}],
        [{**artifact, "digest": None}],
    ):
        with pytest.raises(VALIDATOR.EvidenceValidationError):
            VALIDATOR.select_authoritative_artifact(
                _run(), [{"artifacts": artifacts}], COMMIT
            )


def test_verifies_archive_digest_and_exact_runtime_receipt(tmp_path: Path) -> None:
    content = _archive_bytes()
    archive = tmp_path / "evidence.zip"
    archive.write_bytes(content)
    artifact_digest = "sha256:" + hashlib.sha256(content).hexdigest()
    VALIDATOR.verify_evidence_archive(archive, artifact_digest, COMMIT, IMAGE_DIGEST)


def test_rejects_tampered_or_ambiguous_archive_evidence(tmp_path: Path) -> None:
    valid = _archive_bytes()
    cases = [
        (valid, "sha256:" + "0" * 64),
        (
            _archive_bytes(_evidence().replace(f"image_digest={IMAGE_DIGEST}\n", "image_digest=sha256:" + "e" * 64 + "\n")),
            None,
        ),
        (_archive_bytes(_evidence() + f"runtime_sha={COMMIT}\n"), None),
        (_archive_bytes(filename="nested/evidence.txt"), None),
    ]
    for index, (content, forced_digest) in enumerate(cases):
        archive = tmp_path / f"evidence-{index}.zip"
        archive.write_bytes(content)
        digest = forced_digest or "sha256:" + hashlib.sha256(content).hexdigest()
        with pytest.raises(VALIDATOR.EvidenceValidationError):
            VALIDATOR.verify_evidence_archive(archive, digest, COMMIT, IMAGE_DIGEST)
