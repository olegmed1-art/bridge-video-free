import json
import subprocess
import sys

def test_metadata_stage_refuses_aggregate_only_snapshot(tmp_path):
    source = tmp_path / "bad.json"; output = tmp_path / "manifest.json"
    source.write_text(json.dumps({"sources": [], "authors": [], "bridgeclub_audit": [], "material_queue": []}))
    completed = subprocess.run([sys.executable, "scripts/stage_world_metadata.py", str(source), "--emit-manifest", str(output)], capture_output=True, text=True)
    assert completed.returncode != 0
    assert not output.exists()


def test_metadata_stage_rejects_expected_counts_without_stable_ids(tmp_path):
    source = tmp_path / "bad_ids.json"; output = tmp_path / "manifest.json"
    source.write_text(json.dumps({"sources": [{}] * 245, "authors": [{}] * 42,
                                  "bridgeclub_audit": [{}] * 95, "material_queue": [{}] * 20}))
    completed = subprocess.run([sys.executable, "scripts/stage_world_metadata.py", str(source),
                                "--emit-manifest", str(output)], capture_output=True, text=True)
    assert completed.returncode != 0
    assert "requires nonempty string" in completed.stderr
    assert not output.exists()


def test_metadata_stage_rejects_id_only_rows_without_tab_schema(tmp_path):
    def rows(count, id_field, prefix):
        return [{id_field: f"{prefix}-{n}"} for n in range(count)]
    source = tmp_path / "id_only.json"; output = tmp_path / "manifest.json"
    source.write_text(json.dumps({
        "sources": rows(245, "source_id", "s"),
        "authors": rows(42, "author_id", "a"),
        "bridgeclub_audit": rows(95, "audit_id", "b"),
        "material_queue": rows(20, "material_id", "m"),
    }))
    completed = subprocess.run([sys.executable, "scripts/stage_world_metadata.py", str(source),
                                "--emit-manifest", str(output)], capture_output=True, text=True)
    assert completed.returncode != 0
    assert "requires nonempty scalar fields" in completed.stderr
    assert not output.exists()
