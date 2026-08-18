#!/usr/bin/env python3
"""Fail-closed cleanup for GitHub Actions artifacts under Bridge School lifecycle policy.

The cleaner only deletes GitHub Actions artifacts when a canonical Neon record proves
that an exact, round-trip-verified Google Drive copy exists and the source workflow run
has completed successfully.

No Google Drive credential is needed here: the cleaner consumes fresh append-only
StorageVerification evidence already captured by the storage/offload path. Stale or
incomplete evidence fails closed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Iterable

EXPECTED_PRINCIPAL = "bridge_school_worker_principal"
EXPECTED_SCHOOL = "Школа спортивного бриджа"
DEFAULT_VERIFICATION_MAX_AGE_HOURS = 12
DEFAULT_COMPLETION_GRACE_MINUTES = 30

# Initial allowlist is deliberately narrow. Unknown types fail closed.
ARTIFACT_CLASS_BY_TYPE = {
    "dds_training_state": "P2",
    "dds_training_report": "P3",
}
AUTO_DELETE_CLASSES = {"P2", "P3", "P7"}


class GateError(RuntimeError):
    pass


@dataclass(frozen=True)
class CleanupCandidate:
    github_artifact_id: int
    github_run_id: int
    github_artifact_name: str
    github_digest: str
    github_size: int
    artifact_id: str
    artifact_version_id: str
    asset_id: str
    artifact_type: str
    lifecycle_class: str
    provenance: dict[str, Any]
    storage_checks: tuple[dict[str, Any], ...]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_github_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def classify_artifact(artifact_type: str, provenance: dict[str, Any]) -> str:
    explicit = provenance.get("lifecycle_class")
    if explicit:
        value = str(explicit).upper()
        if value not in {f"P{i}" for i in range(8)}:
            raise GateError(f"invalid lifecycle_class={explicit!r}")
        return value
    try:
        return ARTIFACT_CLASS_BY_TYPE[artifact_type]
    except KeyError as exc:
        raise GateError(f"artifact_type {artifact_type!r} has no approved lifecycle classification") from exc


def _require_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise GateError(f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise GateError(f"{field} must be an integer") from exc
    return result


def static_gate(
    github_artifact: dict[str, Any],
    artifact_record: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    """Validate immutable identity/provenance fields before DB storage checks."""
    provenance = artifact_record["provenance"]
    lifecycle_class = classify_artifact(artifact_record["artifact_type"], provenance)
    if lifecycle_class not in AUTO_DELETE_CLASSES:
        raise GateError(f"lifecycle class {lifecycle_class} is not eligible for automatic deletion")

    if not artifact_record.get("asset_id"):
        raise GateError("artifact_version has no canonical asset_id for audit linkage")

    artifact_id = _require_int(github_artifact.get("id"), "GitHub artifact id")
    if _require_int(provenance.get("github_artifact_id"), "provenance.github_artifact_id") != artifact_id:
        raise GateError("GitHub artifact id does not match canonical provenance")

    run = github_artifact.get("workflow_run") or {}
    run_id = _require_int(run.get("id"), "GitHub workflow_run.id")
    if _require_int(provenance.get("github_run_id"), "provenance.github_run_id") != run_id:
        raise GateError("GitHub run id does not match canonical provenance")

    if str(provenance.get("github_artifact_name")) != str(github_artifact.get("name")):
        raise GateError("GitHub artifact name does not match canonical provenance")

    logical_sha = str(provenance.get("logical_original_sha256", "")).lower()
    if len(logical_sha) != 64 or any(c not in "0123456789abcdef" for c in logical_sha):
        raise GateError("canonical logical_original_sha256 is missing or invalid")
    digest = str(github_artifact.get("digest") or "").lower()
    if digest != f"sha256:{logical_sha}":
        raise GateError("GitHub artifact digest does not match canonical SHA-256")

    logical_size = _require_int(provenance.get("logical_original_size"), "provenance.logical_original_size")
    github_size = _require_int(github_artifact.get("size_in_bytes"), "GitHub size_in_bytes")
    if logical_size != github_size:
        raise GateError("GitHub artifact size does not match canonical logical size")

    if provenance.get("roundtrip_sha256_verified") is not True:
        raise GateError("roundtrip_sha256_verified is not true")

    refs: list[dict[str, Any]] = []
    layout = str(provenance.get("storage_layout", ""))
    if layout.startswith("split-"):
        parts = provenance.get("parts")
        if not isinstance(parts, list) or not parts:
            raise GateError("split storage layout has no parts")
        total = 0
        for index, part in enumerate(parts, start=1):
            if not isinstance(part, dict):
                raise GateError(f"part {index} is not an object")
            size = _require_int(part.get("size"), f"part {index}.size")
            total += size
            refs.append(
                {
                    "kind": "part",
                    "index": index,
                    "drive_file_id": str(part.get("drive_file_id") or ""),
                    "sha256": str(part.get("sha256") or "").lower(),
                    "size": size,
                }
            )
        if total != logical_size:
            raise GateError("split part sizes do not reconstruct the logical artifact size")

        manifest_sha = str(provenance.get("manifest_sha256") or "").lower()
        manifest_drive_id = str(provenance.get("manifest_drive_file_id") or "")
        if not manifest_sha or not manifest_drive_id:
            raise GateError("split layout is missing manifest identity")
        refs.append(
            {
                "kind": "manifest",
                "drive_file_id": manifest_drive_id,
                "sha256": manifest_sha,
                "size": None,
            }
        )
    elif layout == "single-file":
        refs.append(
            {
                "kind": "single-file",
                "drive_file_id": str(provenance.get("drive_file_id") or ""),
                "sha256": str(provenance.get("drive_sha256") or logical_sha).lower(),
                "size": logical_size,
            }
        )
    else:
        raise GateError(f"unsupported storage_layout={layout!r}")

    for ref in refs:
        if not ref["drive_file_id"]:
            raise GateError(f"{ref['kind']} has no Drive file id")
        sha = ref["sha256"]
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise GateError(f"{ref['kind']} has invalid SHA-256")
    return lifecycle_class, refs


class GitHubApi:
    def __init__(self, repository: str, token: str):
        if "/" not in repository:
            raise GateError("GITHUB_REPOSITORY must be owner/name")
        if not token:
            raise GateError("GITHUB_TOKEN is not configured")
        self.repository = repository
        self.token = token

    def _request(self, method: str, path: str) -> tuple[int, Any]:
        url = f"https://api.github.com/repos/{self.repository}{path}"
        req = urllib.request.Request(
            url,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "bridge-school-artifact-cleanup",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                raw = response.read()
                data = json.loads(raw.decode("utf-8")) if raw else None
                return response.status, data
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            if exc.code == 404:
                return 404, None
            safe = raw.decode("utf-8", "replace")[:500]
            raise GateError(f"GitHub API {method} {path} failed: HTTP {exc.code}: {safe}") from exc

    def get_artifact(self, artifact_id: int) -> dict[str, Any] | None:
        status, data = self._request("GET", f"/actions/artifacts/{artifact_id}")
        if status == 404:
            return None
        if not isinstance(data, dict):
            raise GateError("unexpected GitHub artifact response")
        return data

    def get_run(self, run_id: int) -> dict[str, Any]:
        status, data = self._request("GET", f"/actions/runs/{run_id}")
        if status != 200 or not isinstance(data, dict):
            raise GateError(f"unable to read GitHub workflow run {run_id}")
        return data

    def list_artifacts(self, max_artifacts: int) -> Iterable[dict[str, Any]]:
        seen = 0
        page = 1
        while seen < max_artifacts:
            status, data = self._request("GET", f"/actions/artifacts?per_page=100&page={page}")
            if status != 200 or not isinstance(data, dict):
                raise GateError("unexpected GitHub artifact list response")
            artifacts = data.get("artifacts") or []
            if not artifacts:
                break
            for artifact in artifacts:
                if not isinstance(artifact, dict):
                    continue
                yield artifact
                seen += 1
                if seen >= max_artifacts:
                    return
            page += 1

    def delete_artifact(self, artifact_id: int) -> str:
        status, _ = self._request("DELETE", f"/actions/artifacts/{artifact_id}")
        if status in (200, 204):
            return "deleted"
        if status == 404:
            return "already_absent"
        raise GateError(f"unexpected GitHub delete status {status}")


class NeonRegistry:
    def __init__(self, dsn: str, verification_max_age_hours: int):
        if not dsn:
            raise GateError("BRIDGE_WORKER_DATABASE_URL is not configured")
        self.dsn = dsn
        self.max_age = timedelta(hours=verification_max_age_hours)

    def connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise GateError("psycopg is not installed") from exc
        return psycopg.connect(
            self.dsn,
            connect_timeout=10,
            application_name="bridge-school-artifact-cleanup",
            row_factory=dict_row,
        )

    def preflight(self, conn) -> str:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT current_user,
                       has_table_privilege(current_user, 'public.artifact_version', 'SELECT') AS can_read_artifact_version,
                       has_table_privilege(current_user, 'public.storage_verification', 'SELECT') AS can_read_storage_verification,
                       has_table_privilege(current_user, 'public.evidence', 'INSERT') AS can_append_evidence,
                       has_table_privilege(current_user, 'public.evidence', 'DELETE') AS can_delete_evidence
                """
            )
            row = cur.fetchone()
            if row["current_user"] != EXPECTED_PRINCIPAL:
                raise GateError(f"unexpected database principal {row['current_user']!r}")
            if not row["can_read_artifact_version"] or not row["can_read_storage_verification"]:
                raise GateError("worker lacks required read privileges")
            if not row["can_append_evidence"]:
                raise GateError("worker cannot append cleanup audit evidence")
            if row["can_delete_evidence"]:
                raise GateError("worker has forbidden DELETE privilege on evidence")

            cur.execute("SELECT school_id FROM public.school WHERE stable_name=%s", (EXPECTED_SCHOOL,))
            schools = cur.fetchall()
            if len(schools) != 1:
                raise GateError("canonical school row missing or duplicated")
            return str(schools[0]["school_id"])

    def find_artifact_record(self, conn, github_artifact_id: int) -> dict[str, Any] | None:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    a.artifact_id::text AS artifact_id,
                    a.artifact_type,
                    a.title,
                    av.artifact_version_id::text AS artifact_version_id,
                    av.asset_id::text AS asset_id,
                    av.status AS artifact_version_status,
                    av.provenance
                FROM public.artifact_version av
                JOIN public.artifact a ON a.artifact_id = av.artifact_id
                WHERE av.provenance->>'github_artifact_id' = %s
                ORDER BY av.created_at DESC
                """,
                (str(github_artifact_id),),
            )
            rows = cur.fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise GateError(f"multiple canonical artifact versions reference GitHub artifact {github_artifact_id}")
        return rows[0]

    def verify_drive_ref(self, conn, ref: dict[str, Any], checked_at: datetime) -> dict[str, Any]:
        locator = f"gdrive:file:{ref['drive_file_id']}"
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    a.asset_id::text AS asset_id,
                    a.byte_size,
                    a.checksum_algorithm,
                    a.checksum_value,
                    al.asset_location_id::text AS asset_location_id,
                    al.availability_status AS location_availability,
                    al.last_verified_at,
                    sv.verified_at,
                    sv.checksum_algorithm AS observed_algorithm,
                    sv.checksum_observed,
                    sv.availability_status AS verification_availability,
                    sv.integrity_status,
                    sv.method
                FROM public.asset a
                JOIN public.asset_location al ON al.asset_id = a.asset_id
                JOIN LATERAL (
                    SELECT verified_at, checksum_algorithm, checksum_observed,
                           availability_status, integrity_status, method
                    FROM public.storage_verification
                    WHERE asset_location_id = al.asset_location_id
                    ORDER BY verified_at DESC
                    LIMIT 1
                ) sv ON TRUE
                WHERE a.checksum_algorithm = 'sha256'
                  AND a.checksum_value = %s
                  AND al.storage_provider = 'google_drive'
                  AND al.locator = %s
                  AND al.status = 'active'
                """,
                (ref["sha256"], locator),
            )
            rows = cur.fetchall()
        if len(rows) != 1:
            raise GateError(f"{ref['kind']} has no unique canonical verified Drive location")

        row = rows[0]
        if ref["size"] is not None and int(row["byte_size"]) != int(ref["size"]):
            raise GateError(f"{ref['kind']} Drive asset size does not match provenance")
        if row["location_availability"] != "available":
            raise GateError(f"{ref['kind']} Drive location is not available")
        if row["verification_availability"] != "available":
            raise GateError(f"{ref['kind']} latest StorageVerification is not available")
        if row["integrity_status"] != "verified":
            raise GateError(f"{ref['kind']} latest StorageVerification is not integrity=verified")
        if (row["observed_algorithm"] or "").lower() != "sha256":
            raise GateError(f"{ref['kind']} verification algorithm is not sha256")
        if (row["checksum_observed"] or "").lower() != ref["sha256"]:
            raise GateError(f"{ref['kind']} observed SHA-256 does not match provenance")
        if row["method"] != "google_drive_roundtrip_sha256":
            raise GateError(f"{ref['kind']} verification method is not round-trip SHA-256")
        verified_at = row["verified_at"]
        if verified_at is None:
            raise GateError(f"{ref['kind']} has no verification timestamp")
        if verified_at.tzinfo is None:
            verified_at = verified_at.replace(tzinfo=timezone.utc)
        age = checked_at - verified_at.astimezone(timezone.utc)
        if age < timedelta(0) or age > self.max_age:
            raise GateError(f"{ref['kind']} verification is stale ({age})")
        return dict(row)

    def already_deleted(self, conn, github_artifact_id: int) -> bool:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM public.evidence
                WHERE evidence_type = 'artifact_cleanup_deleted'
                  AND locator->>'github_artifact_id' = %s
                LIMIT 1
                """,
                (str(github_artifact_id),),
            )
            return cur.fetchone() is not None

    def append_audit(
        self,
        conn,
        *,
        school_id: str,
        evidence_type: str,
        candidate: CleanupCandidate,
        action_result: str,
        authorization_evidence_id: str | None = None,
    ) -> str:
        locator = {
            "policy": "data_lifecycle_policy_v1_0",
            "lifecycle_class": candidate.lifecycle_class,
            "github_repository": os.environ.get("GITHUB_REPOSITORY"),
            "github_artifact_id": candidate.github_artifact_id,
            "github_run_id": candidate.github_run_id,
            "github_artifact_name": candidate.github_artifact_name,
            "github_digest": candidate.github_digest,
            "github_size": candidate.github_size,
            "artifact_id": candidate.artifact_id,
            "artifact_version_id": candidate.artifact_version_id,
            "permanent_storage_provider": "google_drive",
            "drive_storage": [
                {
                    "kind": check["kind"],
                    "asset_id": check["db"]["asset_id"],
                    "asset_location_id": check["db"]["asset_location_id"],
                    "verified_at": check["db"]["verified_at"].isoformat(),
                    "sha256": check["ref"]["sha256"],
                }
                for check in candidate.storage_checks
            ],
            "action_result": action_result,
            "authorization_evidence_id": authorization_evidence_id,
            "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
            "workflow_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        }
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.evidence
                    (school_id, evidence_type, asset_id, locator, confidence_class, quality_status)
                VALUES
                    (%s, %s, %s::uuid, %s::jsonb, 'HIGH', 'verified')
                RETURNING evidence_id::text
                """,
                (
                    school_id,
                    evidence_type,
                    candidate.asset_id,
                    json.dumps(locator, ensure_ascii=False, sort_keys=True),
                ),
            )
            evidence_id = cur.fetchone()["evidence_id"]
        conn.commit()
        return evidence_id


