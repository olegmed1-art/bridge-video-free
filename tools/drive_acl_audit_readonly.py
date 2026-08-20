from __future__ import annotations

import collections
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from run_drive_3_1_free_oidc import user_oauth_token

FOLDER_MIME = "application/vnd.google-apps.folder"
DRIVE_API = "https://www.googleapis.com/drive/v3"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3/files"
WRITE_ROLES = {"writer", "organizer", "fileOrganizer"}


def _get(session: requests.Session, url: str, **params):
    r = session.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def _list_children(session: requests.Session, folder_id: str):
    token = None
    while True:
        payload = _get(
            session,
            f"{DRIVE_API}/files",
            q=f"'{folder_id}' in parents and trashed = false",
            fields="nextPageToken,files(id,name,mimeType,parents,shared,createdTime,modifiedTime)",
            pageSize=1000,
            pageToken=token,
            spaces="drive",
        )
        for item in payload.get("files") or []:
            yield item
        token = payload.get("nextPageToken")
        if not token:
            break


def _metadata(session: requests.Session, file_id: str):
    return _get(
        session,
        f"{DRIVE_API}/files/{file_id}",
        fields=(
            "id,name,mimeType,parents,shared,createdTime,modifiedTime,"
            "permissions(id,type,role,emailAddress,domain,allowFileDiscovery,"
            "permissionDetails(inherited,inheritedFrom,permissionType,role))"
        ),
        supportsAllDrives="true",
    )


def _risk_permissions(meta: dict, owner_email: str):
    risks = []
    for p in meta.get("permissions") or []:
        ptype = p.get("type")
        role = p.get("role")
        email = (p.get("emailAddress") or "").strip().lower()
        if ptype == "user" and email == owner_email.lower() and role == "owner":
            continue
        if ptype == "anyone":
            category = "ANYONE"
        elif ptype == "domain":
            category = "DOMAIN"
        elif ptype in {"user", "group"}:
            category = "NAMED_EXTERNAL_OR_COLLABORATOR"
        else:
            category = "OTHER"
        details = p.get("permissionDetails") or []
        inherited = any(bool(x.get("inherited")) for x in details if isinstance(x, dict))
        risks.append(
            {
                "category": category,
                "type": ptype,
                "role": role,
                "emailAddress": p.get("emailAddress"),
                "domain": p.get("domain"),
                "allowFileDiscovery": p.get("allowFileDiscovery"),
                "inherited": inherited,
                "inheritedFrom": next(
                    (x.get("inheritedFrom") for x in details if isinstance(x, dict) and x.get("inheritedFrom")),
                    None,
                ),
            }
        )
    return risks


def _upload_private(session: requests.Session, folder_id: str, path: Path, mime: str):
    metadata = {"name": path.name, "parents": [folder_id]}
    files = {
        "metadata": ("metadata", json.dumps(metadata), "application/json; charset=UTF-8"),
        "file": (path.name, path.read_bytes(), mime),
    }
    r = session.post(
        UPLOAD_API,
        params={"uploadType": "multipart", "fields": "id,name,size,webViewLink"},
        files=files,
        timeout=120,
    )
    r.raise_for_status()
    return r.json()


def _count_item_permissions(totals: collections.Counter, risks: list[dict], grantees: set):
    anyone = [r for r in risks if r["category"] == "ANYONE"]
    domain = [r for r in risks if r["category"] == "DOMAIN"]
    named = [r for r in risks if r["category"] == "NAMED_EXTERNAL_OR_COLLABORATOR"]

    if anyone:
        totals["anyone_items"] += 1
        if any(r.get("role") == "reader" for r in anyone):
            totals["anyone_reader_items"] += 1
        if any(r.get("role") in WRITE_ROLES for r in anyone):
            totals["anyone_write_items"] += 1
        if any(not r.get("inherited") for r in anyone):
            totals["anyone_direct_items"] += 1
        if all(r.get("inherited") for r in anyone):
            totals["anyone_inherited_only_items"] += 1

    if domain:
        totals["domain_items"] += 1
        if any(r.get("role") in WRITE_ROLES for r in domain):
            totals["domain_write_items"] += 1

    if named:
        totals["named_collaborator_items"] += 1
        if any(r.get("role") == "reader" for r in named):
            totals["named_reader_items"] += 1
        if any(r.get("role") in WRITE_ROLES for r in named):
            totals["named_writer_items"] += 1
        if any(r.get("role") == "owner" for r in named):
            totals["external_owner_items"] += 1
        for risk in named:
            key = (risk.get("type"), (risk.get("emailAddress") or "").lower(), risk.get("role"))
            grantees.add(key)


