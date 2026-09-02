from __future__ import annotations

import urllib.error
from unittest import mock

import pytest

from assistant_lab.contract import LabContractError
from assistant_lab.worker import RetryableLabError, _post_json


URL = "http://127.0.0.1:8080/v1/compute"


def test_connection_refused_is_terminal() -> None:
    exc = urllib.error.URLError(ConnectionRefusedError(111, "connection refused"))
    with mock.patch("urllib.request.urlopen", side_effect=exc):
        with pytest.raises(LabContractError, match="DDS3_LOCAL_RUNTIME_UNAVAILABLE"):
            _post_json(URL, {"operation": "dd_table"}, token="test", timeout=0.1)


def test_timeout_remains_retryable() -> None:
    with mock.patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        with pytest.raises(RetryableLabError, match="DDS3_LOCAL_TRANSPORT_FAILED"):
            _post_json(URL, {"operation": "dd_table"}, token="test", timeout=0.1)
