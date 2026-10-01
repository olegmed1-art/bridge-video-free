#!/usr/bin/env python3
"""One bounded storage inventory; no VPC mutations and no account-wide lists.

API 2026-09-22; eu-de; one instance, one boot volume; at most 100 snapshots.
Images are deliberately not enumerated: IBM has no source-volume list filter.
Publication is not authorization to execute this credentialed command.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

API_VERSION = "2026-09-22"
ORIGIN = "https://eu-de.iaas.cloud.ibm.com"
INSTANCE_ID = "02c7_4463831b-c1a7-45a7-84f4-c0388c8e03b2"
INSTANCE_NAME = "bridge-school-compute-ibm"
VOLUME_ID = "r010-430d0d70-3cdf-4f0c-8709-53e0ee0c4d7e"
ORACLE_HOST = "ubuntu@92.5.47.149"
MAX_BYTES = 1024 * 1024
PAGE_SIZE = 50
MAX_PAGES = 2
INSTANCE_PATH = "/v1/instances/" + INSTANCE_ID
VOLUME_PATH = "/v1/volumes/" + VOLUME_ID
SNAPSHOTS_PATH = "/v1/snapshots"
FIXED_QUERY = {"version": API_VERSION, "generation": "2"}


class InventoryError(Exception):
    """Only fixed machine codes may cross the output boundary."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise InventoryError("redirect_refused")


