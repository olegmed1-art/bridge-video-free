import json
import subprocess
import sys

def test_metadata_stage_refuses_aggregate_only_snapshot(tmp_path):
    source = tmp_path / "bad.json"; output = tmp_path / "manifest.json"
    source.write_text(json.dumps({"sources": [], "authors": [], "bridgeclub_audit": [], "material_queue": []}))
    completed = subprocess.run([sys.executable, "scripts/stage_world_metadata.py", str(source), "--emit-manifest", str(output)], capture_output=True, text=True)
    assert completed.returncode != 0
    assert not output.exists()
