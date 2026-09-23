from pathlib import Path

from ops.oracle_light_fresh_candidate_restore import schema_digest_texts

ROOT = Path(__file__).resolve().parents[1]


def main():
    first = "header\n\\restrict abc\nbody\n\\unrestrict abc\n"
    second = "header\n\\restrict xyz\nbody\n\\unrestrict xyz\n"
    assert schema_digest_texts([first]) == schema_digest_texts([second])
    workflow = (ROOT / ".github/workflows/oracle-light-fresh-candidate.yml").read_text()
    restore = (ROOT / "ops/oracle_light_fresh_candidate_restore.py").read_text()
    manifest = (ROOT / "ops/oracle_autopilot_snapshot_manifest.sql").read_text()
    assert workflow.count("--snapshot=\"$snapshot\"") == 3
    keeper = (ROOT / "ops/oracle_autopilot_snapshot_keeper.py").read_text()
    assert "REPEATABLE READ, READ ONLY" in keeper
    assert "SERIALIZABLE" not in keeper
    connection = (ROOT / "ops/oracle_autopilot_dump_connection.py").read_text()
    assert "sslrootcert=/secrets/ca-certificates.crt" in connection
    assert "postgres@sha256:0377e72c5289ed2f98cf61b1a9c2db9eb9d300317fe14244492fbc94343b3d04" in workflow
    assert '"${pg_local[@]}" pg_restore' in workflow
    assert '--exit-on-error "/backup/$dump"' in workflow
    assert '-v "$PWD:/backup:ro" "$pg_image"' in workflow
    assert "ALTER DATABASE recovery OWNER TO neondb_owner;" in workflow
    assert '"${pg_local[@]}" psql' in workflow
    assert "-f /backup/ops/oracle_autopilot_snapshot_manifest.sql" in workflow
    assert "test -s reference-manifest.json" in workflow
    assert "\n            pg_restore -h 127.0.0.1" not in workflow
    assert "ALLOW_CONNECTIONS false CONNECTION LIMIT 0" in restore
    assert "SOURCE_TARGET_MANIFEST_MISMATCH" in restore
    assert "copy_stream_to_container(source, remote_dump)" in restore
    assert '"docker", "cp"' not in restore
    assert 'set -C; cat > "$1"' in restore
    assert '"postgres:600"' in restore
    assert "failure_fence" in restore and "UNSAFE_STATE" in restore
    assert "pg_policy" not in manifest  # covered by canonical schema-only digest
    assert "unexpected_schemas" in manifest
    print("oracle_light_fresh_candidate_contract=PASS")


if __name__ == "__main__":
    main()
