"""Fail-closed two-artifact terminal evidence for Issue #881."""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping

from .drive_adapter import download_file, file_metadata, list_folder_files

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CHECKSUM_RE = re.compile(r"^(?:md5:[0-9a-f]{32}|sha1:[0-9a-f]{40}|sha256:[0-9a-f]{64})$")
MAX_OBSERVATIONS_BYTES = 32 * 1024 * 1024
# Full unchanged JSONL is carried by AI_DONE. JSON quoting can double its size;
# retain room for the pre-existing bounded report metadata as well.
MAX_AI_DONE_BYTES = 80 * 1024 * 1024
PRIVATE_AI_DONE_BYTES = 4 * 1024 * 1024


class TerminalEvidenceV2Error(RuntimeError):
    error_code = "UV_TERMINAL_EVIDENCE_V2_FAILED"

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code if not detail else f"{code}: {detail}")
        self.error_code = code


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def source_identity_from_claim(claim: Mapping[str, Any]) -> dict[str, Any]:
    try:
        size = int(claim.get("source_size_bytes"))
    except (TypeError, ValueError) as exc:
        raise TerminalEvidenceV2Error("UV_SOURCE_IDENTITY_SIZE_INVALID") from exc
    raw_checksum = claim.get("source_checksum")
    checksum = None if raw_checksum is None else str(raw_checksum).strip().lower()
    identity = {
        "file_id": str(claim.get("source_file_id") or ""),
        "name": str(claim.get("source_name") or ""),
        "mime_type": str(claim.get("source_mime_type") or ""),
        "size_bytes": size,
        "parent_folder_id": str(claim.get("source_folder_id") or ""),
        # Migration 0056 deliberately permits providers that expose no content
        # checksum. Preserve that absence as JSON null; malformed non-null
        # values still fail closed.
        "checksum": checksum,
    }
    if (
        not identity["file_id"]
        or not identity["name"]
        or not identity["mime_type"]
        or identity["size_bytes"] <= 0
        or not identity["parent_folder_id"]
        or (checksum is not None and not _CHECKSUM_RE.fullmatch(checksum))
    ):
        raise TerminalEvidenceV2Error("UV_SOURCE_IDENTITY_INVALID")
    return identity


def _metadata(meta: Mapping[str, Any]) -> dict[str, Any]:
    try:
        size = int(meta.get("size"))
    except (TypeError, ValueError) as exc:
        raise TerminalEvidenceV2Error("UV_TERMINAL_METADATA_SIZE_INVALID") from exc
    return {
        "id": str(meta.get("id") or ""),
        "name": str(meta.get("name") or ""),
        "mime_type": str(meta.get("mimeType") or ""),
        "size_bytes": size,
        "parents": [str(v) for v in (meta.get("parents") or []) if v],
        "modified_time": str(meta.get("modifiedTime") or ""),
        "version": str(meta.get("version") or ""),
        "md5_checksum": str(meta.get("md5Checksum") or "").strip().lower(),
        "sha1_checksum": str(meta.get("sha1Checksum") or "").strip().lower(),
        "sha256_checksum": str(meta.get("sha256Checksum") or "").strip().lower(),
    }


def _reject_trashed(meta: Mapping[str, Any]) -> None:
    # The real Drive adapter explicitly requests this field. A positive trash
    # state is never eligible terminal evidence, even while direct reads still
    # succeed for a recoverable trashed object.
    if meta.get("trashed") is True:
        raise TerminalEvidenceV2Error("UV_TERMINAL_ARTIFACT_TRASHED")


