from __future__ import annotations

import hmac
import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .observer import ObserverConfig, submit_job

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_TOOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class RunRequest(BaseModel):
    tool_id: str = Field(min_length=1, max_length=64)
    source_path: str = Field(min_length=1, max_length=4096)
    source_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    experiment_id: str | None = Field(default=None, max_length=128)
    timeout_seconds: int = Field(default=3600, ge=1, le=86400)
    label: str = Field(default="", max_length=256)


def _state_root() -> Path:
    return Path(os.getenv("ASSISTANT_LAB_OBSERVER_STATE_ROOT", "/opt/bridge-school/assistant-lab-observer"))


def _observer_config() -> ObserverConfig:
    source_root = os.getenv("ASSISTANT_LAB_OBSERVER_SOURCE_ROOT")
    archive_root = os.getenv("ASSISTANT_LAB_OBSERVER_ARCHIVE_ROOT")
    return ObserverConfig(
        _state_root(),
        source_root=Path(source_root) if source_root else None,
        archive_root=Path(archive_root) if archive_root else None,
        require_archive=os.getenv("ASSISTANT_LAB_OBSERVER_REQUIRE_ARCHIVE", "0") == "1",
    )


def _registry_path() -> Path:
    return Path(os.getenv("ASSISTANT_LAB_CONTROL_REGISTRY", str(_state_root() / "tool_registry.json")))


def _token() -> str:
    token = os.getenv("ASSISTANT_LAB_CONTROL_TOKEN", "").strip()
    if len(token) < 32:
        raise RuntimeError("ASSISTANT_LAB_CONTROL_TOKEN is not configured")
    return token


def _authorize(authorization: str | None) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=404, detail="not found")
    supplied = authorization[7:].strip()
    try:
        expected = _token()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="control plane unavailable") from exc
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=404, detail="not found")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail=f"local state unavailable: {path.name}") from exc


def _load_registry() -> dict[str, list[str]]:
    path = _registry_path()
    raw = _read_json(path)
    if not isinstance(raw, dict) or raw.get("schema") != "assistant-lab-control-tools/v0.1":
        raise HTTPException(status_code=503, detail="tool registry invalid")
    tools = raw.get("tools")
    if not isinstance(tools, dict):
        raise HTTPException(status_code=503, detail="tool registry invalid")
    result: dict[str, list[str]] = {}
    for tool_id, spec in tools.items():
        if not isinstance(tool_id, str) or not _TOOL_RE.fullmatch(tool_id):
            raise HTTPException(status_code=503, detail="tool registry invalid")
        if not isinstance(spec, dict):
            raise HTTPException(status_code=503, detail="tool registry invalid")
        argv = spec.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x for x in argv):
            raise HTTPException(status_code=503, detail="tool registry invalid")
        # No request-controlled argv/env are ever appended. The registry is the command boundary.
        result[tool_id] = argv
    return result


def _queue_counts(config: ObserverConfig) -> dict[str, int]:
    config.ensure()
    return {
        "pending": len(list(config.pending.glob("*.json"))),
        "running": len(list(config.running.glob("*.json"))),
        "done": len(list(config.done.glob("*.json"))),
        "failed": len(list(config.failed.glob("*.json"))),
    }


def _experiment_summary(config: ObserverConfig, path: Path) -> dict[str, Any]:
    manifest_path = path / "manifest.json"
    report_path = path / "observer" / "observer_report.json"
    manifest = _read_json(manifest_path) if manifest_path.exists() else None
    report = _read_json(report_path) if report_path.exists() else None

    # The sealed experiment report records PENDING before the durable archive copy.
    # The daemon writes the authoritative final report only after the copy is
    # checksum-verified. Prefer that root-controlled result once it exists.
    result_path = config.done / f"{path.name}.result.json"
    if result_path.exists():
        final_report = _read_json(result_path)
        if not isinstance(report, dict) or not isinstance(final_report, dict):
            raise HTTPException(status_code=503, detail="observer report invalid")
        identity_fields = ("schema", "experiment_id", "tool_id", "source_sha256")
        if any(final_report.get(field) != report.get(field) for field in identity_fields):
            raise HTTPException(status_code=503, detail="observer final report identity mismatch")
        if final_report.get("archive_status") == "COPIED":
            archive_location = final_report.get("archive_location")
            if not isinstance(archive_location, str) or not Path(archive_location).is_dir():
                raise HTTPException(status_code=503, detail="observer durable archive unavailable")
        report = final_report

    return {
        "experiment_id": path.name,
        "manifest": manifest,
        "observer_report": report,
    }


app = FastAPI(
    title="Assistant Lab Control API",
    version="0.1",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/healthz")
def healthz(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _authorize(authorization)
    config = _observer_config()
    registry = _load_registry()
    return {
        "status": "ready",
        "service": "assistant-lab-control",
        "schema": "assistant-lab-control/v0.1",
        "queue": _queue_counts(config),
        "tool_ids": sorted(registry),
        "arbitrary_shell": False,
        "video_analyzer_result_access": False,
        "other_oracle_result_access": False,
    }


@app.get("/v1/status")
def status(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _authorize(authorization)
    config = _observer_config()
    return {
        "schema": "assistant-lab-control-status/v0.1",
        "queue": _queue_counts(config),
        "experiment_count": len([p for p in config.experiments.iterdir() if p.is_dir()]) if config.experiments.exists() else 0,
    }


@app.get("/v1/experiments")
def experiments(authorization: str | None = Header(default=None), limit: int = 20) -> dict[str, Any]:
    _authorize(authorization)
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="limit must be 1..100")
    config = _observer_config()
    config.ensure()
    paths = sorted((p for p in config.experiments.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    return {"schema": "assistant-lab-control-experiments/v0.1", "experiments": [_experiment_summary(config, p) for p in paths]}


@app.get("/v1/experiments/{experiment_id}")
def experiment(experiment_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _authorize(authorization)
    if not _ID_RE.fullmatch(experiment_id):
        raise HTTPException(status_code=404, detail="experiment not found")
    config = _observer_config()
    path = config.experiments / experiment_id
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="experiment not found")
    return _experiment_summary(config, path)


@app.post("/v1/run", status_code=202)
def run(request: RunRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _authorize(authorization)
    if not _TOOL_RE.fullmatch(request.tool_id):
        raise HTTPException(status_code=422, detail="invalid tool_id")
    if request.experiment_id is not None and not _ID_RE.fullmatch(request.experiment_id):
        raise HTTPException(status_code=422, detail="invalid experiment_id")
    registry = _load_registry()
    argv = registry.get(request.tool_id)
    if argv is None:
        raise HTTPException(status_code=404, detail="tool not found")
    config = _observer_config()
    try:
        queued = submit_job(
            config, argv, request.experiment_id, request.timeout_seconds,
            request.label or request.tool_id, request.tool_id,
            request.source_path, request.source_sha256,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "status": "queued",
        "tool_id": request.tool_id,
        "experiment_id": queued.stem,
        "arbitrary_shell": False,
    }


__all__ = ["app"]
