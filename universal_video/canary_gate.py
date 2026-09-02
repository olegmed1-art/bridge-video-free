"""Fail-closed pre/post processing gates for exactly one Universal Video canary.

This module intentionally contains no media-processing entry point. It is used
by :mod:`universal_video.one_canary` to prove source identity immediately before
processing and to require a real Drive byte readback before a queue job can be
finished successfully.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_IMAGE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CHECKSUM_RE = re.compile(r"^(md5|sha1|sha256):([0-9a-f]+)$")
_CHECKSUM_LENGTH = {"md5": 32, "sha1": 40, "sha256": 64}
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

PUBLICATION_STATE = "NOT_PUBLISHED"
CANONICAL_PROMOTION_ALLOWED = False
MANIFEST_SCHEMA = "bridge.universal_video.exactly_one_canary.v1"

SourceIdentity = dict[str, Any]
DownloadFn = Callable[..., Mapping[str, Any]]
UploadFn = Callable[..., Mapping[str, Any]]


class CanaryGateError(RuntimeError):
    """Raised whenever a canary invariant cannot be proven."""


def validate_runtime_sha(value: str) -> str:
    value = str(value or "").strip()
    if not _SHA_RE.fullmatch(value):
        raise CanaryGateError("runtime SHA must be exactly 40 lowercase hex characters")
    return value


def validate_image_digest(value: str) -> str:
    value = str(value or "").strip()
    if not _IMAGE_DIGEST_RE.fullmatch(value):
        raise CanaryGateError("image digest must be an immutable sha256:<64-hex> value")
    return value


def normalize_checksum(value: str) -> str:
    value = str(value or "").strip().lower()
    match = _CHECKSUM_RE.fullmatch(value)
    if not match:
        raise CanaryGateError("checksum must use md5:, sha1:, or sha256: with lowercase hex")
    algorithm, digest = match.groups()
    if len(digest) != _CHECKSUM_LENGTH[algorithm]:
        raise CanaryGateError(f"invalid {algorithm} checksum length")
    return f"{algorithm}:{digest}"


def source_identity_from_job(job: Mapping[str, Any]) -> SourceIdentity:
    """Return and validate the source identity carried by a claimed queue job."""

    try:
        size_bytes = int(job.get("source_size_bytes"))
    except (TypeError, ValueError) as exc:
        raise CanaryGateError("source_size_bytes must be a positive integer") from exc
    if size_bytes <= 0:
        raise CanaryGateError("source_size_bytes must be a positive integer")

    identity: SourceIdentity = {
        "file_id": str(job.get("source_file_id") or "").strip(),
        "name": str(job.get("source_name") or "").strip(),
        "mime_type": str(job.get("source_mime_type") or "").strip(),
        "size_bytes": size_bytes,
        "parent_id": str(job.get("source_folder_id") or "").strip(),
        "checksum": normalize_checksum(str(job.get("source_checksum") or "")),
    }
    missing = [key for key in ("file_id", "name", "mime_type", "parent_id") if not identity[key]]
    if missing:
        raise CanaryGateError("source identity missing: " + ", ".join(missing))
    if not identity["mime_type"].startswith("video/"):
        raise CanaryGateError("source MIME must be a video/* type")
    return identity


def observed_source_identity(metadata: Mapping[str, Any]) -> SourceIdentity:
    checksums: list[str] = []
    for algorithm, field in (
        ("sha256", "sha256Checksum"),
        ("sha1", "sha1Checksum"),
        ("md5", "md5Checksum"),
    ):
        value = str(metadata.get(field) or "").strip().lower()
        if value:
            checksums.append(normalize_checksum(f"{algorithm}:{value}"))
    if not checksums:
        raise CanaryGateError("Drive metadata exposes no source checksum")

    parents = [str(value) for value in (metadata.get("parents") or []) if str(value)]
    if len(parents) != 1:
        raise CanaryGateError("source must have exactly one expected Drive parent")
    try:
        size_bytes = int(metadata.get("size"))
    except (TypeError, ValueError) as exc:
        raise CanaryGateError("Drive metadata source size is unavailable") from exc
    if size_bytes <= 0:
        raise CanaryGateError("Drive metadata source size must be positive")
    return {
        "file_id": str(metadata.get("id") or "").strip(),
        "name": str(metadata.get("name") or "").strip(),
        "mime_type": str(metadata.get("mimeType") or "").strip(),
        "size_bytes": size_bytes,
        "parent_id": parents[0],
        "checksum": checksums[0],
        "available_checksums": checksums,
    }


def verify_source_snapshot(expected: Mapping[str, Any], metadata: Mapping[str, Any]) -> SourceIdentity:
    """Compare live Drive metadata with the expected identity.

    ``checksum="AUTO"`` is accepted only for the metadata-only preparation
    workflow: the strongest checksum returned by Drive becomes the exact
    recorded identity. A queue job and the actual processing command never
    accept AUTO; they must carry that resolved checksum explicitly.
    """

    checksum_value = str(expected.get("checksum") or "").strip()
    auto_checksum = checksum_value.upper() == "AUTO"
    placeholder_checksum = "md5:" + "0" * 32 if auto_checksum else checksum_value
    expected_identity = source_identity_from_job(
        {
            "source_file_id": expected.get("file_id"),
            "source_name": expected.get("name"),
            "source_mime_type": expected.get("mime_type"),
            "source_size_bytes": expected.get("size_bytes"),
            "source_folder_id": expected.get("parent_id"),
            "source_checksum": placeholder_checksum,
        }
    )
    if metadata.get("trashed") is True:
        raise CanaryGateError("source is trashed")
    observed = observed_source_identity(metadata)
    for key in ("file_id", "name", "mime_type", "size_bytes", "parent_id"):
        if observed[key] != expected_identity[key]:
            raise CanaryGateError(
                f"source identity mismatch for {key}: expected={expected_identity[key]!r}, "
                f"observed={observed[key]!r}"
            )
    if auto_checksum:
        expected_identity["checksum"] = observed["checksum"]
        return expected_identity
    if expected_identity["checksum"] not in observed["available_checksums"]:
        raise CanaryGateError("source checksum mismatch")
    return expected_identity


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    if size <= 0:
        raise CanaryGateError(f"readback file is empty: {path}")
    return digest.hexdigest()


def _metadata_parent(metadata: Mapping[str, Any]) -> str:
    parents = [str(value) for value in (metadata.get("parents") or []) if str(value)]
    if len(parents) != 1:
        raise CanaryGateError("Drive artifact must have exactly one parent")
    return parents[0]


def _default_download(
    *,
    file_id: str,
    destination: Path,
    token: str,
    expected_checksum: str,
) -> Mapping[str, Any]:
    from universal_video.drive_adapter import download_file

    return download_file(file_id, destination, token, expected_checksum=expected_checksum)


def _default_upload(
    *,
    local_path: Path,
    parent_id: str,
    remote_name: str,
    token: str,
) -> Mapping[str, Any]:
    from universal_video.drive_results import PublishArtifact, _upload_or_verify_file

    payload = local_path.read_bytes()
    artifact = PublishArtifact(
        local_path=local_path,
        relative_name=remote_name,
        mime_type="application/json",
        size_bytes=len(payload),
        md5=hashlib.md5(payload).hexdigest(),  # noqa: S324 - Drive exposes MD5 for identity.
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    return _upload_or_verify_file(parent_id, artifact, token)


def _readback_artifact(
    *,
    file_id: str,
    expected_sha256: str,
    expected_parent_id: str,
    token: str,
    destination: Path,
    downloader: DownloadFn,
) -> dict[str, Any]:
    expected_checksum = normalize_checksum(f"sha256:{expected_sha256}")
    try:
        metadata = downloader(
            file_id=file_id,
            destination=destination,
            token=token,
            expected_checksum=expected_checksum,
        )
    except Exception as exc:
        raise CanaryGateError(f"Drive readback failed for {file_id}: {exc}") from exc
    if not destination.is_file():
        raise CanaryGateError(f"Drive readback did not create a local file for {file_id}")
    actual_sha256 = _sha256_file(destination)
    if actual_sha256 != expected_sha256:
        raise CanaryGateError(
            f"Drive readback SHA-256 mismatch for {file_id}: "
            f"expected={expected_sha256}, observed={actual_sha256}"
        )
    if str(metadata.get("id") or "") != file_id:
        raise CanaryGateError("Drive readback returned metadata for a different file")
    if metadata.get("trashed") is True:
        raise CanaryGateError("Drive artifact became trashed during readback")
    if _metadata_parent(metadata) != expected_parent_id:
        raise CanaryGateError("Drive artifact parent mismatch")
    size_bytes = destination.stat().st_size
    try:
        metadata_size = int(metadata.get("size"))
    except (TypeError, ValueError) as exc:
        raise CanaryGateError("Drive artifact metadata size is unavailable") from exc
    if metadata_size != size_bytes:
        raise CanaryGateError("Drive artifact metadata/readback size mismatch")
    return {
        "drive_file_id": file_id,
        "name": str(metadata.get("name") or ""),
        "mime_type": str(metadata.get("mimeType") or ""),
        "parent_id": expected_parent_id,
        "size_bytes": size_bytes,
        "checksum": expected_checksum,
        "readback_sha256": actual_sha256,
        "readback_verified": True,
    }


def _safe_manifest_name(job: Mapping[str, Any], runtime_sha: str) -> str:
    key = str(job.get("stable_job_key") or job.get("id") or "canary").strip()
    key = _SAFE_NAME_RE.sub("-", key).strip("-.") or "canary"
    return f"UV_CANARY_MANIFEST_{key[:80]}_{runtime_sha[:12]}.json"


def apply_result_contract(
    *,
    job: Mapping[str, Any],
    processor_result: Mapping[str, Any],
    runtime_sha: str,
    image_digest: str,
    token: str | None = None,
    downloader: DownloadFn = _default_download,
    uploader: UploadFn = _default_upload,
) -> dict[str, Any]:
    """Require byte-readable Drive artifacts and publish a verified manifest.

    The returned object is safe to hand to ``video_queue.finish_job``. Any
    inability to read a final artifact or the manifest itself raises before a
    terminal queue update can occur.
    """

    runtime_sha = validate_runtime_sha(runtime_sha)
    image_digest = validate_image_digest(image_digest)
    source = source_identity_from_job(job)
    output_folder_id = str(job.get("output_folder_id") or "").strip()
    if not output_folder_id:
        raise CanaryGateError("output_folder_id is required")

    result: MutableMapping[str, Any] = dict(processor_result)
    if result.get("publication_state") != PUBLICATION_STATE:
        raise CanaryGateError("processor result must remain NOT_PUBLISHED")
    if result.get("canonical_promotion_allowed") is not CANONICAL_PROMOTION_ALLOWED:
        raise CanaryGateError("processor result attempted canonical promotion")

    master_file_id = str(result.get("master_pdf_drive_id") or "").strip()
    master_sha256 = str(result.get("master_pdf_sha256") or "").strip().lower()
    if not master_file_id:
        raise CanaryGateError("processor result has no master PDF Drive locator")
    if master_file_id == source["file_id"]:
        raise CanaryGateError("result locator points at the source media")
    if not re.fullmatch(r"[0-9a-f]{64}", master_sha256):
        raise CanaryGateError("processor result has no exact master PDF SHA-256")

    if token is None:
        from universal_video.drive_adapter import access_token

        token = access_token(os.environ.get("GOOGLE_OAUTH_JSON"))
    if not token:
        raise CanaryGateError("Drive access token is unavailable")

    with tempfile.TemporaryDirectory(prefix="uv-canary-contract-") as temp_root:
        root = Path(temp_root)
        master_receipt = _readback_artifact(
            file_id=master_file_id,
            expected_sha256=master_sha256,
            expected_parent_id=output_folder_id,
            token=token,
            destination=root / "masterPdf.readback.pdf",
            downloader=downloader,
        )
        master_receipt["kind"] = "masterPdf"

        manifest_core: dict[str, Any] = {
            "schema": MANIFEST_SCHEMA,
            "job_id": str(job.get("id") or ""),
            "stable_job_key": str(job.get("stable_job_key") or ""),
            "runtime_sha": runtime_sha,
            "image_digest": image_digest,
            "source_identity": source,
            "artifacts": [master_receipt],
            "publication_state": PUBLICATION_STATE,
            "canonical_promotion_allowed": CANONICAL_PROMOTION_ALLOWED,
            "source_reverified_immediately_before_processing": True,
            "drive_readback_required_for_terminal_success": True,
        }
        manifest_bytes = canonical_json_bytes(manifest_core)
        manifest_path = root / "canary_manifest.json"
        manifest_path.write_bytes(manifest_bytes)
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        remote_name = _safe_manifest_name(job, runtime_sha)
        try:
            upload_receipt = uploader(
                local_path=manifest_path,
                parent_id=output_folder_id,
                remote_name=remote_name,
                token=token,
            )
        except Exception as exc:
            raise CanaryGateError(f"manifest upload/verification failed: {exc}") from exc
        manifest_file_id = str(upload_receipt.get("file_id") or "").strip()
        if not manifest_file_id:
            raise CanaryGateError("manifest upload returned no Drive locator")
        upload_sha256 = str(upload_receipt.get("sha256") or "").strip().lower()
        if upload_sha256 != manifest_sha256:
            raise CanaryGateError("manifest upload receipt SHA-256 mismatch")

        manifest_readback_path = root / "manifest.readback.json"
        manifest_receipt = _readback_artifact(
            file_id=manifest_file_id,
            expected_sha256=manifest_sha256,
            expected_parent_id=output_folder_id,
            token=token,
            destination=manifest_readback_path,
            downloader=downloader,
        )
        if manifest_readback_path.read_bytes() != manifest_bytes:
            raise CanaryGateError("manifest readback bytes differ from canonical manifest")
        manifest_receipt.update(
            {
                "kind": "artifactManifest",
                "name": remote_name,
                "manifest_schema": MANIFEST_SCHEMA,
            }
        )

    artifact_locators = [master_receipt, manifest_receipt]
    result.update(
        {
            "runtime_sha": runtime_sha,
            "image_digest": image_digest,
            "publication_state": PUBLICATION_STATE,
            "canonical_promotion_allowed": CANONICAL_PROMOTION_ALLOWED,
            "artifact_locators": artifact_locators,
            "artifact_manifest": manifest_receipt,
            "terminal_receipt": {
                "schema": MANIFEST_SCHEMA,
                "runtime_sha": runtime_sha,
                "image_digest": image_digest,
                "source_identity": source,
                "manifest_drive_file_id": manifest_receipt["drive_file_id"],
                "manifest_sha256": manifest_receipt["readback_sha256"],
                "drive_readback_verified": True,
                "publication_state": PUBLICATION_STATE,
                "canonical_promotion_allowed": CANONICAL_PROMOTION_ALLOWED,
            },
        }
    )
    return dict(result)


def source_snapshot(
    *,
    file_id: str,
    expected: Mapping[str, Any],
    runtime_sha: str,
    image_digest: str,
    oauth_json: str | None = None,
) -> dict[str, Any]:
    """Metadata-only Drive source proof; source media bytes are never fetched."""

    from universal_video.drive_adapter import access_token, file_metadata

    runtime_sha = validate_runtime_sha(runtime_sha)
    image_digest = validate_image_digest(image_digest)
    token = access_token(oauth_json)
    metadata = file_metadata(file_id, token)
    identity = verify_source_snapshot(expected, metadata)
    return {
        "schema": MANIFEST_SCHEMA,
        "runtime_sha": runtime_sha,
        "image_digest": image_digest,
        "source_identity": identity,
        "metadata_only": True,
        "source_media_downloaded": False,
        "publication_state": PUBLICATION_STATE,
        "canonical_promotion_allowed": CANONICAL_PROMOTION_ALLOWED,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    snapshot = sub.add_parser("source-snapshot", help="verify Drive source metadata only")
    snapshot.add_argument("--file-id", required=True)
    snapshot.add_argument("--name", required=True)
    snapshot.add_argument("--mime-type", required=True)
    snapshot.add_argument("--size-bytes", required=True, type=int)
    snapshot.add_argument("--parent-id", required=True)
    snapshot.add_argument("--checksum", required=True)
    snapshot.add_argument("--runtime-sha", required=True)
    snapshot.add_argument("--image-digest", required=True)
    snapshot.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command != "source-snapshot":
        raise CanaryGateError(f"unsupported command: {args.command}")
    payload = source_snapshot(
        file_id=args.file_id,
        expected={
            "file_id": args.file_id,
            "name": args.name,
            "mime_type": args.mime_type,
            "size_bytes": args.size_bytes,
            "parent_id": args.parent_id,
            "checksum": args.checksum,
        },
        runtime_sha=args.runtime_sha,
        image_digest=args.image_digest,
        oauth_json=os.environ.get("GOOGLE_OAUTH_JSON"),
    )
    encoded = canonical_json_bytes(payload)
    if args.output:
        Path(args.output).write_bytes(encoded)
    print(encoded.decode("utf-8"), end="")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
