"""Offline COMPLETE capture -> explicit selection -> existing batch planner.

Pass the original collector receipt separately from its private raw snapshot.
Every captured item must be selected or explicitly excluded. Exclusion reasons
are TRASHED, NON_VIDEO, DERIVED_VIDEO or DEFERRED (an unselected original video).
The selection schema is universal-video-selection-v1 with inventory_sha256,
selected_file_ids and excluded_files (objects containing file_id and reason).

The library returns private selection/plan data plus a sanitized review receipt.
The CLI reads three bounded local JSON files and prints ONLY that receipt:
    python -m universal_video.capture_plan snapshot.json receipt.json selection.json \
        --source-folder-id ID --output-folder-id ID --work-folder-id ID

Digests check consistency against the separately retained COMPLETE receipt;
they are not signatures, remote freshness checks or proof of atomic visibility.
No credentials, capture, queue intake or execution. The current manual workflow
deletes raw capture before exit; connecting this adapter there is separate work.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from . import batch_plan, metadata_inventory as capture


REASONS = ("TRASHED", "NON_VIDEO", "DERIVED_VIDEO", "DEFERRED")


class CapturePlanError(ValueError):
    """Fixed public error code, without metadata or dependency exception text."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CapturePlanError("INVALID_ARGUMENTS")


def _capture_rows(snapshot, receipt, source, destinations):
    if (not isinstance(snapshot, dict)
            or set(snapshot) != {"schema", "folder", "pages", "inventory_sha256", "execution_allowed"}
            or snapshot["schema"] != "universal-video-raw-metadata-v1"
            or snapshot["execution_allowed"] is not False):
        raise CapturePlanError("INVALID_CAPTURE")
    if not isinstance(receipt, dict) or not isinstance(receipt.get("status"), str):
        raise CapturePlanError("INVALID_CAPTURE_RECEIPT")
    try:
        capture._validate_receipt(receipt)  # Pure validation; never invokes a worker.
    except capture.InventoryError as exc:
        raise CapturePlanError("INVALID_CAPTURE_RECEIPT") from exc
    if receipt["status"] != "COMPLETE":
        raise CapturePlanError("CAPTURE_NOT_COMPLETE")
    capture._metadata(snapshot["folder"], source, folder=True)
    pages = snapshot["pages"]
    if not isinstance(pages, list) or not 1 <= len(pages) < capture.MAX_GETS:
        raise CapturePlanError("INVALID_PAGES")
    rows, seen, tokens = [], set(), set()
    for index, page in enumerate(pages):
        if not isinstance(page, dict) or page.get("incompleteSearch") is not False:
            raise CapturePlanError("INCOMPLETE_SEARCH")
        files = page.get("files")
        if not isinstance(files, list) or len(files) > capture.PAGE_SIZE:
            raise CapturePlanError("INVALID_PAGE_FILES")
        token = page.get("nextPageToken")
        if index == len(pages) - 1:
            if token not in (None, ""):
                raise CapturePlanError("UNRESOLVED_PAGINATION")
        elif not isinstance(token, str) or not token.strip() or token in tokens:
            raise CapturePlanError("INVALID_PAGE_CHAIN")
        else:
            tokens.add(token)
        for row in files:
            file_id = capture._metadata(row, source)
            if file_id in seen:
                raise CapturePlanError("DUPLICATE_ID")
            if file_id in destinations:
                raise CapturePlanError("SOURCE_OUTPUT_COLLISION")
            seen.add(file_id)
            rows.append(row)
    if len(rows) > capture.MAX_ITEMS:
        raise CapturePlanError("ITEM_LIMIT")
    counts = receipt["counts"]
    expected = {"get_requests": len(pages) + 1, "pages_received": len(pages),
                "items_received": len(rows),
                "video_items": sum(row["mimeType"].startswith("video/") for row in rows),
                "trashed_items": sum(row["trashed"] for row in rows)}
    if (any(counts[key] != value for key, value in expected.items())
            or not 0 < counts["response_bytes"] < capture.MAX_BYTES):
        raise CapturePlanError("RECEIPT_COUNTS_MISMATCH")
    rows.sort(key=lambda row: row["id"])
    digest = batch_plan._digest({"folder": snapshot["folder"], "files": rows})
    if digest != snapshot["inventory_sha256"] or digest != receipt["inventory_sha256"]:
        raise CapturePlanError("CAPTURE_DIGEST_MISMATCH")
    return rows, digest


def _reason(row):
    if row["trashed"]:
        return "TRASHED"
    if not row["mimeType"].startswith("video/"):
        return "NON_VIDEO"
    if row["name"].startswith("AI_PART_"):
        return "DERIVED_VIDEO"
    return "DEFERRED"