def _read_live(
    file_id: str,
    *,
    token: str,
    expected_parent: str,
    expected_mime: str,
    suffix: str,
    max_bytes: int,
    metadata_reader: Callable[[str, str], Mapping[str, Any]],
    downloader: Callable[..., Mapping[str, Any]],
) -> tuple[dict[str, Any], bytes]:
    try:
        before_raw = dict(metadata_reader(file_id, token))
        _reject_trashed(before_raw)
        before = _metadata(before_raw)
    except TerminalEvidenceV2Error:
        raise
    except Exception as exc:
        raise TerminalEvidenceV2Error("UV_TERMINAL_METADATA_READ_FAILED") from exc
    if (
        before["id"] != file_id
        or not before["name"]
        or before["mime_type"] != expected_mime
        or not 0 < before["size_bytes"] <= max_bytes
        or before["parents"] != [expected_parent]
        or not before["modified_time"]
        or not before["version"]
    ):
        raise TerminalEvidenceV2Error("UV_TERMINAL_METADATA_MISMATCH")

    with tempfile.TemporaryDirectory(prefix="uv-terminal-v2-") as td:
        destination = Path(td) / f"artifact{suffix}"
        try:
            downloaded_raw = dict(
                downloader(
                    file_id,
                    destination,
                    token,
                    max_bytes=max_bytes,
                    metadata=before_raw,
                )
            )
            _reject_trashed(downloaded_raw)
        except TerminalEvidenceV2Error:
            raise
        except Exception as exc:
            raise TerminalEvidenceV2Error("UV_TERMINAL_DRIVE_READBACK_FAILED") from exc
        downloaded = _metadata(downloaded_raw)
        actual_sha = str(downloaded_raw.get("_download_sha256") or "").strip().lower()
        if (
            downloaded != before
            or not destination.is_file()
            or destination.stat().st_size != before["size_bytes"]
            or not _SHA256_RE.fullmatch(actual_sha)
        ):
            raise TerminalEvidenceV2Error("UV_TERMINAL_READBACK_MISMATCH")
        payload = destination.read_bytes()
        if hashlib.sha256(payload).hexdigest() != actual_sha:
            raise TerminalEvidenceV2Error("UV_TERMINAL_CHECKSUM_MISMATCH")

    try:
        after_raw = dict(metadata_reader(file_id, token))
        _reject_trashed(after_raw)
        after = _metadata(after_raw)
    except TerminalEvidenceV2Error:
        raise
    except Exception as exc:
        raise TerminalEvidenceV2Error("UV_TERMINAL_METADATA_REREAD_FAILED") from exc
    if after != before:
        raise TerminalEvidenceV2Error("UV_TERMINAL_METADATA_CHANGED")

    return {
        "drive_id": file_id,
        "name": before["name"],
        "mime_type": before["mime_type"],
        "size_bytes": before["size_bytes"],
        "parent_id": expected_parent,
        # Bind the attestation to the exact Drive revision, not only to the
        # current bytes. Drive can replace an object with identical content
        # while advancing these fields; the terminal re-verification must
        # detect that replacement.
        "modified_time": before["modified_time"],
        "version": before["version"],
        "sha256": actual_sha,
    }, payload


def _validate_done(claim: Mapping[str, Any], done: Mapping[str, Any]) -> None:
    if (
        done.get("status") != "AI_DONE"
        or str(done.get("job_id") or "") != str(claim.get("stable_job_key") or "")
        or str(done.get("algorithmRevision") or "") != str(claim.get("algorithm_revision") or "")
        or str((done.get("original") or {}).get("driveId") or "") != str(claim.get("source_file_id") or "")
    ):
        raise TerminalEvidenceV2Error("UV_AI_DONE_IDENTITY_MISMATCH")


def _observation_provenance(claim, binding, sha256, size, frames):
    from .diana20_route import CLAIM_KEYS
    from . import diana20_recognizer as frame
    contract = frame.component()
    binding.check(claim)
    if (binding.recognizer_job_id != contract["job_id"]
            or binding.source_sha256 != contract["source_sha256"]
            or not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256)
            or type(size) is not int or not 0 < size <= MAX_OBSERVATIONS_BYTES
            or type(frames) is not int or not 1 <= frames <= 232):
        raise TerminalEvidenceV2Error("UV_OBSERVATIONS_BINDING_INVALID")
    return {
        "schema_version": "diana20-sealed-observations/v1",
        "claim": {key: getattr(binding, key) for key in CLAIM_KEYS},
        "recognizer_job_id": binding.recognizer_job_id,
        "source_sha256": binding.source_sha256,
        "component_sha256": frame.COMPONENT_SHA256,
        "sha256": sha256, "size_bytes": size, "frame_count": frames,
        "result_mode": "SHADOW_REVIEW_ONLY",
        "canonical_promotion_allowed": False, "database_persistence_allowed": False,
        "publication_state": "NOT_PUBLISHED",
        "report_relationship": "SUPPLEMENT_TO_COMPLETED_REPORT",
        "master_pdf_recomputed": False,
    }


