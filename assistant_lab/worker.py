"""Low-latency Oracle worker for the isolated Assistant Lab v1 queue.

The worker is deliberately bounded:
- reads/writes only assistant_lab.job;
- executes only allow-listed v1 job kinds;
- DDS3 calls go only to the already-hot localhost runtime;
- no shell execution, arbitrary code execution, canon writes, or student-profile writes.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .contract import CONTRACT_VERSION, LabContractError, LabJob, validate_job_payload, validate_priority, verify_dds3_result

CHANNEL = "assistant_lab_jobs"
LOCAL_DDS3_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class RetryableLabError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkerConfig:
    dsn: str
    worker_id: str
    dds3_url: str
    dds3_token: str
    dds3_timeout_seconds: float = 25.0
    wake_timeout_seconds: float = 2.0
    stale_after_seconds: int = 900
    heartbeat_interval_seconds: int = 30
    video_eval_url: str | None = None
    video_eval_token: str | None = None
    video_eval_timeout_seconds: float = 1800.0


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def validate_neon_dsn(raw: str) -> str:
    value = raw.strip()
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"postgresql", "postgres"}:
        raise RuntimeError("assistant-lab DSN must be PostgreSQL")
    if not parsed.username or not parsed.password:
        raise RuntimeError("assistant-lab DSN must include a dedicated login and password")
    if not (parsed.hostname or "").lower().endswith(".neon.tech"):
        raise RuntimeError("assistant-lab DSN must target Neon")
    if parsed.path != "/neondb":
        raise RuntimeError("assistant-lab DSN must target neondb")
    if parsed.fragment:
        raise RuntimeError("assistant-lab DSN must not contain a fragment")
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if query.get("sslmode", [""])[0] not in {"require", "verify-full"}:
        raise RuntimeError("assistant-lab DSN must require TLS")
    if query.get("channel_binding") != ["require"]:
        raise RuntimeError("assistant-lab DSN must require channel binding")
    expected_user = os.getenv("ASSISTANT_LAB_EXPECTED_DB_USER", "").strip()
    if expected_user and parsed.username != expected_user:
        raise RuntimeError("assistant-lab DSN uses an unexpected principal")
    return value


def validate_local_dds3_url(raw: str) -> str:
    value = raw.strip()
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "http" or (parsed.hostname or "").lower() not in LOCAL_DDS3_HOSTS:
        raise RuntimeError("assistant-lab DDS3 endpoint must be localhost HTTP")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError("assistant-lab DDS3 endpoint contains forbidden URL components")
    if parsed.port != 8080 or parsed.path != "/v1/compute":
        raise RuntimeError("assistant-lab DDS3 endpoint must be localhost:8080/v1/compute")
    return value


def validate_local_video_eval_url(raw: str) -> str:
    value = raw.strip()
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "http" or (parsed.hostname or "").lower() not in LOCAL_DDS3_HOSTS:
        raise RuntimeError("assistant-lab video evaluator must be localhost HTTP")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError("assistant-lab video evaluator contains forbidden URL components")
    if parsed.port != 8090 or parsed.path != "/v1/video-eval-canary":
        raise RuntimeError("assistant-lab video evaluator must be localhost:8090/v1/video-eval-canary")
    return value


def load_config() -> WorkerConfig:
    worker_id = os.getenv("ASSISTANT_LAB_WORKER_ID", f"{socket.gethostname()}:{os.getpid()}").strip()
    if not worker_id:
        raise RuntimeError("ASSISTANT_LAB_WORKER_ID must not be empty")
    stale_after = int(os.getenv("ASSISTANT_LAB_STALE_AFTER_SECONDS", "900"))
    heartbeat_interval = int(os.getenv("ASSISTANT_LAB_HEARTBEAT_INTERVAL_SECONDS", "30"))
    if stale_after < 120:
        raise RuntimeError("assistant-lab stale timeout is too small")
    if heartbeat_interval < 5 or heartbeat_interval * 3 >= stale_after:
        raise RuntimeError("assistant-lab heartbeat interval is inconsistent with stale timeout")
    video_eval_url_raw = os.getenv("ASSISTANT_LAB_VIDEO_EVAL_URL", "").strip()
    video_eval_token = os.getenv("ASSISTANT_LAB_VIDEO_EVAL_TOKEN", "").strip()
    if bool(video_eval_url_raw) != bool(video_eval_token):
        raise RuntimeError("assistant-lab video evaluator URL and token must be configured together")
    return WorkerConfig(
        dsn=validate_neon_dsn(_require_env("ASSISTANT_LAB_DATABASE_URL")),
        worker_id=worker_id,
        dds3_url=validate_local_dds3_url(
            os.getenv("ASSISTANT_LAB_DDS3_URL", "http://127.0.0.1:8080/v1/compute")
        ),
        dds3_token=_require_env("DDS3_RUNTIME_TOKEN"),
        dds3_timeout_seconds=float(os.getenv("ASSISTANT_LAB_DDS3_TIMEOUT_SECONDS", "25")),
        wake_timeout_seconds=float(os.getenv("ASSISTANT_LAB_WAKE_TIMEOUT_SECONDS", "2")),
        stale_after_seconds=stale_after,
        heartbeat_interval_seconds=heartbeat_interval,
        video_eval_url=validate_local_video_eval_url(video_eval_url_raw) if video_eval_url_raw else None,
        video_eval_token=video_eval_token or None,
        video_eval_timeout_seconds=float(os.getenv("ASSISTANT_LAB_VIDEO_EVAL_TIMEOUT_SECONDS", "1800")),
    )


def verify_video_eval_result(result: Any, *, expected_sha256: str) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise LabContractError("video evaluator result must be an object")
    if result.get("kind") != "VIDEO_EVAL_CANARY" or result.get("status") != "COMPLETED":
        raise LabContractError("video evaluator did not prove completed canary execution")
    source = result.get("source")
    if not isinstance(source, dict) or source.get("sha256") != expected_sha256:
        raise LabContractError("video evaluator source provenance mismatch")
    routes = result.get("routes")
    if not isinstance(routes, dict) or set(routes) != {"local", "oci"}:
        raise LabContractError("video evaluator must report exactly local and oci routes")
    if any(not isinstance(routes[name], dict) or routes[name].get("status") != "COMPLETED" for name in routes):
        raise LabContractError("video evaluator route did not complete")
    comparison = result.get("comparison")
    if not isinstance(comparison, dict) or comparison.get("status") != "COMPLETED":
        raise LabContractError("video evaluator comparison did not complete")
    return result


def _connect(dsn: str, *, autocommit: bool = False):
    return psycopg.connect(
        dsn,
        autocommit=autocommit,
        connect_timeout=10,
        application_name="assistant-lab-worker",
        row_factory=dict_row,
    )


def recover_stale(config: WorkerConfig) -> tuple[int, int]:
    with _connect(config.dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE assistant_lab.job
            SET status='FAILED', error_text='STALE_RETRY_BUDGET_EXHAUSTED', completed_at=now()
            WHERE status='RUNNING'
              AND COALESCE(heartbeat_at, claimed_at, updated_at) < now() - make_interval(secs => %s)
              AND attempts >= max_attempts
            """,
            (config.stale_after_seconds,),
        )
        failed = cur.rowcount
        cur.execute(
            """
            UPDATE assistant_lab.job
            SET status='QUEUED', claimed_by=NULL, claimed_at=NULL, heartbeat_at=NULL,
                error_text='STALE_RECOVERED', not_before=now()
            WHERE status='RUNNING'
              AND COALESCE(heartbeat_at, claimed_at, updated_at) < now() - make_interval(secs => %s)
              AND attempts < max_attempts
            """,
            (config.stale_after_seconds,),
        )
        requeued = cur.rowcount
        conn.commit()
        return requeued, failed


