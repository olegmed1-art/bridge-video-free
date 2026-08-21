import json
import os
import stat

import pytest

from bridge_school_api.dds3 import DDS3Config, DDSUnavailable, solve_table


def _exe(tmp_path, body):
    path = tmp_path / "dds"
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def test_returns_dds_result_without_fallback(tmp_path):
    payload = {"hand_order":["N","E","S","W"],"strain_order":["S","H","D","C","NT"],"dd_table":{"S":[7,8,7,8]},"par_score_ns":0,"par_contracts":[]}
    exe = _exe(tmp_path, "echo '" + json.dumps(payload) + "'\n")
    result = solve_table(pbn="N:A... ... ... ...", config=DDS3Config(executable=exe))
    assert result["engine"] == "DDS3"
    assert result["fallback_used"] is False


def test_missing_dds_is_fail_closed():
    with pytest.raises(DDSUnavailable, match="DDS_UNAVAILABLE"):
        solve_table(pbn="N:A... ... ... ...", config=DDS3Config(executable="/definitely/missing/dds"))


def test_failed_dds_is_fail_closed(tmp_path):
    exe = _exe(tmp_path, "exit 2\n")
    with pytest.raises(DDSUnavailable, match="DDS_UNAVAILABLE"):
        solve_table(pbn="N:A... ... ... ...", config=DDS3Config(executable=exe))
