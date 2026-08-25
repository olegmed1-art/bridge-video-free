#!/usr/bin/env python3
"""Reconcile a small operational-state index from primary GitHub evidence.

The generated state is deliberately an index, never a replacement for the
primary source. Mutating operators should use it only as a freshness guard and
must still perform their own last-second primary-source checks.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

UTC = dt.timezone.utc


def _now() -> dt.datetime:
    return dt.datetime.now(UTC)


def _parse_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _request_json(url: str, token: str | None) -> Any:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "bridge-school-project-state-v1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


def _api(repo: str, suffix: str, token: str | None) -> Any:
    return _request_json(f"https://api.github.com/repos/{repo}/{suffix}", token)


def _latest_path_commit(repo: str, path: str, token: str | None) -> dict[str, Any] | None:
    encoded = urllib.parse.quote(path, safe="/")
    rows = _api(repo, f"commits?sha=main&path={encoded}&per_page=1", token)
    if not rows:
        return None
    row = rows[0]
    stamp = row.get("commit", {}).get("committer", {}).get("date") or row.get("commit", {}).get("author", {}).get("date")
    return {"sha": row["sha"], "timestamp": stamp, "path": path}


def _latest_code(repo: str, paths: list[str], token: str | None) -> dict[str, Any] | None:
    candidates = [c for p in paths if (c := _latest_path_commit(repo, p, token))]
    candidates.sort(key=lambda c: c.get("timestamp") or "", reverse=True)
    return candidates[0] if candidates else None


def _evidence(repo: str, issue_numbers: list[int], markers: dict[str, str], token: str | None) -> dict[str, Any] | None:
    found: list[dict[str, Any]] = []
    for number in issue_numbers:
        issue = _api(repo, f"issues/{number}", token)
        bodies = [(issue.get("body") or "", issue.get("updated_at") or issue.get("created_at"), f"issue:{number}")]
        comments = _api(repo, f"issues/{number}/comments?per_page=100", token)
        bodies.extend((c.get("body") or "", c.get("updated_at") or c.get("created_at"), f"issue:{number}#comment:{c.get('id')}") for c in comments)
        for body, stamp, locator in bodies:
            for marker, status in markers.items():
                if marker in body:
                    found.append({"marker": marker, "status": status, "timestamp": stamp, "locator": locator})
    found.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
    return found[0] if found else None


def reconcile(contract: dict[str, Any], repo: str, token: str | None) -> dict[str, Any]:
    main = _api(repo, "commits/main", token)
    now = _now()
    subsystems: dict[str, Any] = {}
    conflicts: list[str] = []
    stale: list[str] = []

    for spec in contract["subsystems"]:
        sid = spec["id"]
        code = _latest_code(repo, spec.get("paths", []), token)
        evidence = _evidence(repo, spec.get("evidence_issues", []), spec.get("markers", {}), token)
        code_time = _parse_time(code.get("timestamp") if code else None)
        evidence_time = _parse_time(evidence.get("timestamp") if evidence else None)
        state = "CURRENT"
        reasons: list[str] = []
        if spec.get("critical") and not evidence:
            state = "UNKNOWN_PRIMARY_EVIDENCE_REQUIRED"
            reasons.append("no_authoritative_runtime_marker_configured_or_observed")
        if code_time and evidence_time and code_time > evidence_time:
            state = "STALE_CODE_NEWER_THAN_EVIDENCE"
            reasons.append("code_changed_after_latest_runtime_evidence")
            stale.append(sid)
        subsystems[sid] = {
            "critical": bool(spec.get("critical")),
            "reconciliation_state": state,
            "reasons": reasons,
            "latest_code": code,
            "latest_runtime_evidence": evidence,
            "mutating_action_allowed_from_index": state == "CURRENT" and evidence is not None,
        }

    return {
        "schema": "bridge-school-project-state-v1",
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "repository": repo,
        "main_sha": main["sha"],
        "main_timestamp": main.get("commit", {}).get("committer", {}).get("date"),
        "source_of_truth": "PRIMARY_SOURCES; THIS_FILE_IS_ONLY_A_RECONCILED_INDEX",
        "subsystems": subsystems,
        "summary": {
            "conflicts": conflicts,
            "stale_subsystems": stale,
            "mutating_actions_require_last_second_primary_check": True,
        },
    }


def self_test() -> None:
    assert _parse_time("2026-08-25T10:00:00Z") < _parse_time("2026-08-25T11:00:00Z")
    contract = json.loads(Path("ops/project_state_contract.json").read_text(encoding="utf-8"))
    assert contract["source_of_truth"]["chat_history_is_not_operational_state"] is True
    assert contract["freshness"]["critical_action_max_state_age_minutes"] <= 15
    ids = [x["id"] for x in contract["subsystems"]]
    assert len(ids) == len(set(ids))
    assert {"dds3_pilot10k", "dds3_main30k", "ben", "oracle_compute"}.issubset(ids)
    main30k = next(x for x in contract["subsystems"] if x["id"] == "dds3_main30k")
    assert main30k["critical"] is True
    assert main30k["evidence_issues"] == []  # fail closed until a dedicated primary locator is wired
    print("PROJECT_STATE_SELF_TEST_PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", default="ops/project_state_contract.json")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "olegmed1-art/bridge-video-free"))
    parser.add_argument("--out", default="project_state_live.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    contract = json.loads(Path(args.contract).read_text(encoding="utf-8"))
    state = reconcile(contract, args.repo, os.environ.get("GITHUB_TOKEN"))
    rendered = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    Path(args.out).write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