def request_json(request):
    """TLS, no proxies/redirects, bounded body, no provider bodies in errors."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=20) as response:
            if response.status != 200:
                raise InventoryError("unexpected_http_status")
            raw = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise InventoryError("http_" + str(exc.code)) from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise InventoryError("transport_failed") from None
    if len(raw) > MAX_BYTES:
        raise InventoryError("response_too_large")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise InventoryError("invalid_json") from None
    if not isinstance(value, dict):
        raise InventoryError("invalid_schema")
    return value


def get(token, path, *, start=None):
    if path not in (INSTANCE_PATH, VOLUME_PATH, SNAPSHOTS_PATH):
        raise InventoryError("path_not_allowed")
    query = dict(FIXED_QUERY)
    if path == SNAPSHOTS_PATH:
        query.update({"source_volume.id": VOLUME_ID, "limit": str(PAGE_SIZE)})
        if start is not None:
            if not isinstance(start, str) or not re.fullmatch(r"[A-Za-z0-9._~-]{1,512}", start):
                raise InventoryError("invalid_cursor")
            query["start"] = start
    elif start is not None:
        raise InventoryError("cursor_not_allowed")
    return request_json(urllib.request.Request(
        ORIGIN + path + "?" + urllib.parse.urlencode(query), method="GET",
        headers={"Authorization": "Bearer " + token, "Accept": "application/json",
                 "User-Agent": "bridge-video-free/ibm-storage-inventory"}))


def enum(value, allowed):
    if not isinstance(value, str) or value not in allowed:
        raise InventoryError("unknown_enum")
    return value


def boolean(value):
    if type(value) is not bool:
        raise InventoryError("invalid_boolean")
    return value


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise InventoryError("invalid_integer")
    return value


def cursor(page):
    if "next" not in page:
        return None
    nxt = page["next"]
    if not isinstance(nxt, dict) or not isinstance(nxt.get("href"), str):
        raise InventoryError("invalid_next")
    url = urllib.parse.urlsplit(nxt["href"])
    if (url.scheme != "https" or url.netloc != "eu-de.iaas.cloud.ibm.com"
            or url.path != SNAPSHOTS_PATH or url.fragment):
        raise InventoryError("next_scope_mismatch")
    q = urllib.parse.parse_qs(url.query, keep_blank_values=True)
    required = {**FIXED_QUERY, "source_volume.id": VOLUME_ID, "limit": str(PAGE_SIZE)}
    if set(q) != set(required) | {"start"} or any(q.get(k) != [v] for k, v in required.items()):
        raise InventoryError("next_filter_mismatch")
    starts = q.get("start", [])
    if len(starts) != 1 or not re.fullmatch(r"[A-Za-z0-9._~-]{1,512}", starts[0]):
        raise InventoryError("invalid_cursor")
    return starts[0]


def inventory(token):
    instance = get(token, INSTANCE_PATH)
    if (instance.get("id") != INSTANCE_ID or instance.get("name") != INSTANCE_NAME
            or instance.get("zone", {}).get("name") != "eu-de-2"):
        raise InventoryError("instance_mismatch")
    status = enum(instance.get("status"), {"stopped", "running", "starting", "stopping",
                                          "pending", "failed", "restarting"})
    boot = instance.get("boot_volume_attachment", {})
    if boot.get("volume", {}).get("id") != VOLUME_ID:
        raise InventoryError("boot_volume_mismatch")
    volume = get(token, VOLUME_PATH)
    if volume.get("id") != VOLUME_ID or volume.get("zone", {}).get("name") != "eu-de-2":
        raise InventoryError("volume_mismatch")
    attachments = volume.get("volume_attachments")
    if not isinstance(attachments, list) or len(attachments) != 1:
        raise InventoryError("attachment_mismatch")
    attachment = attachments[0]
    if (attachment.get("instance", {}).get("id") != INSTANCE_ID
            or attachment.get("type") != "boot" or not boot.get("id")
            or attachment.get("id") != boot["id"]):
        raise InventoryError("attachment_mismatch")
    profile = enum(volume.get("profile", {}).get("name"), {"general-purpose", "5iops-tier", "10iops-tier", "custom", "sdp"})
    result = {"api_version": API_VERSION, "instance_id": INSTANCE_ID, "volume_id": VOLUME_ID,
              "instance_status": status, "boot_attachment_match": True,
              "volume_status": enum(volume.get("status"), {"available", "pending", "failed", "deleting"}),
              "capacity_gb": integer(volume.get("capacity"), 1, 32000),
              "profile": profile, "storage_generation": integer(volume.get("storage_generation"), 1, 2),
              "encryption": enum(volume.get("encryption"), {"provider_managed", "user_managed"}),
              "encryption_key_present": "encryption_key" in volume,
              "health_state": enum(volume.get("health_state"), {"ok", "degraded", "faulted", "inapplicable"}),
              "busy": boolean(volume.get("busy")),
              "attachment_state": enum(volume.get("attachment_state"), {"attached"}),
              "delete_volume_on_instance_delete": boolean(attachment.get("delete_volume_on_instance_delete")),
              "images": "NOT_CHECKED_NO_BOUNDED_LOOKUP"}
    snapshots = []
    seen_ids = set()
    seen_cursors = set()
    start = None
    for _ in range(MAX_PAGES):
        page = get(token, SNAPSHOTS_PATH, start=start)
        entries = page.get("snapshots")
        if not isinstance(entries, list) or len(entries) > PAGE_SIZE:
            raise InventoryError("invalid_snapshot_page")
        for item in entries:
            if item.get("source_volume", {}).get("id") != VOLUME_ID:
                raise InventoryError("snapshot_source_mismatch")
            sid = item.get("id")
            if not isinstance(sid, str) or not re.fullmatch(r"r010-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", sid) or sid in seen_ids:
                raise InventoryError("invalid_snapshot_id")
            seen_ids.add(sid)
            snapshots.append({"id": sid, "source_volume_match": True,
                              "lifecycle_state": enum(item.get("lifecycle_state"), {"pending", "stable", "failed", "deleting", "deleted", "updating", "waiting", "suspended"}),
                              "bootable": boolean(item.get("bootable"))})
        start = cursor(page)
        if start is None:
            result.update(snapshots=snapshots, snapshot_count=len(snapshots), snapshots_complete=True)
            return result
        if start in seen_cursors:
            raise InventoryError("cursor_cycle")
        seen_cursors.add(start)
    raise InventoryError("snapshot_inventory_incomplete_page_limit")


def safe_inventory(token):
    try:
        return {"result": "PASS", **inventory(token)}
    except InventoryError as exc:
        return {"result": "BLOCKED", "reason": str(exc)}
    except (TypeError, AttributeError, KeyError, ValueError):
        return {"result": "BLOCKED", "reason": "invalid_schema"}


def sanitize_report(report):
    """Revalidate the remote wire format; never echo unrecognized fields."""
    if not isinstance(report, dict):
        raise InventoryError("remote_output_invalid")
    if report.get("result") == "BLOCKED":
        reason = report.get("reason")
        allowed = {"redirect_refused", "unexpected_http_status", "transport_failed", "response_too_large",
                   "invalid_json", "invalid_schema", "path_not_allowed", "invalid_cursor", "cursor_not_allowed",
                   "unknown_enum", "invalid_boolean", "invalid_integer", "invalid_next", "next_scope_mismatch",
                   "next_filter_mismatch", "instance_mismatch", "boot_volume_mismatch", "volume_mismatch",
                   "attachment_mismatch", "invalid_snapshot_page", "snapshot_source_mismatch",
                   "invalid_snapshot_id", "cursor_cycle", "snapshot_inventory_incomplete_page_limit",
                   "invalid_token_payload"}
        if not isinstance(reason, str) or (reason not in allowed and not re.fullmatch(r"http_[1-5][0-9]{2}", reason)):
            raise InventoryError("remote_output_invalid")
        return {"result": "BLOCKED", "reason": reason}
    fixed = {"result": "PASS", "api_version": API_VERSION, "instance_id": INSTANCE_ID,
             "volume_id": VOLUME_ID, "boot_attachment_match": True, "snapshots_complete": True,
             "images": "NOT_CHECKED_NO_BOUNDED_LOOKUP"}
    if any(type(report.get(k)) is not type(v) or report.get(k) != v for k, v in fixed.items()):
        raise InventoryError("remote_output_invalid")
    out = dict(fixed)
    for k, choices in {
        "instance_status": {"stopped", "running", "starting", "stopping", "pending", "failed", "restarting"},
        "volume_status": {"available", "pending", "failed", "deleting"},
        "profile": {"general-purpose", "5iops-tier", "10iops-tier", "custom", "sdp"},
        "encryption": {"provider_managed", "user_managed"},
        "health_state": {"ok", "degraded", "faulted", "inapplicable"}, "attachment_state": {"attached"},
    }.items():
        out[k] = enum(report.get(k), choices)
    for k in ("encryption_key_present", "busy", "delete_volume_on_instance_delete"):
        out[k] = boolean(report.get(k))
    out["capacity_gb"] = integer(report.get("capacity_gb"), 1, 32000)
    out["storage_generation"] = integer(report.get("storage_generation"), 1, 2)
    items = report.get("snapshots")
    if not isinstance(items, list) or len(items) > MAX_PAGES * PAGE_SIZE:
        raise InventoryError("remote_output_invalid")
    out["snapshots"] = []
    seen = set()
    for item in items:
        sid = item.get("id")
        if (not isinstance(sid, str) or not re.fullmatch(r"r010-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", sid)
                or sid in seen or item.get("source_volume_match") is not True):
            raise InventoryError("remote_output_invalid")
        seen.add(sid)
        out["snapshots"].append({"id": sid, "source_volume_match": True,
            "lifecycle_state": enum(item.get("lifecycle_state"), {"pending", "stable", "failed", "deleting", "deleted", "updating", "waiting", "suspended"}),
            "bootable": boolean(item.get("bootable"))})
    out["snapshot_count"] = integer(report.get("snapshot_count"), 0, MAX_PAGES * PAGE_SIZE)
    if out["snapshot_count"] != len(items):
        raise InventoryError("remote_output_invalid")
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote-read", action="store_true")
    parser.add_argument("--ssh-key")
    parser.add_argument("--known-hosts")
    args = parser.parse_args(argv)
    if args.remote_read:
        try:
            payload = json.loads(sys.stdin.read(32769))
            token = payload["token"]
            if not isinstance(token, str) or not 20 <= len(token) <= 16384 or any(c.isspace() for c in token):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            print('{"result":"BLOCKED","reason":"invalid_token_payload"}')
            return 3
        report = safe_inventory(token)
        print(json.dumps(report, sort_keys=True))
        return 0 if report["result"] == "PASS" else 3
    if not args.ssh_key or not args.known_hosts:
        parser.error("--ssh-key and --known-hosts are required")
    # Only the local runner obtains credentials; Oracle receives a token in stdin.
    try:
        try:
            from .ibm_vpc_oracle_probe import verify_identity
        except ImportError:
            from ibm_vpc_oracle_probe import verify_identity
        key = os.environ.get("IBM_CLOUD_API_KEY", "")
        if not key or any(c.isspace() for c in key):
            raise InventoryError("api_key_invalid")
        body = urllib.parse.urlencode({"grant_type": "urn:ibm:params:oauth:grant-type:apikey", "apikey": key}).encode()
        token = request_json(urllib.request.Request("https://iam.cloud.ibm.com/identity/token", data=body,
            method="POST", headers={"Content-Type": "application/x-www-form-urlencoded",
                                    "User-Agent": "bridge-video-free/ibm-storage-inventory"})).get("access_token")
        verify_identity(token)
        command = ["ssh", "-F", "/dev/null", "-i", args.ssh_key, "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                   "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=" + args.known_hosts,
                   "-o", "ConnectTimeout=15", "-o", "ConnectionAttempts=1", ORACLE_HOST,
                   "python3 -c " + shlex.quote(Path(__file__).read_text()) + " --remote-read"]
        proc = subprocess.run(command, input=json.dumps({"token": token}), text=True, capture_output=True, timeout=120)
        # Never relay arbitrary stdout/stderr, including malformed remote responses.
        if len(proc.stdout) > 32768:
            raise InventoryError("remote_output_invalid")
        report = sanitize_report(json.loads(proc.stdout))
        if report["result"] == "BLOCKED":
            if proc.returncode != 3:
                raise InventoryError("remote_exit_mismatch")
        elif proc.returncode != 0:
            raise InventoryError("remote_exit_mismatch")
        print(json.dumps(report, sort_keys=True))
        return 0 if report["result"] == "PASS" else 3
    except InventoryError as exc:
        print(json.dumps({"result": "BLOCKED", "reason": str(exc)}))
    except Exception:
        print('{"result":"BLOCKED","reason":"authentication_or_ssh_failed"}')
    return 3


if __name__ == "__main__":
    sys.exit(main())
