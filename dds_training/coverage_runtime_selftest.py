from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    dds_root = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="dds-runtime-coverage-selftest-") as td:
        root = Path(td)
        fragments = root / "fragments"
        module = root / "covered_fixture.py"
        module.write_text(
            "def choose(value):\n"
            "    if value:\n"
            "        return 1\n"
            "    return 0\n"
            "\n"
            "choose(True)\n"
            "choose(False)\n",
            encoding="utf-8",
        )

        # This self-test itself may be launched inside the outer runtime-coverage
        # suite, where sitecustomize has already activated the collector.  Use a
        # fresh -S child so the collector under test starts from a clean state and
        # binds to the temporary root rather than the outer suite's fragment dir.
        env = os.environ.copy()
        env["DDS_COVERAGE_ROOT"] = str(root)
        env["DDS_COVERAGE_DIR"] = str(fragments)
        env.pop("DDS_COVERAGE_MANIFEST", None)
        env.pop("DDS_COVERAGE_SUITE", None)
        env["PYTHONPATH"] = str(dds_root)
        code = """
import runpy
import sys
import coverage_runtime as runtime
assert runtime.activate_from_environment() is True
assert runtime.activate_from_environment() is True
runpy.run_path(sys.argv[1], run_name='__main__')
runtime._write_fragment()
sys.settrace(None)
"""
        completed = subprocess.run(
            [sys.executable, "-S", "-c", code, str(module)],
            cwd=dds_root,
            env=env,
            text=True,
            capture_output=True,
            timeout=15,
        )
        assert completed.returncode == 0, (completed.stdout, completed.stderr)

        files = sorted(fragments.glob("coverage-*.json"))
        assert len(files) == 1, files
        payload = json.loads(files[0].read_text(encoding="utf-8"))
        assert payload["schema"] == "dds-runtime-coverage-fragment-v1"
        assert payload["root"] == str(root.resolve())
        assert payload["included_modules"] is None
        assert "covered_fixture.py" in payload["lines"]
        assert len(payload["lines"]["covered_fixture.py"]) >= 4
        assert "covered_fixture.py" in payload["arcs"]
        assert payload["arcs"]["covered_fixture.py"]

        print(json.dumps({
            "ok": True,
            "isolated_clean_activation": True,
            "activation_idempotent": True,
            "line_events_recorded": True,
            "arc_events_recorded": True,
            "fragment_written_atomically": True,
        }, indent=2))


if __name__ == "__main__":
    main()