def build_candidate(
    *,
    registry: NeonRegistry,
    conn,
    github: GitHubApi,
    github_artifact: dict[str, Any],
    checked_at: datetime,
    completion_grace_minutes: int,
) -> CleanupCandidate:
    artifact_id = _require_int(github_artifact.get("id"), "GitHub artifact id")
    record = registry.find_artifact_record(conn, artifact_id)
    if record is None:
        raise GateError("no canonical Neon artifact_version provenance for this GitHub artifact")

    lifecycle_class, refs = static_gate(github_artifact, record)

    run_id = _require_int((github_artifact.get("workflow_run") or {}).get("id"), "GitHub workflow_run.id")
    run = github.get_run(run_id)
    if run.get("status") != "completed":
        raise GateError(f"source workflow run {run_id} is not completed")
    if run.get("conclusion") != "success":
        raise GateError(f"source workflow run {run_id} conclusion is not success")
    completed_at_raw = run.get("updated_at")
    if not completed_at_raw:
        raise GateError("source workflow run has no completion timestamp")
    completed_at = parse_github_time(str(completed_at_raw))
    if checked_at - completed_at < timedelta(minutes=completion_grace_minutes):
        raise GateError("completion grace period has not elapsed")

    checks: list[dict[str, Any]] = []
    for ref in refs:
        db = registry.verify_drive_ref(conn, ref, checked_at)
        checks.append({"kind": ref["kind"], "ref": ref, "db": db})

    return CleanupCandidate(
        github_artifact_id=artifact_id,
        github_run_id=run_id,
        github_artifact_name=str(github_artifact["name"]),
        github_digest=str(github_artifact["digest"]),
        github_size=int(github_artifact["size_in_bytes"]),
        artifact_id=record["artifact_id"],
        artifact_version_id=record["artifact_version_id"],
        asset_id=record["asset_id"],
        artifact_type=record["artifact_type"],
        lifecycle_class=lifecycle_class,
        provenance=record["provenance"],
        storage_checks=tuple(checks),
    )


