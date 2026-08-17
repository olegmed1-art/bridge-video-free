from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from checkpointing import sha256_file
from run_authorization import (
    AuthorizationError,
    build_authorization,
    validate_authorization,
    validate_engine_environment,
)

TOKEN = "dds-explicit-selftest-token-2026"
NOW = datetime(2026, 8, 17, 9, 30, tzinfo=timezone.utc)


@contextmanager
def environment(**values: str | None):
    old = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def expect_blocked(fn, text: str) -> None:
    try:
        fn()
    except AuthorizationError as exc:
        assert text.lower() in str(exc).lower(), (text, str(exc))
    else:
        raise AssertionError(f"Expected AuthorizationError containing {text!r}")


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        work = root / "work"
        work.mkdir()
        corpus_hash = hashlib.sha256(b"selftest-corpus").hexdigest()
        (work / "corpus_summary.json").write_text(
            json.dumps({"raw_sha256": corpus_hash, "count": 10}),
            encoding="utf-8",
        )
        predictions = root / "locked_predictions.jsonl"
        predictions.write_text('{"task_id":"T1","locked":true}\n', encoding="utf-8")
        prediction_hash = sha256_file(predictions)
        auth_path = root / "authorization.json"
        manifest = build_authorization(
            authorization_id="AUTH-SELFTEST-001",
            approval_token=TOKEN,
            stage="pilot",
            splits=["train"],
            corpus_hash=corpus_hash,
            predictions_hash=prediction_hash,
            not_before="2026-08-17T09:00:00Z",
            expires_at="2026-08-17T10:00:00Z",
            max_tasks=10,
        )
        auth_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        valid = validate_authorization(
            path=auth_path,
            token=TOKEN,
            stage="pilot",
            splits=["train"],
            work=work,
            predictions=predictions,
            requested_tasks=10,
            open_sealed=False,
            now=NOW,
        )
        assert valid.authorization_id == "AUTH-SELFTEST-001"
        assert valid.corpus_sha256 == corpus_hash
        assert valid.predictions_sha256 == prediction_hash
        assert valid.max_tasks == 10

        expect_blocked(
            lambda: validate_authorization(
                path=auth_path,
                token="wrong-token-with-enough-length",
                stage="pilot",
                splits=["train"],
                work=work,
                predictions=predictions,
                requested_tasks=10,
                open_sealed=False,
                now=NOW,
            ),
            "token",
        )
        expect_blocked(
            lambda: validate_authorization(
                path=auth_path,
                token=TOKEN,
                stage="main",
                splits=["train"],
                work=work,
                predictions=predictions,
                requested_tasks=10,
                open_sealed=False,
                now=NOW,
            ),
            "stage",
        )
        expect_blocked(
            lambda: validate_authorization(
                path=auth_path,
                token=TOKEN,
                stage="pilot",
                splits=["validation"],
                work=work,
                predictions=predictions,
                requested_tasks=10,
                open_sealed=False,
                now=NOW,
            ),
            "split",
        )
        original_predictions = predictions.read_text(encoding="utf-8")
        predictions.write_text(original_predictions + '{"task_id":"T2","locked":true}\n', encoding="utf-8")
        expect_blocked(
            lambda: validate_authorization(
                path=auth_path,
                token=TOKEN,
                stage="pilot",
                splits=["train"],
                work=work,
                predictions=predictions,
                requested_tasks=10,
                open_sealed=False,
                now=NOW,
            ),
            "prediction sha",
        )
        predictions.write_text(original_predictions, encoding="utf-8")

        expect_blocked(
            lambda: validate_authorization(
                path=auth_path,
                token=TOKEN,
                stage="pilot",
                splits=["train"],
                work=work,
                predictions=predictions,
                requested_tasks=11,
                open_sealed=False,
                now=NOW,
            ),
            "exceeds",
        )
        expect_blocked(
            lambda: validate_authorization(
                path=auth_path,
                token=TOKEN,
                stage="pilot",
                splits=["train"],
                work=work,
                predictions=predictions,
                requested_tasks=10,
                open_sealed=False,
                now=datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc),
            ),
            "expired",
        )

        sealed_manifest = build_authorization(
            authorization_id="AUTH-SELFTEST-SEALED",
            approval_token=TOKEN,
            stage="pilot",
            splits=["sealed_test"],
            corpus_hash=corpus_hash,
            predictions_hash=prediction_hash,
            not_before="2026-08-17T09:00:00Z",
            expires_at="2026-08-17T10:00:00Z",
            allow_sealed=False,
            max_tasks=10,
        )
        sealed_path = root / "sealed.json"
        sealed_path.write_text(json.dumps(sealed_manifest), encoding="utf-8")
        expect_blocked(
            lambda: validate_authorization(
                path=sealed_path,
                token=TOKEN,
                stage="pilot",
                splits=["sealed_test"],
                work=work,
                predictions=predictions,
                requested_tasks=10,
                open_sealed=True,
                now=NOW,
            ),
            "sealed",
        )

        with environment(
            DDS_TEST_MODE=None,
            DDS_PREFLIGHT_MODE=None,
            DDS_TRAINING_CONFIRM="YES",
            DDS_RUN_AUTH_FILE=None,
            DDS_RUN_APPROVAL_TOKEN=None,
            DDS_RUN_AUTH_CONTEXT=None,
        ):
            expect_blocked(validate_engine_environment, "authorized_run_stage")

        with environment(
            DDS_TEST_MODE=None,
            DDS_PREFLIGHT_MODE=None,
            DDS_TRAINING_CONFIRM="YES",
            DDS_RUN_AUTH_FILE=str(auth_path),
            DDS_RUN_APPROVAL_TOKEN=TOKEN,
            DDS_RUN_AUTH_CONTEXT=valid.context_sha256,
        ):
            engine = validate_engine_environment(now=NOW)
            assert engine["authorized"] is True
            assert engine["authorization_id"] == "AUTH-SELFTEST-001"

        with environment(
            DDS_TEST_MODE="1",
            DDS_PREFLIGHT_MODE=None,
            DDS_TRAINING_CONFIRM="YES",
            DDS_RUN_AUTH_FILE=None,
            DDS_RUN_APPROVAL_TOKEN=None,
            DDS_RUN_AUTH_CONTEXT=None,
        ):
            assert validate_engine_environment(now=NOW)["mode"] == "technical_test"

        assert manifest["automatic_issuance_allowed"] is False
        assert TOKEN not in auth_path.read_text(encoding="utf-8")
        print(
            json.dumps(
                {
                    "ok": True,
                    "authorization_bound_to_corpus_and_predictions": True,
                    "wrong_token_blocked": True,
                    "wrong_stage_and_split_blocked": True,
                    "expired_authorization_blocked": True,
                    "sealed_permission_blocked": True,
                    "max_task_limit_enforced": True,
                    "direct_training_environment_blocked": True,
                    "plaintext_token_not_stored": True,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