def _validate_observation_bytes(claim, provenance, raw):
    """Verify every original envelope; never infer missing cards or canon."""
    from uuid import UUID
    from .diana20_route import ClaimBinding, CLAIM_KEYS
    from . import diana20_recognizer as frame
    try:
        if not isinstance(provenance, Mapping) or not isinstance(raw, bytes):
            raise ValueError()
        bound = provenance["claim"]
        if not isinstance(bound, Mapping) or set(bound) != set(CLAIM_KEYS):
            raise ValueError()
        for key in ("job_id", "batch_id", "lease_token"):
            if type(bound[key]) is not str or str(UUID(bound[key])) != bound[key]:
                raise ValueError()
        if (type(bound["attempt_count"]) is not int or not 1 <= bound["attempt_count"] <= 3
                or type(bound["source_file_id"]) is not str or not bound["source_file_id"]):
            raise ValueError()
        binding = ClaimBinding(**bound, recognizer_job_id=provenance["recognizer_job_id"],
                               source_sha256=provenance["source_sha256"])
        expected = _observation_provenance(claim, binding, provenance["sha256"],
                    provenance["size_bytes"], provenance["frame_count"])
        if dict(provenance) != expected:
            raise ValueError()
        if (len(raw) != expected["size_bytes"]
                or hashlib.sha256(raw).hexdigest() != expected["sha256"]
                or not raw.endswith(b"\n")):
            raise ValueError()
        lines = raw.split(b"\n")[:-1]
        if len(lines) != expected["frame_count"]:
            raise ValueError()
        contract = frame.component()
        seen = set()
        for line in lines:
            if not line or len(line) + 1 > frame.MAX_RESPONSE:
                raise ValueError()
            value = frame._json(line)
            if not isinstance(value, dict) or not isinstance(value.get("result"), dict):
                raise ValueError()
            key, result = value.get("id"), value["result"]
            if (type(key) is not str or not 0 < len(key) <= 256 or key in seen
                    or not isinstance(result.get("frame_sha256"), str)
                    or not _SHA256_RE.fullmatch(result["frame_sha256"])
                    or type(result.get("source_timestamp_ms")) is not int
                    or result["source_timestamp_ms"] < 0):
                raise ValueError()
            seen.add(key)
            frame.validate_response(value, {"id": key,
                "frame_sha256": result["frame_sha256"],
                "source_sha256": binding.source_sha256,
                "timestamp_ms": result["source_timestamp_ms"]}, contract)
        return expected
    except TerminalEvidenceV2Error:
        raise
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise TerminalEvidenceV2Error("UV_OBSERVATIONS_INVALID") from exc


def read_sealed_observations(claim, sealed):
    """Accept only the route's typed, completed receipt and stable private file."""
    from .diana20_route import ObservationReceipt
    from . import diana20_recognizer as frame
    if type(sealed) is not ObservationReceipt:
        raise TerminalEvidenceV2Error("UV_OBSERVATIONS_RECEIPT_REQUIRED")
    provenance = _observation_provenance(claim, sealed.claim, sealed.sha256,
                                         sealed.size, sealed.frames)
    try:
        raw = frame._read(sealed.path, MAX_OBSERVATIONS_BYTES, owner=True, private=True)
        _validate_observation_bytes(claim, provenance, raw)
        return {"provenance": provenance, "jsonl": raw.decode("utf-8")}
    except RuntimeError as exc:
        raise TerminalEvidenceV2Error("UV_OBSERVATIONS_INVALID") from exc


def _done_observations(claim, done):
    payload = done.get("savedFrameObservations")
    if not isinstance(payload, dict) or set(payload) != {"provenance", "jsonl"}:
        raise TerminalEvidenceV2Error("UV_OBSERVATIONS_MISSING")
    if not isinstance(payload["jsonl"], str):
        raise TerminalEvidenceV2Error("UV_OBSERVATIONS_INVALID")
    provenance = _validate_observation_bytes(claim, payload["provenance"],
                                             payload["jsonl"].encode("utf-8"))
    original = done.get("original") or {}
    identity = source_identity_from_claim(claim)
    if any(original.get(key) != value for key, value in {
        "driveId": identity["file_id"], "name": identity["name"],
        "mimeType": identity["mime_type"], "sizeBytes": identity["size_bytes"],
        "parentFolderId": identity["parent_folder_id"], "sha256": provenance["source_sha256"],
    }.items()):
        raise TerminalEvidenceV2Error("UV_OBSERVATIONS_SOURCE_MISMATCH")
    return provenance


