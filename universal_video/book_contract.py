"""Strict one-atom PDF inbox contract. New review implementation v4.

The inbox cannot activate the worker, authorize uploads, select commands,
change school canon or request database/LLM/DDS work.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import re


SCHEMA = "book-single-atom-job-v1"
PROFILE = "book_single_atom"
INPUT_NAMES = {
    "source_unit": "transfer_atom_ch21.json",
    "independent_review": "independent_bridge_claim_review.json",
    "expected_candidate": "single_atom_bundle.json",
    "expected_draft_binding": "single_atom_review_binding_DRAFT.json",
    "producer": "prepare_single_atom.py",
}


class BookJobError(ValueError):
    error_code = "UV_BOOK_JOB_INVALID"


def require(condition, reason):
    if not condition:
        raise BookJobError(reason)


def exact_fields(value, names):
    require(type(value) is dict and set(value) == set(names), "unexpected fields")


def exact_int(value, minimum, maximum):
    require(type(value) is int and minimum <= value <= maximum, "integer out of bounds")


def digest_field(value, length=64):
    require(type(value) is str and re.fullmatch("[0-9a-f]{%d}" % length, value),
            "invalid digest")


def drive_id(value):
    require(type(value) is str and re.fullmatch("[A-Za-z0-9_-]{10,200}", value),
            "invalid Drive ID")


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value):
        raise BookJobError("non-finite JSON")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=reject_constant)


@dataclass(frozen=True)
class BookJob:
    payload: dict
    job_id: str
    job_hash: str
    profile: str = PROFILE


def validate_book_job(value):
    exact_fields(value, {
        "schema", "job_id", "enabled", "kind", "mode", "code_commit",
        "source", "scope", "inputs", "limits", "permissions",
    })
    require(value["schema"] == SCHEMA and value["kind"] == PROFILE
            and value["mode"] == "reproduce_and_verify"
            and value["enabled"] is False, "unsupported book mode")
    identifier = value["job_id"]
    require(type(identifier) is str
            and re.fullmatch("[A-Za-z0-9][A-Za-z0-9_-]{0,99}", identifier),
            "unsafe job ID")
    digest_field(value["code_commit"], 40)
    source = value["source"]
    exact_fields(source, {"drive_file_id", "mime_type", "bytes", "pages", "sha256"})
    drive_id(source["drive_file_id"])
    require(source["mime_type"] == "application/pdf", "PDF required")
    exact_int(source["bytes"], 1, 20 * 1024 * 1024)
    exact_int(source["pages"], 3, 1000)
    digest_field(source["sha256"])
    scope = value["scope"]
    exact_fields(scope, {
        "pdf_page_indexes", "max_atoms", "object_id", "preserve_legacy_id",
        "contour", "domain", "publication",
    })
    exact_int(scope["max_atoms"], 1, 1)
    require(scope["preserve_legacy_id"] is True
            and scope["contour"] == "WORLD_EXTERNAL"
            and scope["domain"] == "CARD_PLAY"
            and scope["publication"] == "NOT_PUBLISHED", "scope widened")
    require(type(scope["object_id"]) is str
            and re.fullmatch("[A-Za-z0-9._:-]{1,160}", scope["object_id"]),
            "invalid object ID")
    pages = scope["pdf_page_indexes"]
    require(type(pages) is list and len(pages) == 3, "three pages required")
    for page in pages:
        exact_int(page, 0, source["pages"] - 1)
    require(pages == list(range(pages[0], pages[0] + 3)), "page order or duplicates")
    inputs = value["inputs"]
    require(type(inputs) is list and len(inputs) == len(INPUT_NAMES),
            "five inputs required")
    seen_roles, seen_ids = set(), {source["drive_file_id"]}
    for item in inputs:
        exact_fields(item, {"role", "drive_file_id", "name", "bytes", "sha256"})
        role = item["role"]
        require(type(role) is str and role in INPUT_NAMES and role not in seen_roles,
                "unknown or duplicate input role")
        require(item["name"] == INPUT_NAMES[role], "unsafe input name")
        drive_id(item["drive_file_id"])
        require(item["drive_file_id"] not in seen_ids, "aliased input ID")
        exact_int(item["bytes"], 1, 128 * 1024)
        digest_field(item["sha256"])
        seen_roles.add(role)
        seen_ids.add(item["drive_file_id"])
    caps = {
        "concurrency": 1, "cpu": 1, "memory_mib": 2048, "workspace_mib": 512,
        "wall_seconds": 900, "max_output_files": 32,
        "max_output_file_bytes": 5 * 1024 * 1024,
        "max_output_total_bytes": 16 * 1024 * 1024,
    }
    limits = value["limits"]
    exact_fields(limits, set(caps) | {"llm_calls", "dds_calls"})
    for name, maximum in caps.items():
        exact_int(limits[name], 1, maximum)
    for name in ("llm_calls", "dds_calls"):
        exact_int(limits[name], 0, 0)
    require(limits["max_output_files"] >= 8, "eight result artifacts required")
    outputs = [item for item in inputs if item["role"] in {"expected_candidate", "expected_draft_binding"}]
    require(max(item["bytes"] for item in outputs) <= limits["max_output_file_bytes"],
            "candidate exceeds output cap")
    require(limits["max_output_file_bytes"] <= limits["max_output_total_bytes"],
            "inconsistent output cap")
    footprint = (source["bytes"] + sum(item["bytes"] for item in inputs)
                 + limits["max_output_total_bytes"]
                 + 5 * limits["max_output_file_bytes"] + 1024 * 1024)
    require(footprint <= limits["workspace_mib"] * 1024 * 1024,
            "insufficient workspace budget")
    permissions = value["permissions"]
    exact_fields(permissions, {
        "database_writes", "canon_changes", "public_uploads",
        "original_changes", "install_dependencies",
    })
    require(all(flag is False for flag in permissions.values()),
            "mutation permission forbidden")
    copied = deepcopy(value)
    raw = json.dumps(copied, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return BookJob(copied, identifier, hashlib.sha256(raw).hexdigest())
