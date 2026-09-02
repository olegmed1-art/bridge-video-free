from pathlib import Path
from types import SimpleNamespace

from steward import drive_inventory_runner as runner


def test_runner_uses_public_access_token_and_writes_complete_output(monkeypatch, tmp_path: Path):
    observed = {}

    def fake_client(**kwargs):
        observed["token_provider"] = kwargs["token_provider"]
        return object()

    monkeypatch.setattr(runner, "access_token", lambda: "token")
    monkeypatch.setattr(runner, "DriveListClient", fake_client)
    monkeypatch.setattr(
        runner,
        "parse_args",
        lambda argv: SimpleNamespace(
            root_folder_id="folder_abcdefghij",
            root_name="School",
            output_dir=tmp_path,
            timeout_seconds=30.0,
        ),
    )
    monkeypatch.setattr(
        runner,
        "build_drive_inventory",
        lambda client, **kwargs: {"status": "COMPLETE"},
    )
    monkeypatch.setattr(
        runner,
        "write_inventory_outputs",
        lambda manifest, output_dir: observed.update(
            {"manifest": manifest, "output_dir": output_dir}
        ),
    )

    assert runner.main([]) == 0
    assert observed["token_provider"] is runner.access_token
    assert observed["manifest"] == {"status": "COMPLETE"}
    assert observed["output_dir"] == tmp_path
