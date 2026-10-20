from __future__ import annotations

"""Bounded GitHub GET-only observation; emits a local receipt and dry-run text.

Historical installation/merge receipts do not establish current runtime health.
The existing governance indexes are read at one pinned current-main revision;
their embedded verification dates, not retrieval time, determine freshness.
"""

import base64
import json
import os
import re
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from autopilot_app.observer import assess_sources, build_notification_preview, evaluate_snapshot


_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _github_get(url: str, token: str) -> Any:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "global-school-observer-read-only-canary",
        },
        method="GET",
    )
    with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed GitHub API origin below
        if response.status != 200:
            raise RuntimeError(f"GITHUB_READ_FAILED:{response.status}")
        return json.load(response)


def build_live_snapshot(
    repo: str, token: str, *, now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not _REPO_RE.fullmatch(repo):
        raise ValueError("GITHUB_REPOSITORY_INVALID")
    now = now or datetime.now(timezone.utc)
    base = f"https://api.github.com/repos/{repo}"
    web = f"https://github.com/{repo}"
    sources: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    def read(path: str) -> dict[str, Any]:
        try:
            payload = _github_get(f"{base}/{path}", token)
            if not isinstance(payload, dict):
                raise ValueError("EXPECTED_OBJECT")
            return payload
        except (OSError, ValueError, RuntimeError, HTTPException) as exc:
            # Never put tokens, response bodies or exception URLs in receipts.
            errors.append({"source": path, "error_type": type(exc).__name__})
            return {}

    def add(subject: str, status: str, verified_at: Any, refs: list[str],
            detail: str, next_action: str, owner: str, updated_at: Any = None) -> None:
        sources.append({
            "subject": subject, "status": status, "verified_at": verified_at,
            "source_updated_at": updated_at, "evidence_refs": refs,
            "detail": detail, "minimal_next_action": next_action, "owner_or_lane": owner,
        })

    main = read("commits/main")
    sha = main.get("sha")
    if not isinstance(sha, str) or not _SHA_RE.fullmatch(sha):
        sha = None
    add("current-main", "CONFIRMED" if sha else "UNKNOWN", now.isoformat(),
        [f"{web}/commit/{sha}"] if sha else [f"{base}/commits/main"],
        "Current main revision only; this is not evidence of deployed service health.",
        "Restore the current-main read before interpreting repository indexes.", "OBSERVER")

    def document(path: str) -> tuple[dict[str, Any], list[str]]:
        fallback = [f"{web}/blob/main/{path}"]
        if sha is None:
            return {}, fallback
        payload = read(f"contents/{path}?ref={sha}")
        try:
            blob_sha = payload["sha"]
            if not isinstance(blob_sha, str) or not _SHA_RE.fullmatch(blob_sha):
                raise ValueError("INVALID_BLOB_SHA")
            if payload.get("encoding") != "base64":
                raise ValueError("INVALID_ENCODING")
            value = json.loads(base64.b64decode(payload["content"]).decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("EXPECTED_OBJECT")
            # Blob identity remains stable when unrelated main commits land.
            return value, [f"{base}/git/blobs/{blob_sha}"]
        except (KeyError, TypeError, ValueError):
            errors.append({"source": path, "error_type": "INVALID_DOCUMENT"})
            return {}, fallback

    portfolio, portfolio_refs = document("ops/governance/portfolio.json")
    add("governance-portfolio", "CONFIRMED", portfolio.get("last_verified_at"), portfolio_refs,
        "Portfolio verification must be current; retrieving an old index does not refresh it.",
        "Reconcile only changed portfolio entries against primary evidence before updating verification.",
        "GOVERNANCE")
    project, project_refs = document("ops/autopilot/project-state.json")
    verification = project.get("verification")
    verified_at = verification.get("verified_at") if isinstance(verification, dict) else None
    add("autopilot-project-state", "CONFIRMED", verified_at, project_refs,
        "This index is not a live Autopilot receipt; stale deployment claims cannot establish readiness.",
        "Have the current Autopilot owner reconcile this index with live receipts; do not change services.",
        "AUTOPILOT_OWNER")

    governance, governance_refs = document("ops/governance/governance-state.json")
    reviews = governance.get("reviews")
    due = reviews.get("first_operational_audit_due") if isinstance(reviews, dict) else None
    audit = read("issues/620")
    audit_state = "UNKNOWN"
    try:
        due_date = datetime.strptime(due, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        due_date = None
    if due_date is not None:
        if audit.get("state") == "closed":
            audit_state = "COMPLETED" if audit.get("state_reason") == "completed" else "CONFIRMED"
        elif audit.get("state") == "open":
            audit_state = "BLOCKED" if now.date() > due_date else "CONFIRMED"
    add("governance-audit-tracker", audit_state, now.isoformat(),
        [f"{web}/issues/620", *governance_refs],
        f"Audit tracker state={audit.get('state', 'unknown')}; due={due}. Closure describes the tracker only, not school readiness.",
        "Complete the overdue operational audit with evidence and an explicit governance outcome.",
        "GOVERNANCE", audit.get("updated_at"))

    observer = read("issues/1157")
    observer_state = {"open": "BLOCKED", "closed": "CONFIRMED"}.get(observer.get("state"), "UNKNOWN")
    if observer.get("state") == "closed" and observer.get("state_reason") == "completed":
        observer_state = "COMPLETED"
    add("observer-acceptance-tracker", observer_state, now.isoformat(), [f"{web}/issues/1157"],
        f"Observer acceptance tracker is {observer.get('state', 'unknown')}; code/test success is not notification-quality acceptance.",
        "Review retained positive findings and duplicate behavior, then record the acceptance decision in this tracker.",
        "OBSERVER", observer.get("updated_at"))

    return {"sources": sources, "tasks": [], "operations": []}, {
        "main_sha": sha, "collection_status": "PARTIAL" if errors or not sha else "COLLECTED",
        "read_errors": errors,
        "freshness_window_seconds": 86400,
        "unobserved_scopes": ["runtime_services", "Neon_queue", "external_notification_delivery"],
    }


def build_receipt(repo: str, token: str, *, now: datetime) -> dict[str, Any]:
    snapshot, source_state = build_live_snapshot(repo, token, now=now)
    findings = evaluate_snapshot(snapshot, now=now)
    assessments = assess_sources(snapshot["sources"], now=now)
    return {
        "schema": "GLOBAL_SCHOOL_OBSERVER_CANARY_V2", "mode": "READ_ONLY_CANARY",
        "external_mutation_allowed": False, "notifications_enabled": False,
        "notification_quality": "NOT_ACCEPTED_BY_THIS_OBSERVATION",
        "repo": repo, "observed_at": now.isoformat(), "source_state": source_state,
        "assessment": "ATTENTION_REQUIRED" if findings else "NO_FINDINGS_IN_OBSERVED_SCOPE",
        "source_assessments": assessments, "finding_count": len(findings), "findings": findings,
        "notification_preview": build_notification_preview(findings),
    }


def main() -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    output_path = Path(os.environ.get("OBSERVER_CANARY_OUTPUT", "observer-canary.json"))
    if not token:
        raise RuntimeError("GITHUB_TOKEN_MISSING")
    receipt = build_receipt(repo, token, now=datetime.now(timezone.utc))
    output_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