def audit_root(session: requests.Session, root_id: str, root_label: str, owner_email: str):
    root_meta = _metadata(session, root_id)
    queue = collections.deque([(root_id, root_label)])
    seen = set()
    detail = []
    totals = collections.Counter()
    grantees = set()

    while queue:
        folder_id, path = queue.popleft()
        if folder_id in seen:
            continue
        seen.add(folder_id)
        totals["folders_scanned"] += 1
        if folder_id == root_id:
            risks = _risk_permissions(root_meta, owner_email)
            if risks:
                _count_item_permissions(totals, risks, grantees)
                detail.append(
                    {
                        "id": folder_id,
                        "path": path,
                        "mimeType": root_meta.get("mimeType"),
                        "shared": root_meta.get("shared"),
                        "permissions": risks,
                    }
                )
        for item in _list_children(session, folder_id):
            totals["objects_scanned"] += 1
            if item.get("mimeType") == FOLDER_MIME:
                queue.append((item["id"], f"{path}/{item.get('name') or item['id']}"))
            else:
                totals["files_scanned"] += 1
            if not item.get("shared"):
                continue
            totals["shared_flag_items"] += 1
            meta = _metadata(session, item["id"])
            risks = _risk_permissions(meta, owner_email)
            if not risks:
                continue
            _count_item_permissions(totals, risks, grantees)
            detail.append(
                {
                    "id": item["id"],
                    "path": f"{path}/{item.get('name') or item['id']}",
                    "mimeType": item.get("mimeType"),
                    "shared": meta.get("shared"),
                    "createdTime": item.get("createdTime"),
                    "modifiedTime": item.get("modifiedTime"),
                    "permissions": risks,
                }
            )

    totals["distinct_named_grants"] = len(grantees)
    return {
        "root_id": root_id,
        "root_label": root_label,
        "root_shared": bool(root_meta.get("shared")),
        "totals": dict(sorted(totals.items())),
        "risk_items": detail,
    }


def safe_root_summary(root: dict):
    t = root["totals"]
    keys = [
        "folders_scanned", "files_scanned", "objects_scanned", "shared_flag_items",
        "anyone_items", "anyone_reader_items", "anyone_write_items",
        "anyone_direct_items", "anyone_inherited_only_items",
        "domain_items", "domain_write_items",
        "named_collaborator_items", "named_reader_items", "named_writer_items",
        "external_owner_items", "distinct_named_grants",
    ]
    return {
        "root_label": root["root_label"],
        "root_shared": root["root_shared"],
        **{key: t.get(key, 0) for key in keys},
    }


def main():
    token = user_oauth_token()
    if not token:
        raise SystemExit("BLOCKED_ACCESS: Google Drive OAuth unavailable")
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})

    owner_email = os.environ.get("DRIVE_AUDIT_OWNER_EMAIL", "olegmed1@gmail.com")
    audit_folder = os.environ["DRIVE_AUDIT_FOLDER_ID"]
    roots = [
        (os.environ["BRIDGE_PROJECT_ROOT_ID"], "bridge_project_root"),
        (os.environ["ZOOM_RECORDINGS_ROOT_ID"], "zoom_recordings_root"),
    ]

    started = datetime.now(timezone.utc)
    results = [audit_root(session, root_id, label, owner_email) for root_id, label in roots]
    finished = datetime.now(timezone.utc)

    detail = {
        "schema": "bridge-drive-acl-audit-v1.1",
        "status": "READ_ONLY_AUDIT_COMPLETE",
        "started_at": started.isoformat().replace("+00:00", "Z"),
        "finished_at": finished.isoformat().replace("+00:00", "Z"),
        "write_operations_to_existing_items": 0,
        "roots": results,
    }
    safe = {
        "schema": "bridge-drive-acl-audit-safe-summary-v1.1",
        "status": "READ_ONLY_AUDIT_COMPLETE",
        "write_operations_to_existing_items": 0,
        "roots": [safe_root_summary(x) for x in results],
    }
    aggregate_keys = [
        "folders_scanned", "files_scanned", "objects_scanned", "shared_flag_items",
        "anyone_items", "anyone_reader_items", "anyone_write_items",
        "anyone_direct_items", "anyone_inherited_only_items",
        "domain_items", "domain_write_items",
        "named_collaborator_items", "named_reader_items", "named_writer_items",
        "external_owner_items",
    ]
    safe["aggregate"] = {
        key: sum(int(root.get(key, 0)) for root in safe["roots"])
        for key in aggregate_keys
    }
    safe["aggregate"]["roots_shared"] = sum(bool(root["root_shared"]) for root in safe["roots"])

    outdir = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "drive-acl-audit"
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    detail_path = outdir / f"DRIVE_ACL_AUDIT_DETAIL_{stamp}.json"
    safe_path = outdir / f"DRIVE_ACL_AUDIT_SAFE_{stamp}.json"
    detail_path.write_text(json.dumps(detail, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    safe_path.write_text(json.dumps(safe, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    detail_upload = _upload_private(session, audit_folder, detail_path, "application/json")
    safe_upload = _upload_private(session, audit_folder, safe_path, "application/json")
    receipt = {
        "status": "READ_ONLY_AUDIT_COMPLETE",
        "safe_summary": safe,
        "private_detail_drive_id": detail_upload.get("id"),
        "private_safe_drive_id": safe_upload.get("id"),
        "detail_sha256": hashlib.sha256(detail_path.read_bytes()).hexdigest(),
        "safe_sha256": hashlib.sha256(safe_path.read_bytes()).hexdigest(),
    }
    receipt_path = outdir / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