def sealed_ai_done_name(claim):
    # Distinct from the completed baseline's stable AI_DONE. The exact lease
    # name is create-only too; a terminal failure cannot be resumed silently.
    return (f"AI_DONE_{claim['stable_job_key']}_diana20_a{claim['attempt_count']}_"
            f"{claim['lease_token']}.json")


def _baseline_readback(claim, done, token, *, metadata_reader, downloader):
    """Verify the exact previously completed report, including Drive revision."""
    expected = done.get("completedReportBaseline")
    if not isinstance(expected, Mapping):
        raise TerminalEvidenceV2Error("UV_COMPLETED_REPORT_BASELINE_MISSING")
    file_id = str(expected.get("drive_id") or "")
    if not file_id or file_id in {claim.get("source_file_id"), (done.get("masterPdf") or {}).get("driveId")}:
        raise TerminalEvidenceV2Error("UV_COMPLETED_REPORT_BASELINE_MISMATCH")
    baseline, raw = _read_live(file_id, token=token,
        expected_parent=str(claim.get("output_folder_id") or ""),
        expected_mime="application/json", suffix=".json", max_bytes=8 * 1024 * 1024,
        metadata_reader=metadata_reader, downloader=downloader)
    from .diana20_recognizer import _json
    wanted = {key: value for key, value in done.items()
              if key not in {"savedFrameObservations", "completedReportBaseline"}}
    if (baseline != dict(expected)
            or baseline["name"] != f"AI_DONE_{claim['stable_job_key']}.json"
            or _canonical_json(_json(raw.removeprefix(b"\xef\xbb\xbf"))) != _canonical_json(wanted)):
        raise TerminalEvidenceV2Error("UV_COMPLETED_REPORT_BASELINE_MISMATCH")
    return baseline


