"""Read-only routed artifact discovery for Issue #881 terminal v2 evidence."""
from __future__ import annotations

from typing import Any, Callable, Mapping

from .drive_adapter import file_metadata, list_folder_files


class RouteReceiptV2Error(RuntimeError):
    error_code = "UV_ROUTE_RECEIPT_V2_FAILED"

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.error_code = code


def _exact_parent(meta: Mapping[str, Any]) -> str:
    parents = [str(value) for value in (meta.get("parents") or []) if value]
    return parents[0] if len(parents) == 1 else ""


def discover_route_receipt(
    claim: Mapping[str, Any],
    done: Mapping[str, Any],
    token: str,
    *,
    folder_lister: Callable[[str, str], list[dict]] = list_folder_files,
    metadata_reader: Callable[[str, str], Mapping[str, Any]] = file_metadata,
) -> dict[str, Any]:
    """Locate exactly the routed master PDF and matching AI_DONE in output folder.

    Discovery is exact and fail-closed: no fuzzy names, recursive scans, or fallback
    parents are accepted.  This function performs Drive reads only.
    """
    output_folder = str(claim.get("output_folder_id") or "")
    source_id = str(claim.get("source_file_id") or "")
    job_key = str(claim.get("stable_job_key") or "")
    revision = str(claim.get("algorithm_revision") or "")
    master = done.get("masterPdf") if isinstance(done.get("masterPdf"), Mapping) else None
    master_id = str((master or {}).get("driveId") or "")
    if (
        not output_folder
        or not source_id
        or not job_key
        or not revision
        or done.get("status") != "AI_DONE"
        or str(done.get("job_id") or "") != job_key
        or str(done.get("algorithmRevision") or "") != revision
        or str((done.get("original") or {}).get("driveId") or "") != source_id
        or not master_id
        or master_id == source_id
    ):
        raise RouteReceiptV2Error("UV_ROUTE_RECEIPT_IDENTITY_INVALID")

    try:
        items = folder_lister(output_folder, token)
    except Exception as exc:
        raise RouteReceiptV2Error("UV_ROUTE_RECEIPT_FOLDER_READ_FAILED") from exc
    if not isinstance(items, list):
        raise RouteReceiptV2Error("UV_ROUTE_RECEIPT_FOLDER_READ_INVALID")

    master_matches = [
        dict(item) for item in items
        if isinstance(item, Mapping) and str(item.get("id") or "") == master_id
    ]
    ai_name = f"AI_DONE_{job_key}.json"
    ai_matches = [
        dict(item) for item in items
        if isinstance(item, Mapping) and str(item.get("name") or "") == ai_name
    ]
    if len(master_matches) != 1 or len(ai_matches) != 1:
        raise RouteReceiptV2Error("UV_ROUTE_RECEIPT_CARDINALITY_INVALID")

    ai_item = ai_matches[0]
    ai_done_id = str(ai_item.get("id") or "")
    if not ai_done_id or ai_done_id in {source_id, master_id}:
        raise RouteReceiptV2Error("UV_ROUTE_RECEIPT_LOCATOR_INVALID")

    # Re-read both exact IDs after the folder listing so the receipt is bound to
    # current object metadata, not only the listing page.
    try:
        master_meta = dict(metadata_reader(master_id, token))
        ai_meta = dict(metadata_reader(ai_done_id, token))
    except Exception as exc:
        raise RouteReceiptV2Error("UV_ROUTE_RECEIPT_METADATA_READ_FAILED") from exc

    if (
        str(master_meta.get("id") or "") != master_id
        or str(master_meta.get("mimeType") or "") != "application/pdf"
        or _exact_parent(master_meta) != output_folder
        or str(ai_meta.get("id") or "") != ai_done_id
        or str(ai_meta.get("name") or "") != ai_name
        or str(ai_meta.get("mimeType") or "") != "application/json"
        or _exact_parent(ai_meta) != output_folder
    ):
        raise RouteReceiptV2Error("UV_ROUTE_RECEIPT_METADATA_MISMATCH")

    return {
        "schema_version": "universal-video-route-receipt/v2",
        "job_id": job_key,
        "source_file_id": source_id,
        "output_folder_id": output_folder,
        "master_pdf_drive_id": master_id,
        "ai_done_drive_id": ai_done_id,
    }


__all__ = ["RouteReceiptV2Error", "discover_route_receipt"]