def claim_one(config: WorkerConfig) -> LabJob | None:
    with _connect(config.dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH next_job AS (
                SELECT job_id
                FROM assistant_lab.job
                WHERE status='QUEUED'
                  AND not_before <= now()
                  AND (deadline_at IS NULL OR deadline_at > now())
                ORDER BY priority ASC, deadline_at ASC NULLS LAST, created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE assistant_lab.job j
            SET status='RUNNING', claimed_by=%s, claimed_at=now(), heartbeat_at=now(),
                completed_at=NULL, error_text=NULL, attempts=j.attempts + 1
            FROM next_job
            WHERE j.job_id=next_job.job_id
            RETURNING j.job_id::text AS job_id, j.kind, j.payload_json, j.priority,
                      j.attempts, j.max_attempts
            """,
            (config.worker_id,),
        )
        row = cur.fetchone()
        conn.commit()
    if not row:
        return None
    return LabJob(
        job_id=row["job_id"],
        kind=row["kind"],
        payload=validate_job_payload(row["kind"], row["payload_json"]),
        priority=validate_priority(row["priority"]),
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
    )


def heartbeat_job(job: LabJob, config: WorkerConfig) -> bool:
    with _connect(config.dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE assistant_lab.job
            SET heartbeat_at=now()
            WHERE job_id=%s::uuid AND status='RUNNING' AND claimed_by=%s
            """,
            (job.job_id, config.worker_id),
        )
        owned = cur.rowcount == 1
        conn.commit()
        return owned


@contextmanager
def keep_lease_alive(job: LabJob, config: WorkerConfig) -> Iterator[None]:
    stop = threading.Event()

    def _loop() -> None:
        while not stop.wait(config.heartbeat_interval_seconds):
            try:
                if not heartbeat_job(job, config):
                    return
            except psycopg.Error:
                # Completion remains ownership-checked; transient DB heartbeat failure
                # must not fabricate a successful job or kill a valid DDS3 compute.
                continue

    thread = threading.Thread(target=_loop, name="assistant-lab-heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2)


def _post_json(url: str, payload: dict[str, Any], *, token: str, timeout: float) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise LabContractError("DDS3 response exceeds assistant-lab limit")
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", errors="replace")
        if exc.code >= 500:
            raise RetryableLabError(f"DDS3_HTTP_{exc.code}: {detail}") from exc
        raise LabContractError(f"DDS3_HTTP_{exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RetryableLabError("DDS3_LOCAL_TRANSPORT_FAILED") from exc
    except json.JSONDecodeError as exc:
        raise RetryableLabError("DDS3_LOCAL_INVALID_JSON") from exc


def execute_job(job: LabJob, config: WorkerConfig) -> dict[str, Any]:
    if job.kind == "NOOP":
        return {"ok": True, "kind": "NOOP", "echo": job.payload, "contract": CONTRACT_VERSION}
    if job.kind == "VIDEO_EVAL_CANARY":
        if not config.video_eval_url or not config.video_eval_token:
            raise LabContractError("VIDEO_EVAL_EXECUTOR_NOT_CONFIGURED")
        result = _post_json(
            config.video_eval_url,
            job.payload,
            token=config.video_eval_token,
            timeout=config.video_eval_timeout_seconds,
        )
        return verify_video_eval_result(result, expected_sha256=job.payload["source"]["sha256"])
    if job.kind != "DDS3_COMPUTE":
        raise LabContractError("unsupported assistant-lab job kind")
    operation = str(job.payload.get("operation") or "dd_table")
    result = _post_json(
        config.dds3_url,
        job.payload,
        token=config.dds3_token,
        timeout=config.dds3_timeout_seconds,
    )
    return verify_dds3_result(result, expected_operation=operation)


def mark_completed(job: LabJob, result: dict[str, Any], config: WorkerConfig) -> None:
    provenance = {
        "assistant_lab_contract": CONTRACT_VERSION,
        "worker_id": config.worker_id,
        "execution_path": (
            "oracle_local_dds3"
            if job.kind == "DDS3_COMPUTE"
            else "oracle_local_video_eval_executor"
            if job.kind == "VIDEO_EVAL_CANARY"
            else "oracle_noop"
        ),
    }
    with _connect(config.dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE assistant_lab.job
            SET status='COMPLETED', result_json=%s,
                provenance_json=provenance_json || %s,
                completed_at=now(), heartbeat_at=now()
            WHERE job_id=%s::uuid AND status='RUNNING' AND claimed_by=%s
            """,
            (Jsonb(result), Jsonb(provenance), job.job_id, config.worker_id),
        )
        if cur.rowcount != 1:
            raise RuntimeError("assistant-lab completion ownership mismatch")
        conn.commit()


def mark_failed(job: LabJob, error: Exception, config: WorkerConfig, *, retryable: bool) -> None:
    message = f"{type(error).__name__}: {error}"[:4000]
    can_retry = retryable and job.attempts < job.max_attempts
    with _connect(config.dsn) as conn, conn.cursor() as cur:
        if can_retry:
            cur.execute(
                """
                UPDATE assistant_lab.job
                SET status='QUEUED', claimed_by=NULL, claimed_at=NULL, heartbeat_at=NULL,
                    error_text=%s, not_before=now() + interval '2 seconds'
                WHERE job_id=%s::uuid AND status='RUNNING' AND claimed_by=%s
                """,
                (message, job.job_id, config.worker_id),
            )
        else:
            cur.execute(
                """
                UPDATE assistant_lab.job
                SET status='FAILED', error_text=%s, completed_at=now(), heartbeat_at=now()
                WHERE job_id=%s::uuid AND status='RUNNING' AND claimed_by=%s
                """,
                (message, job.job_id, config.worker_id),
            )
        if cur.rowcount != 1:
            raise RuntimeError("assistant-lab failure ownership mismatch")
        conn.commit()


def process_one(config: WorkerConfig) -> bool:
    recover_stale(config)
    job = claim_one(config)
    if job is None:
        return False
    try:
        with keep_lease_alive(job, config):
            result = execute_job(job, config)
    except RetryableLabError as exc:
        mark_failed(job, exc, config, retryable=True)
    except Exception as exc:
        mark_failed(job, exc, config, retryable=False)
    else:
        mark_completed(job, result, config)
    return True


def wait_for_wakeup(listener, timeout: float) -> None:
    for _notify in listener.notifies(timeout=timeout, stop_after=1):
        return


def run_forever(config: WorkerConfig) -> None:
    while True:
        try:
            with _connect(config.dsn, autocommit=True) as listener:
                listener.execute(f"LISTEN {CHANNEL}")
                while True:
                    if process_one(config):
                        continue
                    wait_for_wakeup(listener, config.wake_timeout_seconds)
        except KeyboardInterrupt:
            return
        except psycopg.Error:
            time.sleep(2)


def main() -> None:
    run_forever(load_config())


if __name__ == "__main__":
    main()