def process_artifact(
    *,
    registry: NeonRegistry,
    conn,
    github: GitHubApi,
    school_id: str,
    artifact: dict[str, Any],
    execute: bool,
    checked_at: datetime,
    completion_grace_minutes: int,
) -> tuple[str, str]:
    artifact_id = _require_int(artifact.get("id"), "GitHub artifact id")
    name = str(artifact.get("name") or "")
    try:
        candidate = build_candidate(
            registry=registry,
            conn=conn,
            github=github,
            github_artifact=artifact,
            checked_at=checked_at,
            completion_grace_minutes=completion_grace_minutes,
        )
    except GateError as exc:
        return "SKIP", f"id={artifact_id} name={name!r}: {exc}"

    if registry.already_deleted(conn, artifact_id):
        return "SKIP", f"id={artifact_id} name={name!r}: deletion already recorded in Neon"

    if not execute:
        return (
            "ELIGIBLE",
            f"id={artifact_id} name={name!r} class={candidate.lifecycle_class} "
            f"run={candidate.github_run_id} storage_checks={len(candidate.storage_checks)}",
        )

    authorization_id = registry.append_audit(
        conn,
        school_id=school_id,
        evidence_type="artifact_cleanup_authorized",
        candidate=candidate,
        action_result="authorized",
    )
    result = github.delete_artifact(artifact_id)
    registry.append_audit(
        conn,
        school_id=school_id,
        evidence_type="artifact_cleanup_deleted",
        candidate=candidate,
        action_result=result,
        authorization_evidence_id=authorization_id,
    )
    return (
        "DELETED" if result == "deleted" else "ABSENT",
        f"id={artifact_id} name={name!r} class={candidate.lifecycle_class} result={result}",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-id", type=int, default=None)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--max-artifacts", type=int, default=200)
    parser.add_argument(
        "--verification-max-age-hours",
        type=int,
        default=DEFAULT_VERIFICATION_MAX_AGE_HOURS,
    )
    parser.add_argument(
        "--completion-grace-minutes",
        type=int,
        default=DEFAULT_COMPLETION_GRACE_MINUTES,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.max_artifacts <= 0:
        raise GateError("--max-artifacts must be positive")
    if args.verification_max_age_hours <= 0:
        raise GateError("--verification-max-age-hours must be positive")
    if args.completion_grace_minutes < 0:
        raise GateError("--completion-grace-minutes cannot be negative")

    github = GitHubApi(
        repository=os.environ.get("GITHUB_REPOSITORY", ""),
        token=os.environ.get("GITHUB_TOKEN", ""),
    )
    registry = NeonRegistry(
        os.environ.get("BRIDGE_WORKER_DATABASE_URL", ""),
        verification_max_age_hours=args.verification_max_age_hours,
    )
    checked_at = now_utc()

    with registry.connect() as conn:
        school_id = registry.preflight(conn)
        if args.artifact_id is not None:
            artifact = github.get_artifact(args.artifact_id)
            if artifact is None:
                print(f"ARTIFACT_CLEANUP: ABSENT id={args.artifact_id}")
                return 0
            artifacts = [artifact]
        else:
            artifacts = list(github.list_artifacts(args.max_artifacts))

        counts = {"ELIGIBLE": 0, "DELETED": 0, "ABSENT": 0, "SKIP": 0}
        for artifact in artifacts:
            status, message = process_artifact(
                registry=registry,
                conn=conn,
                github=github,
                school_id=school_id,
                artifact=artifact,
                execute=args.execute,
                checked_at=checked_at,
                completion_grace_minutes=args.completion_grace_minutes,
            )
            counts[status] += 1
            print(f"ARTIFACT_CLEANUP: {status}: {message}")

    print(
        "ARTIFACT_CLEANUP: SUMMARY "
        + " ".join(f"{key.lower()}={value}" for key, value in counts.items())
        + f" execute={str(args.execute).lower()}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GateError as exc:
        print(f"ARTIFACT_CLEANUP: FAIL: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except Exception as exc:
        print(
            f"ARTIFACT_CLEANUP: FAIL: unexpected error {exc.__class__.__name__}",
            file=sys.stderr,
        )
        raise SystemExit(3)