def _selection(selection, rows, digest):
    if (not isinstance(selection, dict)
            or set(selection) != {"schema", "inventory_sha256", "selected_file_ids", "excluded_files"}
            or selection["schema"] != "universal-video-selection-v1"):
        raise CapturePlanError("EXPLICIT_SELECTION_REQUIRED")
    if selection["inventory_sha256"] != digest:
        raise CapturePlanError("SELECTION_DIGEST_MISMATCH")
    selected, excluded = selection["selected_file_ids"], selection["excluded_files"]
    if not isinstance(selected, list) or not selected or not isinstance(excluded, list):
        raise CapturePlanError("INVALID_SELECTION")
    selected_ids, excluded_ids = set(), set()
    for file_id in selected:
        capture._id(file_id)
        if file_id in selected_ids:
            raise CapturePlanError("DUPLICATE_SELECTION")
        selected_ids.add(file_id)
    by_id = {row["id"]: row for row in rows}
    for entry in excluded:
        if not isinstance(entry, dict) or set(entry) != {"file_id", "reason"}:
            raise CapturePlanError("INVALID_EXCLUSION")
        file_id = capture._id(entry["file_id"])
        if file_id in excluded_ids or file_id in selected_ids:
            raise CapturePlanError("DUPLICATE_SELECTION")
        excluded_ids.add(file_id)
        if file_id not in by_id or entry["reason"] != _reason(by_id[file_id]):
            raise CapturePlanError("INVALID_EXCLUSION_REASON")
    if selected_ids | excluded_ids != set(by_id):
        raise CapturePlanError("UNACCOUNTED_SELECTION")
    normalized = {"schema": selection["schema"], "inventory_sha256": digest,
                  "selected_file_ids": sorted(selected_ids),
                  "excluded_files": sorted((dict(entry) for entry in excluded),
                                           key=lambda entry: entry["file_id"])}
    return normalized, [row for row in rows if row["id"] in selected_ids]


def _receipt(code, *, counts=None, digests=None):
    return {"schema": "universal-video-capture-plan-receipt-v1",
            "status": "REVIEW_ONLY" if code == "OK" else "ERROR", "code": code,
            "counts": counts, "digests": digests, "plan_valid": code == "OK",
            "execution_allowed": False, "database_persistence_allowed": False,
            "canonical_promotion_allowed": False}


def build_capture_plan(snapshot, receipt, selection, *, source_folder_id,
                       output_folder_id, work_folder_id,
                       max_jobs_per_batch=batch_plan.MAX_JOBS_PER_BATCH):
    """Validate every captured row, then delegate the exact selected rows once.

    Returned plan/selection contain private names and IDs; only receipt is public.
    Receipt response_bytes is a bounded collector claim, not reconstructed HTTP.
    """
    try:
        # Reject NaN, overflow, invalid Unicode and non-JSON values even if unused.
        for value in (snapshot, receipt, selection):
            batch_plan._digest(value)
        source = capture._id(source_folder_id)
        output, work = capture._id(output_folder_id), capture._id(work_folder_id)
        if source in {output, work}:
            raise CapturePlanError("SOURCE_OUTPUT_COLLISION")
        rows, digest = _capture_rows(snapshot, receipt, source, {output, work})
        normalized, selected = _selection(selection, rows, digest)
        plan = batch_plan.build_batch_plan(
            selected, source_folder_id=source, output_folder_id=output,
            work_folder_id=work, max_jobs_per_batch=max_jobs_per_batch)
        selection_digest = batch_plan._digest(normalized)
    except capture.InventoryError as exc:
        raise CapturePlanError(exc.code) from exc
    except batch_plan.BatchPlanError as exc:
        raise CapturePlanError("INVALID_PLAN_INPUT") from exc
    counts = {"captured_items": len(rows), "selected_items": len(selected),
              "excluded_items": len(normalized["excluded_files"]), "batches": len(plan["batches"]),
              "exclusions_by_reason": {reason: sum(entry["reason"] == reason
                  for entry in normalized["excluded_files"]) for reason in REASONS}}
    return {"schema": "universal-video-capture-plan-v1", "selection": normalized,
            "plan": plan, "execution_allowed": False,
            "receipt": _receipt("OK", counts=counts, digests={"capture_inventory_sha256": digest,
                "selection_sha256": selection_digest, "plan_inventory_sha256": plan["inventory_sha256"],
                "plan_sha256": plan["plan_sha256"]})}


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("selection", type=Path)
    parser.add_argument("--source-folder-id", required=True)
    parser.add_argument("--output-folder-id", required=True)
    parser.add_argument("--work-folder-id", required=True)
    parser.add_argument("--max-jobs-per-batch", type=int, default=batch_plan.MAX_JOBS_PER_BATCH)
    try:
        args = parser.parse_args(argv)
        result = build_capture_plan(*(batch_plan.read_snapshot(path) for path in
            (args.snapshot, args.receipt, args.selection)), source_folder_id=args.source_folder_id,
            output_folder_id=args.output_folder_id, work_folder_id=args.work_folder_id,
            max_jobs_per_batch=args.max_jobs_per_batch)["receipt"]
    except CapturePlanError as exc:
        result = _receipt(str(exc))
    except batch_plan.BatchPlanError:
        result = _receipt("INVALID_LOCAL_JSON")
    print(batch_plan._json(result, ensure_ascii=True))
    return 0 if result["plan_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