def consume_sealed_report(claim, completed_done, sealed, token, *,
                          metadata_reader=file_metadata, downloader=download_file,
                          folder_lister=list_folder_files, uploader=None,
                          _private_bytes=False):
    """Supplement an already completed report, without invoking any media work.

    The supplied mapping must equal the exact live completed AI_DONE. Both it
    and its PDF must already be in the approved output folder. The existing
    application scope must serialize this writer: Drive names are not unique
    and the absence check is not an atomic create primitive. An existing lease
    supplement is refused. Full observations live in that new AI_DONE, while
    the PDF remains the previous completed report, explicitly identified as such.
    """
    if not isinstance(completed_done, Mapping):
        raise TerminalEvidenceV2Error("UV_SEALED_REPORT_INPUTS_REQUIRED")
    _validate_done(claim, completed_done)
    if "savedFrameObservations" in completed_done or "completedReportBaseline" in completed_done:
        raise TerminalEvidenceV2Error("UV_SEALED_REPORT_ALREADY_SUPPLEMENTED")
    done = {**completed_done, "savedFrameObservations": read_sealed_observations(claim, sealed)}
    _done_observations(claim, done)
    folder = str(claim.get("output_folder_id") or "")
    pdf = done.get("masterPdf") or {}
    master_id = str(pdf.get("driveId") or "")
    if not folder or not master_id or master_id == claim.get("source_file_id"):
        raise TerminalEvidenceV2Error("UV_TERMINAL_LOCATOR_INVALID")
    master, _ = _read_live(master_id, token=token, expected_parent=folder,
        expected_mime="application/pdf", suffix=".pdf", max_bytes=512 * 1024 * 1024,
        metadata_reader=metadata_reader, downloader=downloader)
    if master["sha256"] != pdf.get("sha256"):
        raise TerminalEvidenceV2Error("UV_MASTER_PDF_READBACK_MISMATCH")
    from .route_receipt_v2 import discover_route_receipt
    baseline_route = discover_route_receipt(claim, completed_done, token,
        folder_lister=folder_lister, metadata_reader=metadata_reader)
    baseline, _ = _read_live(baseline_route["ai_done_drive_id"], token=token,
        expected_parent=folder, expected_mime="application/json", suffix=".json",
        max_bytes=8 * 1024 * 1024, metadata_reader=metadata_reader, downloader=downloader)
    done["completedReportBaseline"] = baseline
    _baseline_readback(claim, done, token, metadata_reader=metadata_reader, downloader=downloader)
    name = sealed_ai_done_name(claim)
    items = folder_lister(folder, token)
    if (not isinstance(items, list) or any(not isinstance(item, Mapping) for item in items)
            or any(item.get("name") == name for item in items)):
        raise TerminalEvidenceV2Error("UV_SEALED_REPORT_OUTPUT_CONFLICT")
    # Serialize before any write, including the size bound used by the existing
    # Drive JSON uploader (indent=2, ensure_ascii=False).
    payload = json.dumps(done, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
    # The sealed supplement is also the private-stage object. Keep the stricter
    # existing private budget before ANY upload; the ordinary 80 MiB envelope
    # is not permission to enlarge this payload.
    if len(payload) > PRIVATE_AI_DONE_BYTES:
        raise TerminalEvidenceV2Error("UV_AI_DONE_SIZE_INVALID")
    if read_sealed_observations(claim, sealed) != done["savedFrameObservations"]:
        raise TerminalEvidenceV2Error("UV_OBSERVATIONS_CHANGED")
    if uploader is None:
        import run_drive_3_1_free as io
        uploader = io.upload_bytes if _private_bytes else io.upload_json
    from .owner_effects import terminal_effect
    with terminal_effect(claim, required=True):
        uploaded = (uploader(token, folder, name, payload, 'application/json')
                    if _private_bytes else uploader(token, folder, name, done))
    if (not isinstance(uploaded, Mapping) or not uploaded.get("id")
            or uploaded["id"] in {claim.get("source_file_id"), master_id}):
        raise TerminalEvidenceV2Error("UV_AI_DONE_UPLOAD_UNCONFIRMED")
    if _private_bytes:
        with terminal_effect(claim,required=True):
            readback, raw = _read_live(str(uploaded['id']),token=token,expected_parent=folder,
                expected_mime='application/json',suffix='.json',max_bytes=PRIVATE_AI_DONE_BYTES,
                metadata_reader=metadata_reader,downloader=downloader)
            if readback['name'] != name or raw != payload:
                raise TerminalEvidenceV2Error('UV_PRIVATE_UPLOAD_BYTES_MISMATCH')
    return done


def consume_sealed_report_private(claim, completed_done, sealed, token, *,
                                  metadata_reader=file_metadata, downloader=download_file,
                                  folder_lister=list_folder_files, byte_uploader=None):
    """Private producer: serialize once, check 4 MiB, upload/read exact bytes.

    Explicit source port only; no launcher/policy is enrolled by importing it.
    """
    return consume_sealed_report(claim,completed_done,sealed,token,
        metadata_reader=metadata_reader,downloader=downloader,folder_lister=folder_lister,
        uploader=byte_uploader,_private_bytes=True)


def _build(
    claim: Mapping[str, Any],
    done: Mapping[str, Any],
    master_id: str,
    ai_done_id: str,
    token: str,
    *,
    metadata_reader: Callable[[str, str], Mapping[str, Any]],
    downloader: Callable[..., Mapping[str, Any]],
    max_ai_done_bytes: int = MAX_AI_DONE_BYTES,
) -> dict[str, Any]:
    _validate_done(claim, done)
    source = source_identity_from_claim(claim)
    output_folder = str(claim.get("output_folder_id") or "")
    if not output_folder or not master_id or not ai_done_id or len({source["file_id"], master_id, ai_done_id}) != 3:
        raise TerminalEvidenceV2Error("UV_TERMINAL_LOCATOR_INVALID")

    master, master_bytes = _read_live(
        master_id,
        token=token,
        expected_parent=output_folder,
        expected_mime="application/pdf",
        suffix=".pdf",
        max_bytes=512 * 1024 * 1024,
        metadata_reader=metadata_reader,
        downloader=downloader,
    )
    ai, ai_bytes = _read_live(
        ai_done_id,
        token=token,
        expected_parent=output_folder,
        expected_mime="application/json",
        suffix=".json",
        max_bytes=max_ai_done_bytes,
        metadata_reader=metadata_reader,
        downloader=downloader,
    )
    try:
        from .diana20_recognizer import _json
        live_done = _json(ai_bytes.removeprefix(b"\xef\xbb\xbf"))
    except RuntimeError as exc:
        raise TerminalEvidenceV2Error("UV_AI_DONE_READBACK_INVALID") from exc
    if not isinstance(live_done, dict) or _canonical_json(live_done) != _canonical_json(dict(done)):
        raise TerminalEvidenceV2Error("UV_AI_DONE_READBACK_MISMATCH")

    expected_master_sha = str((done.get("masterPdf") or {}).get("sha256") or "").strip().lower()
    expected_master_id = str((done.get("masterPdf") or {}).get("driveId") or "")
    if expected_master_id != master_id or not _SHA256_RE.fullmatch(expected_master_sha) or expected_master_sha != master["sha256"]:
        raise TerminalEvidenceV2Error("UV_MASTER_PDF_READBACK_MISMATCH")

    artifacts = [
        {"kind": "master_pdf", "locator": f"gdrive:file:{master_id}", **master},
        {"kind": "ai_done", "locator": f"gdrive:file:{ai_done_id}", **ai},
    ]
    manifest = {
        "schema_version": "universal-video-artifact-manifest/v1",
        "job_id": str(claim.get("stable_job_key") or ""),
        "source_file_id": source["file_id"],
        "algorithm_revision": str(claim.get("algorithm_revision") or ""),
        "result_mode": "SHADOW_REVIEW_ONLY",
        "canonical_promotion_allowed": False,
        "database_persistence_allowed": False,
        "publication_state": "NOT_PUBLISHED",
        "source_identity": source,
        "artifacts": artifacts,
    }
    observations = None
    baseline = None
    if "savedFrameObservations" in done:
        observations = _done_observations(claim, done)
        if ai["name"] != sealed_ai_done_name(claim):
            raise TerminalEvidenceV2Error("UV_OBSERVATIONS_TERMINAL_MISMATCH")
        baseline = _baseline_readback(claim, done, token,
            metadata_reader=metadata_reader, downloader=downloader)
        if baseline["drive_id"] == ai_done_id:
            raise TerminalEvidenceV2Error("UV_COMPLETED_REPORT_BASELINE_MISMATCH")
        manifest["saved_frame_observations"] = observations
        manifest["completed_report_baseline"] = baseline
    manifest_sha = _canonical_sha256(manifest)
    receipt_core = {
        "schema_version": "universal-video-terminal-receipt/v1",
        "status": "PASS",
        "job_id": manifest["job_id"],
        "source_file_id": source["file_id"],
        "source_identity_verified": True,
        "drive_readback_verified": True,
        "result_readback_verified": True,
        "checksum_verified": True,
        "artifact_count": 2,
        "artifact_manifest_sha256": manifest_sha,
        "canonical_promotion_allowed": False,
        "database_persistence_allowed": False,
        "publication_state": "NOT_PUBLISHED",
    }
    if observations is not None:
        receipt_core["observation_stream_verified"] = True
        receipt_core["observation_stream_sha256"] = observations["sha256"]
        receipt_core["completed_report_baseline_sha256"] = baseline["sha256"]
    evidence_sha = _canonical_sha256(receipt_core)
    receipt = {**receipt_core, "evidence_sha256": evidence_sha}
    return {
        "result_mode": "SHADOW_REVIEW_ONLY",
        "canonical_promotion_allowed": False,
        "database_persistence_allowed": False,
        "publication_state": "NOT_PUBLISHED",
        "source_file_id": source["file_id"],
        "stable_job_key": manifest["job_id"],
        "algorithm_revision": manifest["algorithm_revision"],
        "master_pdf_drive_id": master_id,
        "master_pdf_sha256": master["sha256"],
        "ai_done_drive_id": ai_done_id,
        "ai_done_sha256": ai["sha256"],
        "artifact_locators": {"master_pdf": master_id, "ai_done": ai_done_id},
        "artifact_manifest": manifest,
        "artifact_manifest_sha256": manifest_sha,
        "terminal_receipt": receipt,
        "terminal_evidence_sha256": evidence_sha,
        **({"saved_frame_observations": observations} if observations is not None else {}),
        **({"completed_report_baseline": baseline} if baseline is not None else {}),
    }


def build_terminal_evidence(
    claim: Mapping[str, Any],
    done: Mapping[str, Any],
    route_receipt: Mapping[str, Any],
    token: str,
    *,
    metadata_reader: Callable[[str, str], Mapping[str, Any]] = file_metadata,
    downloader: Callable[..., Mapping[str, Any]] = download_file,
    sealed_observations=None,
) -> dict[str, Any]:
    if (
        route_receipt.get("schema_version") != "universal-video-route-receipt/v2"
        or str(route_receipt.get("job_id") or "") != str(claim.get("stable_job_key") or "")
        or str(route_receipt.get("source_file_id") or "") != str(claim.get("source_file_id") or "")
        or str(route_receipt.get("output_folder_id") or "") != str(claim.get("output_folder_id") or "")
    ):
        raise TerminalEvidenceV2Error("UV_ROUTE_RECEIPT_BINDING_INVALID")
    if sealed_observations is not None:
        expected = read_sealed_observations(claim, sealed_observations)
        if done.get("savedFrameObservations") != expected:
            raise TerminalEvidenceV2Error("UV_OBSERVATIONS_CONSUMPTION_MISMATCH")
    return _build(
        claim,
        done,
        str(route_receipt.get("master_pdf_drive_id") or ""),
        str(route_receipt.get("ai_done_drive_id") or ""),
        token,
        metadata_reader=metadata_reader,
        downloader=downloader,
    )


def validate_terminal_output(claim: Mapping[str, Any], output: Mapping[str, Any]) -> None:
    required = (
        "master_pdf_drive_id", "master_pdf_sha256", "ai_done_drive_id", "ai_done_sha256",
        "artifact_locators", "artifact_manifest", "artifact_manifest_sha256",
        "terminal_receipt", "terminal_evidence_sha256",
    )
    if any(key not in output for key in required):
        raise TerminalEvidenceV2Error("UV_TERMINAL_OUTPUT_INCOMPLETE")
    manifest = output.get("artifact_manifest")
    receipt = output.get("terminal_receipt")
    locators = output.get("artifact_locators")
    if not isinstance(manifest, Mapping) or not isinstance(receipt, Mapping) or not isinstance(locators, Mapping):
        raise TerminalEvidenceV2Error("UV_TERMINAL_OUTPUT_TYPE_INVALID")
    manifest_sha = str(output.get("artifact_manifest_sha256") or "").lower()
    evidence_sha = str(output.get("terminal_evidence_sha256") or "").lower()
    if not _SHA256_RE.fullmatch(manifest_sha) or _canonical_sha256(manifest) != manifest_sha:
        raise TerminalEvidenceV2Error("UV_TERMINAL_MANIFEST_HASH_INVALID")
    core = dict(receipt)
    receipt_evidence = str(core.pop("evidence_sha256", "") or "").lower()
    if not _SHA256_RE.fullmatch(evidence_sha) or receipt_evidence != evidence_sha or _canonical_sha256(core) != evidence_sha:
        raise TerminalEvidenceV2Error("UV_TERMINAL_RECEIPT_HASH_INVALID")
    source = source_identity_from_claim(claim)
    if (
        output.get("result_mode") != "SHADOW_REVIEW_ONLY"
        or output.get("canonical_promotion_allowed") is not False
        or output.get("database_persistence_allowed") is not False
        or output.get("publication_state") != "NOT_PUBLISHED"
        or output.get("source_file_id") != claim.get("source_file_id")
        or output.get("stable_job_key") != claim.get("stable_job_key")
        or output.get("algorithm_revision") != claim.get("algorithm_revision")
        or manifest.get("source_identity") != source
        or manifest.get("job_id") != claim.get("stable_job_key")
        or manifest.get("source_file_id") != claim.get("source_file_id")
        or manifest.get("algorithm_revision") != claim.get("algorithm_revision")
        or receipt.get("status") != "PASS"
        or receipt.get("source_identity_verified") is not True
        or receipt.get("drive_readback_verified") is not True
        or receipt.get("result_readback_verified") is not True
        or receipt.get("checksum_verified") is not True
        or receipt.get("artifact_count") != 2
        or receipt.get("artifact_manifest_sha256") != manifest_sha
        or locators.get("master_pdf") != output.get("master_pdf_drive_id")
        or locators.get("ai_done") != output.get("ai_done_drive_id")
    ):
        raise TerminalEvidenceV2Error("UV_TERMINAL_OUTPUT_BINDING_INVALID")
    observation_fields = (
        "saved_frame_observations" in output,
        "saved_frame_observations" in manifest,
        "observation_stream_verified" in receipt,
        "observation_stream_sha256" in receipt,
        "completed_report_baseline" in output,
        "completed_report_baseline" in manifest,
        "completed_report_baseline_sha256" in receipt,
    )
    if any(observation_fields):
        provenance = output.get("saved_frame_observations")
        if (not all(observation_fields) or not isinstance(provenance, Mapping)
                or manifest.get("saved_frame_observations") != provenance
                or receipt.get("observation_stream_verified") is not True
                or receipt.get("observation_stream_sha256") != provenance.get("sha256")
                or not isinstance(output.get("completed_report_baseline"), Mapping)
                or manifest.get("completed_report_baseline") != output["completed_report_baseline"]
                or receipt.get("completed_report_baseline_sha256") != output["completed_report_baseline"].get("sha256")):
            raise TerminalEvidenceV2Error("UV_OBSERVATIONS_TERMINAL_MISMATCH")


def reverify_terminal_output_live(
    claim: Mapping[str, Any],
    output: Mapping[str, Any],
    token: str,
    *,
    metadata_reader: Callable[[str, str], Mapping[str, Any]] = file_metadata,
    downloader: Callable[..., Mapping[str, Any]] = download_file,
    max_ai_done_bytes: int = MAX_AI_DONE_BYTES,
) -> dict[str, Any]:
    if type(max_ai_done_bytes) is not int or not 2 <= max_ai_done_bytes <= MAX_AI_DONE_BYTES:
        raise TerminalEvidenceV2Error('UV_AI_DONE_SIZE_INVALID')
    validate_terminal_output(claim, output)
    ai_id = str(output.get("ai_done_drive_id") or "")
    try:
        ai_raw = dict(metadata_reader(ai_id, token))
        _reject_trashed(ai_raw)
        with tempfile.TemporaryDirectory(prefix="uv-terminal-ai-v2-") as td:
            path = Path(td) / "AI_DONE.json"
            downloader(ai_id, path, token, max_bytes=max_ai_done_bytes, metadata=ai_raw)
            from .diana20_recognizer import _json
            done = _json(path.read_bytes().removeprefix(b"\xef\xbb\xbf"))
    except TerminalEvidenceV2Error:
        raise
    except Exception as exc:
        raise TerminalEvidenceV2Error("UV_AI_DONE_REVERIFY_FAILED") from exc
    if not isinstance(done, dict):
        raise TerminalEvidenceV2Error("UV_AI_DONE_REVERIFY_INVALID")
    rebuilt = _build(
        claim,
        done,
        str(output.get("master_pdf_drive_id") or ""),
        ai_id,
        token,
        metadata_reader=metadata_reader,
        downloader=downloader,
        max_ai_done_bytes=max_ai_done_bytes,
    )
    for key in (
        "master_pdf_drive_id", "master_pdf_sha256", "ai_done_drive_id", "ai_done_sha256",
        "artifact_locators", "artifact_manifest", "artifact_manifest_sha256",
        "terminal_receipt", "terminal_evidence_sha256",
        "saved_frame_observations",
        "completed_report_baseline",
    ):
        if output.get(key) != rebuilt.get(key):
            raise TerminalEvidenceV2Error("UV_TERMINAL_LIVE_EVIDENCE_MISMATCH")
    return rebuilt


def verify_sealed_terminal_output(claim, output, sealed, *, token=None,
                                  metadata_reader=file_metadata, downloader=download_file):
    """The route's three-argument verifier, including fresh full-stream readback."""
    expected = read_sealed_observations(claim, sealed)["provenance"]
    validate_terminal_output(claim, output)
    if output.get("saved_frame_observations") != expected:
        raise TerminalEvidenceV2Error("UV_OBSERVATIONS_CONSUMPTION_MISMATCH")
    if token is None:
        from .drive_adapter import access_token
        token = access_token()
    rebuilt = reverify_terminal_output_live(claim, output, token,
        metadata_reader=metadata_reader, downloader=downloader)
    if rebuilt.get("saved_frame_observations") != expected:
        raise TerminalEvidenceV2Error("UV_OBSERVATIONS_CONSUMPTION_MISMATCH")
    if read_sealed_observations(claim, sealed)["provenance"] != expected:
        raise TerminalEvidenceV2Error("UV_OBSERVATIONS_CHANGED")


__all__ = [
    "TerminalEvidenceV2Error",
    "build_terminal_evidence",
    "consume_sealed_report",
    "consume_sealed_report_private",
    "read_sealed_observations",
    "sealed_ai_done_name",
    "reverify_terminal_output_live",
    "source_identity_from_claim",
    "validate_terminal_output",
    "verify_sealed_terminal_output",
]
