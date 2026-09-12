#!/usr/bin/env python3
"""Run the Diana 23--26 recognition batch entirely on Heavy Oracle."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from universal_video.drive_adapter import access_token, download_file, file_metadata


SOURCE_PARENT_ID = "1Fr-H2NgBKEpp3q_H4FzNmQwCV6bj2x6b"
FORBIDDEN_PARENT_ID = "16TVeL_595YU05H0VaRYzxo0IDJkfPAhg"
GOLD_FILE_ID = "1MGaz14wswn2XOgi4AqqvzT2sNFLdOink"
GOLD_SHA256 = "2e7aeff84cfeb142fd310f4c4350207706d687748ee20d06f10895fd123c75e9"
OLD_REPORT_FILE_ID = "1_0iNx29e1Vbm8AzRehXGTQ2Uc7XMttb2"
POST_GOLD_BOUNDARY = "2026-09-11T21:49:40Z"
VIDEOS = (
    ("23", "1G9wvJAOBbjYqC6bKkMGf1DkX-2SNzeLa", "Диана 23.mp4"),
    ("24", "1EzMCIGz1fRRAKDhz0j5_cwsK9FFHQ-oe", "Диана 24.mp4"),
    ("25", "1_p-r-FOYB9hZooScHrzGV-0gQeggLdAz", "Диана 25. 13.09.21.mp4"),
    ("26", "1XqTfXNJ2eNdxnmFQtwkpvvt4XjMzvhFv", "Диана 26.mp4"),
)


def write_status(root: Path, stage: str, **extra: Any) -> None:
    payload = {
        "stage": stage,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "host": os.uname().nodename,
        **extra,
    }
    temporary = root / "status.json.tmp"
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(root / "status.json")
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def checked_metadata(file_id: str, expected_name: str | None, token: str) -> dict[str, Any]:
    item = file_metadata(file_id, token)
    if item.get("id") != file_id:
        raise RuntimeError("Drive identity mismatch")
    if expected_name is not None and item.get("name") != expected_name:
        raise RuntimeError(f"Drive name mismatch: {item.get('name')!r} != {expected_name!r}")
    parents = set(item.get("parents") or [])
    if SOURCE_PARENT_ID not in parents or FORBIDDEN_PARENT_ID in parents:
        raise RuntimeError("source is outside the explicitly allowed Drive folder")
    return item


def upload(path: Path, token: str) -> dict[str, Any]:
    mime = "application/pdf" if path.suffix.lower() == ".pdf" else "application/zip"
    metadata = {"name": path.name, "parents": [SOURCE_PARENT_ID]}
    with path.open("rb") as handle:
        response = requests.post(
            "https://www.googleapis.com/upload/drive/v3/files",
            headers={"Authorization": f"Bearer {token}"},
            params={"uploadType": "multipart", "fields": "id,name,parents,size,createdTime"},
            files={
                "metadata": ("metadata", json.dumps(metadata, ensure_ascii=False), "application/json; charset=UTF-8"),
                "file": (path.name, handle, mime),
            },
            timeout=1800,
        )
    response.raise_for_status()
    item = response.json()
    parents = set(item.get("parents") or [])
    if SOURCE_PARENT_ID not in parents or FORBIDDEN_PARENT_ID in parents:
        raise RuntimeError("uploaded artifact is outside the allowed Drive folder")
    return item


def extract_history(root: Path, token: str) -> int:
    response = requests.get(
        "https://www.googleapis.com/drive/v3/files",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "q": (
                f"'{SOURCE_PARENT_ID}' in parents and createdTime > '{POST_GOLD_BOUNDARY}' "
                "and mimeType = 'application/pdf' and trashed = false"
            ),
            "fields": "files(id,name,size,createdTime,parents,trashed,mimeType)",
            "pageSize": 1000,
        },
        timeout=120,
    )
    response.raise_for_status()
    count = 0
    for item in response.json().get("files", []):
        if "server" not in str(item.get("name", "")).lower():
            continue
        pdf = root / f"{item['id']}.pdf"
        try:
            meta = file_metadata(item["id"], token)
            parents = set(meta.get("parents") or [])
            if SOURCE_PARENT_ID not in parents or FORBIDDEN_PARENT_ID in parents:
                continue
            download_file(item["id"], pdf, token, max_bytes=100_000_000, metadata=meta)
            from pypdf import PdfReader

            reader = PdfReader(str(pdf))
            for name, payloads in reader.attachments.items():
                values = payloads if isinstance(payloads, list) else [payloads]
                for index, payload in enumerate(values):
                    if isinstance(payload, bytes) and name.endswith(".json"):
                        target = root / f"{item['id']}-{index}-{Path(name).name}"
                        target.write_bytes(payload)
                        count += 1
        except Exception as exc:
            print(json.dumps({"history_pdf": item.get("id"), "status": "SKIP", "error": str(exc)}, ensure_ascii=False), flush=True)
        finally:
            pdf.unlink(missing_ok=True)
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-root", required=True, type=Path)
    parser.add_argument("--runtime-commit", required=True)
    args = parser.parse_args()
    root = args.job_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    inputs = root / "inputs"
    new_root = root / "new"
    history = root / "history"
    comparison = root / "comparison"
    for directory in (inputs, new_root, history, comparison):
        directory.mkdir(parents=True, exist_ok=True)
    os.environ["GITHUB_SHA"] = args.runtime_commit
    token = access_token()
    write_status(root, "PREPARING_INPUTS")

    gold_meta = checked_metadata(GOLD_FILE_ID, None, token)
    gold = inputs / "bridgit_gold_v2.zip"
    gold_receipt = download_file(GOLD_FILE_ID, gold, token, max_bytes=5_000_000, metadata=gold_meta)
    if gold_receipt["_download_sha256"] != GOLD_SHA256:
        raise RuntimeError("gold v2 SHA-256 mismatch")
    old_meta = checked_metadata(OLD_REPORT_FILE_ID, "Diana_23-26_recognition_review.pdf", token)
    old_pdf = inputs / "old-review.pdf"
    download_file(OLD_REPORT_FILE_ID, old_pdf, token, max_bytes=100_000_000, metadata=old_meta)
    history_count = extract_history(history, token)

    per_video: list[dict[str, Any]] = []
    for index, (number, file_id, expected_name) in enumerate(VIDEOS, 1):
        write_status(root, "DOWNLOADING_VIDEO", video=number, completed=index - 1, total=len(VIDEOS))
        meta = checked_metadata(file_id, expected_name, token)
        video = inputs / expected_name
        receipt = download_file(file_id, video, token, max_bytes=900_000_000, metadata=meta)
        output = new_root / f"diana-{number}"
        output.mkdir(parents=True, exist_ok=True)
        write_status(root, "PROCESSING_VIDEO", video=number, completed=index - 1, total=len(VIDEOS))
        command = [
            sys.executable,
            "tools/diana167_server_report.py",
            "--video", str(video),
            "--gold-zip", str(gold),
            "--output-dir", str(output),
            "--source-file-id", file_id,
            "--source-parent-id", SOURCE_PARENT_ID,
            "--report-name", f"Диана {number}",
            "--event-probe-ms", "500",
            "--max-deals", "500",
        ]
        subprocess.run(command, check=True)
        pdf = next(output.glob("*server v6.pdf"))
        uploaded = upload(pdf, token)
        per_video.append({
            "video": number,
            "source_id": file_id,
            "source_sha256": receipt["_download_sha256"],
            "pdf": uploaded,
        })
        video.unlink(missing_ok=True)
        shutil.rmtree(output / "work", ignore_errors=True)
        write_status(root, "VIDEO_COMPLETE", video=number, completed=index, total=len(VIDEOS), pdf_id=uploaded["id"])

    write_status(root, "BUILDING_COMPARISON", completed=4, total=4, history_json_files=history_count)
    subprocess.run([
        sys.executable,
        "tools/diana_recognition_compare.py",
        "--new-root", str(new_root),
        "--old-pdf", str(old_pdf),
        "--history-root", str(history),
        "--output-dir", str(comparison),
    ], check=True)
    combined = comparison / "Диана 23–26 — новый прогон и сравнение — server v6.pdf"
    archive = comparison / "Диана 23–26 — данные нового прогона — server v6.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(comparison / "recognition_comparison.json", "recognition_comparison.json")
        bundle.write(root / "status.json", "status-at-packaging.json")
        for path in sorted(new_root.rglob("*")):
            if not path.is_file() or "screenshots" in path.parts or path.suffix.lower() == ".pdf":
                continue
            bundle.write(path, path.relative_to(root))
    combined_item = upload(combined, token)
    archive_item = upload(archive, token)
    result = {
        "status": "PASS",
        "host": os.uname().nodename,
        "runtime_commit": args.runtime_commit,
        "source_parent_id": SOURCE_PARENT_ID,
        "forbidden_parent_used": False,
        "per_video": per_video,
        "combined_pdf": combined_item,
        "data_zip": archive_item,
    }
    (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    write_status(root, "COMPLETE", result=result)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        job_root = Path(next((arg.split("=", 1)[1] for arg in sys.argv if arg.startswith("--job-root=")), "."))
        try:
            write_status(job_root.resolve(), "FAILED", error=f"{type(exc).__name__}: {exc}")
        except Exception:
            pass
        raise
