"""Bounded, evidence-driven PAUSED recovery; no GitHub writes or TSV parsing."""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

REPOSITORY = "olegmed1-art/bridge-video-free"
MUTATIONS = frozenset({"REMEDIATE", "CLOSE_SUPERSEDED", "OWNER_HOLD"})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError("PAUSED_GITHUB_REDIRECT_FORBIDDEN")


def github(path):
    url = f"https://api.github.com/repos/{REPOSITORY}/{path}"
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "bridge-autopilot-paused-reconcile-v2",
    })
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=30) as response:
            if response.status != 200 or response.geturl() != url:
                raise RuntimeError("PAUSED_GITHUB_READ_FAILED")
            raw = response.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ValueError("PAUSED_GITHUB_RESPONSE_TOO_LARGE")
        return json.loads(raw)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("PAUSED_GITHUB_READ_FAILED") from exc


def timestamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("PAUSED_TIMESTAMP_INVALID")
    return value


def validate_candidate(row):
    UUID(str(row["work_item_id"]))
    if type(row["target_pr"]) is not int or not 1 <= row["target_pr"] <= 1000000:
        raise ValueError("PAUSED_TARGET_INVALID")
    if type(row["owner_gated"]) is not bool:
        raise ValueError("PAUSED_OWNER_GATE_INVALID")
    for key in ("result_code", "blocker_action"):
        if key == "result_code" and row[key] is None:
            continue
        if not isinstance(row[key], str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", row[key]):
            raise ValueError("PAUSED_CODE_INVALID")
    for key, pattern in (("last_observed_head_sha", r"[0-9a-f]{40}"),
                         ("progress_token", r"[0-9a-f]{64}")):
        if row[key] is not None and not re.fullmatch(pattern, row[key]):
            raise ValueError("PAUSED_BINDING_INVALID")
    if row["provider_state"] not in (None, "OPEN", "HALF_OPEN", "CLOSED"):
        raise ValueError("PAUSED_PROVIDER_INVALID")
    timestamp(row["updated_at"])
    if row["provider_last_success_at"] is not None:
        timestamp(row["provider_last_success_at"])


def collect_evidence(row, main_sha, api=github, now=None):
    validate_candidate(row)
    now = now or datetime.now(timezone.utc)
    observed = timestamp(row["updated_at"])
    if observed > now:
        raise ValueError("PAUSED_FUTURE_EVIDENCE")
    pr = api(f"pulls/{row['target_pr']}")
    head = pr["head"]["sha"]
    if (pr["base"]["repo"]["full_name"] != REPOSITORY
            or pr["number"] != row["target_pr"]
            or pr["state"] not in ("open", "closed")
            or not re.fullmatch(r"[0-9a-f]{40}", head)
            or not re.fullmatch(r"[0-9a-f]{40}", main_sha)):
        raise ValueError("PAUSED_GITHUB_BINDING_INVALID")
    # All check pages: an older first page must not conceal recent CI evidence.
    completed = []
    for page in range(1, 11):
        checks = api(f"commits/{head}/check-runs?per_page=100&page={page}")["check_runs"]
        completed.extend(timestamp(c["completed_at"]) for c in checks if c["completed_at"])
        if len(checks) < 100:
            break
    else:
        raise ValueError("PAUSED_CHECK_PAGINATION_LIMIT")
    latest = max(completed, default=None)
    if latest and latest > now:
        raise ValueError("PAUSED_FUTURE_EVIDENCE")
    disposition = None
    if pr.get("merged_at"):
        if timestamp(pr["merged_at"]) > now or pr["state"] != "closed":
            raise ValueError("PAUSED_MERGE_INVALID")
        disposition = "MERGED"
    elif pr["state"] == "closed" or row["blocker_action"] == "RECONCILE_TARGET":
        if api(f"compare/{head}...{main_sha}")["status"] in ("ahead", "identical"):
            disposition = "SUBSUMED_BY_MAIN"
    success = row["provider_last_success_at"]
    success = timestamp(success) if success else None
    if success and success > now:
        raise ValueError("PAUSED_FUTURE_EVIDENCE")
    owner_hold = row["owner_gated"] or row["blocker_action"] == "OWNER_HOLD"
    # Comment/label updates alone are not evidence that a failed task can work.
    fresh = (disposition is not None or head != row["last_observed_head_sha"]
             or (latest is not None and latest > observed)
             or (row["blocker_action"] == "PROVIDER_HOLD"
                 and row["provider_state"] == "CLOSED" and success is not None and success > observed))
    if owner_hold:
        fresh = row["progress_token"] is None
    if not fresh:
        return None
    evidence = {"schema": "paused-reconcile-v2", "work_item_id": str(row["work_item_id"]),
                "target_pr": row["target_pr"], "head": head, "main": main_sha,
                "state": pr["state"], "checks_completed": latest.isoformat() if latest else None,
                "result_code": row["result_code"], "owner_gated": row["owner_gated"],
                "provider_success": success.isoformat() if success else None,
                "disposition": disposition}
    token = hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return token, disposition, f"Fresh repository or CI evidence for PR #{row['target_pr']} at {head[:12]}."


def reconcile_batch(rows, main_sha, apply, api=github, limit=3):
    if type(limit) is not int or not 1 <= limit <= 5:
        raise ValueError("PAUSED_MUTATION_LIMIT_INVALID")
    changed = 0
    errors = 0
    for row in rows:
        try:
            evidence = collect_evidence(row, main_sha, api)
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            # Fail closed for this candidate, without starving unrelated work.
            # No unvalidated identifiers, API bodies or exception text in logs.
            errors += 1
            print(f"PAUSED_CANDIDATE_SKIPPED error_type={type(exc).__name__}")
            continue
        if evidence is None:
            continue
        action = apply(row, *evidence)
        if action not in MUTATIONS | {"NO_CHANGE"}:
            raise ValueError("PAUSED_ACTION_INVALID")
        print(f"work_item={row['work_item_id']} target_pr={row['target_pr']} result={action}")
        changed += action in MUTATIONS
        if changed >= limit:
            break
    if errors:
        raise RuntimeError("PAUSED_RECONCILE_PARTIAL_FAILURE")
    return changed


def main():
    if os.environ.get("REPOSITORY") != REPOSITORY:
        raise ValueError("PAUSED_REPOSITORY_INVALID")
    main_sha = github("git/ref/heads/main")["object"]["sha"]
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                         row_factory=dict_row, connect_timeout=10,
                         options="-c statement_timeout=10000 -c lock_timeout=3000") as conn:
        rows = conn.execute("SELECT * FROM autopilot.paused_reconcile_candidates(50)").fetchall()

        def apply(row, token, disposition, summary):
            return conn.execute(
                "SELECT autopilot.reconcile_paused_project_work_cas(%s,%s,%s,%s,%s) AS action",
                (row["work_item_id"], row["updated_at"], token, disposition, summary),
            ).fetchone()["action"]

        print(f"paused_reconcile_changed={reconcile_batch(rows, main_sha, apply)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Deliberately omit exception strings and tracebacks (DSN / API body).
        print(f"PAUSED_RECONCILE_FAILED error_type={type(exc).__name__}")
        raise SystemExit(1) from None
