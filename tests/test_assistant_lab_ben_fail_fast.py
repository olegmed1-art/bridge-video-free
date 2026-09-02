from __future__ import annotations

import urllib.error
from unittest import mock

import pytest

from assistant_lab.ben_runtime import RetryableBenError, compute_ben_policy
from assistant_lab.contract import LabContractError


PAYLOAD = {
    "hand": "AKQ.JT9.876.5432",
    "seat": "N",
    "dealer": "N",
    "vul": "None",
    "auction": [],
}


def test_connection_refused_is_terminal() -> None:
    exc = urllib.error.URLError(ConnectionRefusedError(111, "connection refused"))
    with mock.patch("urllib.request.urlopen", side_effect=exc):
        with pytest.raises(LabContractError, match="BEN_LOCAL_RUNTIME_UNAVAILABLE"):
            compute_ben_policy("http://127.0.0.1:8085", PAYLOAD, timeout=0.1)


def test_timeout_remains_retryable() -> None:
    with mock.patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        with pytest.raises(RetryableBenError, match="BEN_LOCAL_TRANSPORT_FAILED"):
            compute_ben_policy("http://127.0.0.1:8085", PAYLOAD, timeout=0.1)
