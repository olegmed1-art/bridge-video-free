#!/usr/bin/env python3
"""Output-generation scoped idempotency for Bridge Video runs.

A completed result for the same opaque job/revision in another Drive output
folder must not suppress a deliberately requested fresh verification run.
Retries inside the same output generation remain idempotent.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

_DRIVE_ID = re.compile(r"^[0-9A-Za-z_-]+$")


def _output_folder_id() -> str:
    value = os.getenv("BRIDGE_OUTPUT_FOLDER_ID", "").strip()
    if value and not _DRIVE_ID.fullmatch(value):
        raise RuntimeError("INVALID_OUTPUT_FOLDER_ID")
    return value


def receipt_search_query(name: str, output_folder_id: str | None = None) -> str:
    folder = _output_folder_id() if output_folder_id is None else str(output_folder_id or "").strip()
    if folder and not _DRIVE_ID.fullmatch(folder):
        raise RuntimeError("INVALID_OUTPUT_FOLDER_ID")
    base = f"trashed=false and name='{name}'"
    return f"'{folder}' in parents and {base}" if folder else base


def _json_reader(semantic_module: Any):
    reader = getattr(semantic_module, "_read_json", None)
    if callable(reader):
        return reader

    def read_json(token, item):
        try:
            return json.loads(semantic_module.base._read_text(token, item))
        except Exception:
            return None

    return read_json


def existing_same_revision_done(
    semantic_module: Any,
    token: str,
    job_id: str,
    revision: str,
):
    """Return an existing terminal result only inside the requested generation.

    When BRIDGE_OUTPUT_FOLDER_ID is present, both AI_DONE and methodology receipt
    must be direct children of that folder. With no output folder configured we
    intentionally retain the historical global lookup for legacy callers.
    """
    output_folder_id = _output_folder_id()
    search = semantic_module.base.io.search
    read_json = _json_reader(semantic_module)

    done_name = f"AI_DONE_{job_id}.json"
    done_candidates = search(token, receipt_search_query(done_name, output_folder_id))
    done_candidates.sort(key=lambda item: item.get("modifiedTime") or "", reverse=True)

    for candidate in done_candidates:
        done = read_json(token, candidate)
        if (
            not done
            or done.get("status") != "AI_DONE"
            or done.get("job_id") not in (None, job_id)
            or done.get("algorithmRevision") != revision
        ):
            continue
        pdf_id = (done.get("masterPdf") or {}).get("driveId")
        for prefix, accepted_status in (
            ("METHODOLOGY_READY", "METHODOLOGY_READY"),
            ("METHODOLOGY_PARTIAL", "METHODOLOGY_PARTIAL"),
        ):
            receipt_name = f"{prefix}_{job_id}.json"
            receipts = search(token, receipt_search_query(receipt_name, output_folder_id))
            receipts.sort(key=lambda item: item.get("modifiedTime") or "", reverse=True)
            for receipt_item in receipts:
                receipt = read_json(token, receipt_item)
                if (
                    receipt
                    and receipt.get("status") == accepted_status
                    and receipt.get("job_id") in (None, job_id)
                    and receipt.get("algorithmRevision") == revision
                    and receipt.get("masterPdfDriveId") == pdf_id
                ):
                    return done
    return None


__all__ = ["receipt_search_query", "existing_same_revision_done"]
