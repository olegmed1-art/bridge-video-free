"""Synthetic upload/readback/delete proof for an exact Drive result folder."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import uuid
from typing import Any

import requests

from .drive_adapter import DRIVE, access_token, file_metadata
from .drive_result_readback import DriveResultContractError, readback_artifact

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_IMAGE_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class DriveReadbackProbeError(RuntimeError):
    error_code = "UV_DRIVE_READBACK_PROBE_FAILED"


def _folder(folder_id: str, token: str) -> None:
    metadata = file_metadata(folder_id, token)
    if (
        str(metadata.get("id") or "") != folder_id
        or metadata.get("mimeType") != "application/vnd.google-apps.folder"
    ):
        raise DriveReadbackProbeError("PROBE_FOLDER_INVALID")


def _upload_json(folder_id: str, name: str, body: bytes, token: str) -> dict[str, Any]:
    metadata = {"name": name, "mimeType": "application/json", "parents": [folder_id]}
    response = requests.post(
        "https://www.googleapis.com/upload/drive/v3/files",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "uploadType": "multipart",
            "fields": "id,name,mimeType,size,parents,md5Checksum,sha1Checksum,sha256Checksum",
        },
        files={
            "metadata": ("metadata", json.dumps(metadata), "application/json; charset=UTF-8"),
            "file": (name, body, "application/json"),
        },
        timeout=60,
    )
    try:
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        raise DriveReadbackProbeError("PROBE_UPLOAD_FAILED") from exc
    if not isinstance(payload, dict) or not payload.get("id"):
        raise DriveReadbackProbeError("PROBE_UPLOAD_RECEIPT_INVALID")
    return dict(payload)


def _delete_and_verify(file_id: str, token: str) -> None:
    response = requests.delete(
        f"{DRIVE}/files/{file_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    try:
        response.raise_for_status()
    except Exception as exc:
        raise DriveReadbackProbeError("PROBE_DELETE_FAILED") from exc
    check = requests.get(
        f"{DRIVE}/files/{file_id}",
        headers={"Authorization": f"Bearer {token}"},
        params={"fields": "id,trashed"},
        timeout=30,
    )
    if check.status_code not in {404, 410}:
        raise DriveReadbackProbeError("PROBE_DELETE_READBACK_FAILED")


def run_probe(
    *,
    folder_id: str,
    runtime_sha: str,
    image_digest: str,
    token: str | None = None,
) -> dict[str, Any]:
    runtime_sha = runtime_sha.strip().lower()
    image_digest = image_digest.strip().lower()
    if not _COMMIT_RE.fullmatch(runtime_sha) or not _IMAGE_RE.fullmatch(image_digest):
        raise DriveReadbackProbeError("PROBE_RUNTIME_IDENTITY_INVALID")
    drive_token = token or access_token()
    _folder(folder_id, drive_token)
    probe_id = uuid.uuid4().hex
    name = f"UV_READBACK_PROBE_{runtime_sha[:12]}_{probe_id}.json"
    payload = {
        "schema": "universal-video-drive-readback-probe-v1",
        "runtime_sha": runtime_sha,
        "image_digest": image_digest,
        "synthetic": True,
        "real_media_read": False,
        "real_video_result_written": False,
        "canonical_promotion_allowed": False,
        "publication_state": "NOT_PUBLISHED",
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    upload: dict[str, Any] | None = None
    try:
        upload = _upload_json(folder_id, name, body, drive_token)
        locator, readback = readback_artifact(
            upload,
            token=drive_token,
            expected_parent_id=folder_id,
            expected_name=name,
            expected_mime_type="application/json",
            expected_sha256=hashlib.sha256(body).hexdigest(),
            max_bytes=1024 * 1024,
            collect=True,
        )
        if readback != body:
            raise DriveReadbackProbeError("PROBE_BODY_READBACK_MISMATCH")
        _delete_and_verify(str(upload["id"]), drive_token)
        return {
            "schema": "universal-video-drive-readback-probe-receipt-v1",
            "status": "PASS",
            "runtime_sha": runtime_sha,
            "image_digest": image_digest,
            "folder_id": folder_id,
            "probe_locator": locator,
            "probe_deleted": True,
            "drive_upload_gate": "PASS",
            "drive_readback_gate": "PASS",
            "checksum_gate": "PASS",
            "synthetic": True,
            "real_media_read": False,
            "real_video_result_written": False,
            "canonical_promotion_allowed": False,
            "publication_state": "NOT_PUBLISHED",
        }
    except DriveResultContractError as exc:
        raise DriveReadbackProbeError(str(exc)) from exc
    finally:
        if upload is not None:
            # Best-effort cleanup on a failure before the verified delete.
            try:
                requests.delete(
                    f"{DRIVE}/files/{upload['id']}",
                    headers={"Authorization": f"Bearer {drive_token}"},
                    timeout=30,
                )
            except Exception:
                pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder-id", required=True)
    parser.add_argument("--runtime-sha", required=True)
    parser.add_argument("--image-digest", required=True)
    args = parser.parse_args()
    try:
        receipt = run_probe(
            folder_id=args.folder_id,
            runtime_sha=args.runtime_sha,
            image_digest=args.image_digest,
        )
    except Exception as exc:
        print(json.dumps({
            "schema": "universal-video-drive-readback-probe-receipt-v1",
            "status": "BLOCKED",
            "error_code": getattr(exc, "error_code", "UV_DRIVE_READBACK_PROBE_FAILED"),
            "error_type": type(exc).__name__,
        }, sort_keys=True))
        return 1
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
