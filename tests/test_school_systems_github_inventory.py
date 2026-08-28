from __future__ import annotations

from datetime import datetime, timezone

import pytest

from steward.github_inventory import (
    GitHubInventoryError,
    GitHubReadClient,
    build_github_inventory,
)


class FakeResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)


def test_rest_client_proves_pagination_with_get_only():
    session = FakeSession(
        [
            FakeResponse([{"id": 1}, {"id": 2}]),
            FakeResponse([{"id": 3}]),
        ]
    )
    client = GitHubReadClient("token", session=session)
    rows = client.get_paginated_list("/repos/o/r/branches", page_size=2)
    assert [row["id"] for row in rows] == [1, 2, 3]
    assert len(session.calls) == 2
    assert session.calls[0][1]["params"]["page"] == 1
    assert session.calls[1][1]["params"]["page"] == 2
    assert all(call[0].endswith("/branches") for call in session.calls)


def test_rate_limit_exhaustion_is_bounded_without_response_body():
    session = FakeSession(
        [FakeResponse({}, status_code=403, headers={"X-RateLimit-Remaining": "0"})]
    )
    client = GitHubReadClient("token", session=session)
    with pytest.raises(GitHubInventoryError, match="GITHUB_RATE_LIMIT_EXHAUSTED"):
        client.get_json("/repos/o/r")


class InventoryClient:
    def __init__(self, *, duplicate_branch=False):
        self.commit_dates = {
            "a" * 40: "2025-01-01T00:00:00Z",
            "b" * 40: "2025-02-01T00:00:00Z",
            "c" * 40: "2025-03-01T00:00:00Z",
            "d" * 40: "2025-12-01T00:00:00Z",
            "e" * 40: "2025-09-01T00:00:00Z",
        }
        self.duplicate_branch = duplicate_branch

    def get_json(self, path, **kwargs):
        if path == "/repos/o/r":
            return {
                "default_branch": "main",
                "private": False,
                "archived": False,
                "fork": False,
            }
        prefix = "/repos/o/r/commits/"
        if path.startswith(prefix):
            sha = path[len(prefix):]
            return {"commit": {"committer": {"date": self.commit_dates[sha]}}}
        raise AssertionError(path)

    def get_paginated_list(self, path, **kwargs):
        if path.endswith("/branches"):
            rows = [
                {"name": "main", "commit": {"sha": "a" * 40}, "protected": False},
                {"name": "open-work", "commit": {"sha": "b" * 40}, "protected": False},
                {"name": "protected-old", "commit": {"sha": "c" * 40}, "protected": True},
                {"name": "fresh", "commit": {"sha": "d" * 40}, "protected": False},
                {"name": "stale", "commit": {"sha": "e" * 40}, "protected": False},
            ]
            if self.duplicate_branch:
                rows.append(dict(rows[-1]))
            return rows
        if path.endswith("/pulls"):
            return [
                {
                    "number": 7,
                    "title": "Work",
                    "draft": True,
                    "head": {"ref": "open-work"},
                    "base": {"ref": "main"},
                    "created_at": "2025-01-01T00:00:00Z",
                    "updated_at": "2025-10-01T00:00:00Z",
                }
            ]
        if path.endswith("/issues"):
            return [
                {
                    "number": 8,
                    "title": "Issue",
                    "labels": [{"name": "P1"}],
                    "created_at": "2025-01-01T00:00:00Z",
                    "updated_at": "2025-11-01T00:00:00Z",
                },
                {"number": 7, "pull_request": {}},
            ]
        if path.endswith("/actions/workflows"):
            return [
                {"id": 1, "name": "CI", "path": ".github/workflows/ci.yml", "state": "active"},
                {"id": 2, "name": "Old", "path": ".github/workflows/old.yml", "state": "disabled_manually"},
            ]
        if path.endswith("/actions/artifacts"):
            return [
                {
                    "id": 9,
                    "name": "artifact",
                    "size_in_bytes": 123,
                    "expired": False,
                    "created_at": "2025-12-01T00:00:00Z",
                    "expires_at": "2025-12-15T00:00:00Z",
                }
            ]
        raise AssertionError(path)


def test_inventory_marks_only_unprotected_non_pr_old_branch_as_stale_candidate():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    inventory = build_github_inventory(InventoryClient(), repository="o/r", now=now)
    by_name = {row["name"]: row for row in inventory["branches"]}
    assert inventory["status"] == "COMPLETE"
    assert by_name["main"]["stale_candidate"] is False
    assert by_name["main"]["stale_excluded_reason"] == "DEFAULT_BRANCH"
    assert by_name["open-work"]["stale_excluded_reason"] == "OPEN_PR"
    assert by_name["protected-old"]["stale_excluded_reason"] == "PROTECTED"
    assert by_name["fresh"]["stale_candidate"] is False
    assert by_name["stale"]["stale_candidate"] is True
    assert inventory["counts"]["stale_branch_candidates_90_days"] == 1
    assert inventory["counts"]["open_pull_requests"] == 1
    assert inventory["counts"]["open_issues"] == 1
    assert inventory["counts"]["active_workflows"] == 1
    assert inventory["counts"]["artifact_bytes"] == 123


def test_duplicate_branch_name_fails_closed():
    with pytest.raises(GitHubInventoryError, match="GITHUB_DUPLICATE_BRANCH"):
        build_github_inventory(
            InventoryClient(duplicate_branch=True),
            repository="o/r",
            now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


def test_inventory_checksum_is_deterministic_for_same_observed_time():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    one = build_github_inventory(InventoryClient(), repository="o/r", now=now)
    two = build_github_inventory(InventoryClient(), repository="o/r", now=now)
    assert one["inventory_sha256"] == two["inventory_sha256"]
