import hashlib
from types import SimpleNamespace

import pytest

import bridge_contracts.video_dds_pinned_executor as pinned


class FakeWorker:
    def __init__(self, config):
        self.config = config
        self.closed = False

    def close(self):
        self.closed = True


def test_executor_hashes_binary_before_isolated_position_run(tmp_path, monkeypatch):
    executable = tmp_path / "dds_position_worker"
    executable.write_bytes(b"reviewed-dds-binary")
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    monkeypatch.setenv("DDS3_POSITION_WORKER_SHA256", digest)
    monkeypatch.setattr(
        pinned, "DDS3PositionConfig", lambda: SimpleNamespace(executable=str(executable))
    )
    monkeypatch.setattr(pinned, "PositionWorker", FakeWorker)
    monkeypatch.setattr(pinned, "solve_position_all_moves", lambda *_args, **_kwargs: {
        "engine": "DDS3", "fallback_used": False, "operation": "position_all_moves",
        "moves": [{"card": "SA", "tricks": 10, "regret": 0, "optimal": True}],
    })
    result = pinned.execute_digest_pinned_dds3({
        "operation": "position_all_moves", "position": {"pbn": "N:- - - -"},
    })
    assert result["binary_sha256"] == digest
    assert result["engine_version"] == pinned.DDS_UPSTREAM


def test_executor_rejects_relative_executable_even_with_matching_digest(tmp_path, monkeypatch):
    executable = tmp_path / "dds_position_worker"
    executable.write_bytes(b"reviewed-dds-binary")
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    monkeypatch.setenv("DDS3_POSITION_WORKER_SHA256", digest)
    monkeypatch.setattr(
        pinned, "DDS3PositionConfig", lambda: SimpleNamespace(executable="dds_position_worker")
    )
    with pytest.raises(pinned.PinnedDDSExecutorError, match="executable is unavailable"):
        pinned.execute_digest_pinned_dds3({
            "operation": "position_all_moves", "position": {"pbn": "N:- - - -"},
        })


def test_executor_rejects_missing_or_changed_binary_digest(tmp_path, monkeypatch):
    executable = tmp_path / "dds_position_worker"
    executable.write_bytes(b"unreviewed")
    monkeypatch.setattr(
        pinned, "DDS3PositionConfig", lambda: SimpleNamespace(executable=str(executable))
    )
    request = {"operation": "position_all_moves", "position": {"pbn": "N:- - - -"}}
    monkeypatch.delenv("DDS3_POSITION_WORKER_SHA256", raising=False)
    with pytest.raises(pinned.PinnedDDSExecutorError, match="SHA256 is required"):
        pinned.execute_digest_pinned_dds3(request)
    monkeypatch.setenv("DDS3_POSITION_WORKER_SHA256", "0" * 64)
    with pytest.raises(pinned.PinnedDDSExecutorError, match="digest mismatch"):
        pinned.execute_digest_pinned_dds3(request)
