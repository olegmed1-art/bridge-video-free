from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from authorized_run_stage import (
    build_child_environment,
    build_command,
    consumed_marker_path,
    parser as wrapper_parser,
)
from launch_authorization import AuthorizationError, APPROVAL_PHRASE, issue_receipt, verify_and_consume

COMMIT = "a" * 40
NONCE = "N" * 40
NOW = datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc)


def expect_failure(fragment: str, fn) -> None:
    try:
        fn()
    except AuthorizationError as exc:
        assert fragment in str(exc), (fragment, str(exc))
    else:
        raise AssertionError(f"Expected AuthorizationError containing {fragment!r}")


def verify(path: Path, manifest: Path, consume: Path, **overrides):
    values = {
        "receipt_path": path,
        "manifest_path": manifest,
        "nonce": NONCE,
        "scope": "main_train",
        "repository": "olegmed1-art/bridge-video-free",
        "ref_name": "dds-training-local",
        "commit_sha": COMMIT,
        "actor": "olegmed1-art",
        "triggering_actor": "olegmed1-art",
        "event_name": "workflow_dispatch",
        "consume_dir": consume,
        "now": NOW + timedelta(minutes=1),
    }
    values.update(overrides)
    return verify_and_consume(**values)


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        manifest = root / "tasks.jsonl"
        manifest.write_text('{"task_id":"T1"}\n', encoding="utf-8")
        receipt_path = root / "receipt.json"
        receipt = issue_receipt(
            out_path=receipt_path,
            repository="olegmed1-art/bridge-video-free",
            ref_name="dds-training-local",
            commit_sha=COMMIT,
            expected_commit_sha=COMMIT,
            actor="olegmed1-art",
            triggering_actor="olegmed1-art",
            scope="main_train",
            manifest_path=manifest,
            nonce=NONCE,
            approval_phrase=APPROVAL_PHRASE,
            ttl_minutes=20,
            now=NOW,
        )
        assert receipt["scope"] == "main_train"
        assert receipt["manifest_sha256"]

        expect_failure(
            "nonce",
            lambda: verify(receipt_path, manifest, root / "consume-wrong", nonce="X" * 40),
        )
        expect_failure(
            "commit_sha mismatch",
            lambda: verify(receipt_path, manifest, root / "consume-commit", commit_sha="b" * 40),
        )
        expect_failure(
            "actor mismatch",
            lambda: verify(receipt_path, manifest, root / "consume-actor", actor="someone-else"),
        )
        expect_failure(
            "not allowed from event",
            lambda: verify(receipt_path, manifest, root / "consume-event", event_name="push"),
        )
        expect_failure(
            "expired",
            lambda: verify(
                receipt_path,
                manifest,
                root / "consume-expired",
                now=NOW + timedelta(minutes=21),
            ),
        )

        changed = root / "changed.jsonl"
        changed.write_text('{"task_id":"T2"}\n', encoding="utf-8")
        expect_failure(
            "manifest changed",
            lambda: verify(receipt_path, changed, root / "consume-manifest"),
        )

        consume = root / "consumed"
        accepted = verify(receipt_path, manifest, consume)
        assert accepted["receipt_id"] == receipt["receipt_id"]
        markers = list(consume.glob("*.consumed"))
        assert len(markers) == 1
        marker = consumed_marker_path(consume, receipt["receipt_id"])
        assert marker == markers[0]

        # Prove the authorized wrapper passes the exact marker/scope fields that the
        # interpreter-start sitecustomize guard requires before run_stage can import.
        base_env = {
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_SHA": COMMIT,
            "GITHUB_REPOSITORY": "olegmed1-art/bridge-video-free",
        }
        child_env = build_child_environment(
            base_env=base_env,
            receipt_id=receipt["receipt_id"],
            scope="main_train",
            consume_dir=consume,
        )
        assert child_env["DDS_TRAINING_CONFIRM"] == "YES"
        assert child_env["DDS_AUTHORIZED_LAUNCH"] == "YES"
        assert child_env["DDS_LAUNCH_RECEIPT_ID"] == receipt["receipt_id"]
        assert child_env["DDS_LAUNCH_CONSUMED_MARKER"] == str(marker)
        assert child_env["DDS_LAUNCH_SCOPE"] == "main_train"
        marker_payload = json.loads(marker.read_text(encoding="utf-8"))
        assert marker_payload["receipt_id"] == child_env["DDS_LAUNCH_RECEIPT_ID"]
        assert marker_payload["commit_sha"] == child_env["GITHUB_SHA"]
        assert marker_payload["scope"] == child_env["DDS_LAUNCH_SCOPE"]

        expect_failure(
            "already been consumed",
            lambda: verify(receipt_path, manifest, consume),
        )
        expect_failure(
            "Consumed receipt marker is missing",
            lambda: build_child_environment(
                base_env=base_env,
                receipt_id="f" * 64,
                scope="main_train",
                consume_dir=consume,
            ),
        )

        args = wrapper_parser().parse_args([
            "--receipt", str(receipt_path),
            "--nonce", NONCE,
            "--scope", "sealed_test",
            "--manifest", str(manifest),
            "--consume-dir", str(root / "other"),
            "--stage", "main",
            "--work", "work/main",
            "--predictions", "sealed.jsonl",
            "--run-id", "sealed-selftest",
            "--no-generate-followups",
        ])
        command = build_command(args)
        assert command[:3] == [command[0], "run_stage.py", "evaluate"]
        assert "--splits" in command and "sealed_test" in command
        assert "--open-sealed" in command
        assert "--start" in command

        bad_phrase_path = root / "bad.json"
        expect_failure(
            "approval phrase",
            lambda: issue_receipt(
                out_path=bad_phrase_path,
                repository="olegmed1-art/bridge-video-free",
                ref_name="dds-training-local",
                commit_sha=COMMIT,
                expected_commit_sha=COMMIT,
                actor="olegmed1-art",
                triggering_actor="olegmed1-art",
                scope="main_train",
                manifest_path=manifest,
                nonce=NONCE,
                approval_phrase="СТАРТ",
                now=NOW,
            ),
        )

        print(json.dumps({
            "ok": True,
            "one_time_receipt": True,
            "wrong_nonce_blocked": True,
            "wrong_commit_blocked": True,
            "wrong_actor_blocked": True,
            "push_event_blocked": True,
            "expired_receipt_blocked": True,
            "changed_manifest_blocked": True,
            "wrapper_binds_consumed_marker_to_preimport_guard": True,
            "missing_consumed_marker_blocked": True,
            "sealed_scope_adds_open_sealed": True,
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
