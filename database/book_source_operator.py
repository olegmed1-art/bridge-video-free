"""One-shot, fail-closed PDF identity reconciliation for an existing book source.

Run with python -m database.book_source_operator --help. The sole connection
source is the existing BRIDGE_WORKER_DATABASE_URL. No credentials are created,
copied or printed. This module never publishes WORLD, activates canon or delivers
an outbox. Its receipts are operator inputs, not an authenticity mechanism.

The pinned worker supports existing sources only. Missing-source creation is
unavailable under the current admin-owned school privilege boundary; the
--register-missing-source option is rejected locally before inputs or connection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import signal
import stat
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import psycopg

from bridge_contracts.book_source_review import validate_identity_review
from bridge_contracts.book_world import build_world_publication
from database.book_source_reconciliation import reconcile_book_source_asset

EXPECTED_PROJECT = "misty-poetry-18012774"
EXPECTED_BRANCH = "br-aged-mud-b1i64914"
EXPECTED_ENDPOINT = "ep-noisy-pine-b1pe30sf"
EXPECTED_HOST = "ep-noisy-pine-b1pe30sf-pooler.c-5.eu-central-1.aws.neon.tech"
EXPECTED_DATABASE = "neondb"
EXPECTED_PRINCIPAL = "bridge_school_worker_principal"
EXPECTED_CAPABILITY = "bridge_school_worker"
MAX_INPUT_BYTES = 2 * 1024 * 1024
SAFE_CONTRACT_ERRORS = frozenset({
    "BOOK_SOURCE_ASSET_CONFLICT", "BOOK_SOURCE_DUPLICATE_LOCATOR",
    "BOOK_SOURCE_REGISTRY_CONFLICT", "BOOK_SOURCE_SCHOOL_INACTIVE",
    "BOOK_SOURCE_IDENTITY_EFFECT_REVIEW_REQUIRED",
    "BOOK_SOURCE_IDENTITY_DECISION_CONFLICT", "BOOK_SOURCE_IDENTITY_BUDGET",
    "BOOK_WORLD_SOURCE_BINDING_MISSING", "BOOK_SOURCE_IDENTITY_REVIEW_BINDING",
    "BOOK_SOURCE_REQUIRES_IDLE_CONNECTION", "BOOK_SOURCE_ASSET_BINDING_REQUIRED",
})


class OperatorError(ValueError):
    """Only fixed local error codes; never include input/connection contents."""


class Deadline:
    """POSIX main-thread wall-clock bound, including connect and commit.

    Unsupported environments or an existing process alarm fail closed. A timeout
    closes the connection and raises through the transaction context. A lost
    COMMIT acknowledgement still requires readback; there is no automatic retry.
    """
    def __init__(self, seconds):
        self.seconds = seconds
        self.ends_at = None
        self.previous_handler = None
        self.connection = None

    def __enter__(self):
        if (threading.current_thread() is not threading.main_thread()
                or not hasattr(signal, "setitimer")
                or signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0)):
            raise OperatorError("BOOK_SOURCE_OPERATOR_DEADLINE_UNAVAILABLE")
        self.previous_handler = signal.getsignal(signal.SIGALRM)
        self.ends_at = time.monotonic() + self.seconds
        signal.signal(signal.SIGALRM, self._expired)
        signal.setitimer(signal.ITIMER_REAL, self.seconds)
        return self

    def _expired(self, signum, frame):
        # Do not leave cleanup waiting indefinitely on a broken network socket.
        # Closing aborts an open transaction; an in-flight COMMIT stays ambiguous.
        try:
            if self.connection is not None:
                self.connection.close()
        finally:
            raise OperatorError("BOOK_SOURCE_OPERATOR_DEADLINE_EXCEEDED")

    def remaining(self):
        value = self.ends_at - time.monotonic()
        if value <= 0:
            raise OperatorError("BOOK_SOURCE_OPERATOR_DEADLINE_EXCEEDED")
        return value

    def __exit__(self, *args):
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, self.previous_handler)
        return False


def strict_dsn(raw):
    """Validate exactly; never retarget a URI or fall back to another variable."""
    if (not isinstance(raw, str) or not raw or len(raw) > 8192
            or any(character.isspace() for character in raw)):
        raise OperatorError("BOOK_SOURCE_OPERATOR_DSN_POLICY")
    try:
        parsed = urlsplit(raw)
        params = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        valid = (
            parsed.scheme in ("postgresql", "postgres")
            and parsed.username == EXPECTED_PRINCIPAL
            and bool(parsed.password)
            and parsed.hostname == EXPECTED_HOST
            and parsed.port in (None, 5432)
            and parsed.path == "/" + EXPECTED_DATABASE
            and not parsed.fragment
            and params == {"sslmode": ["require"], "channel_binding": ["require"]}
        )
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise OperatorError("BOOK_SOURCE_OPERATOR_DSN_POLICY")
    return raw


def reject_libpq_environment():
    # Inspect names only. Even an empty inherited option can select defaults;
    # service, routing and future libpq PG* variables are all outside this CLI.
    if any(name.startswith("PG") for name in os.environ):
        raise OperatorError("BOOK_SOURCE_OPERATOR_LIBPQ_ENVIRONMENT_FORBIDDEN")


def _canonical_uuid(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise OperatorError("BOOK_SOURCE_OPERATOR_UUID_BINDING") from None
    return value


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise OperatorError("BOOK_SOURCE_OPERATOR_JSON_DUPLICATE_KEY")
        value[key] = item
    return value


def _constant(value):
    raise OperatorError("BOOK_SOURCE_OPERATOR_JSON_INVALID")


def read_input(path, expected_hash):
    if (not isinstance(expected_hash, str) or len(expected_hash) != 64
            or any(char not in "0123456789abcdef" for char in expected_hash)):
        raise OperatorError("BOOK_SOURCE_OPERATOR_HASH_REQUIRED")
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise OperatorError("BOOK_SOURCE_OPERATOR_REGULAR_FILE_REQUIRED")
        raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise OperatorError("BOOK_SOURCE_OPERATOR_INPUT_BUDGET")
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise OperatorError("BOOK_SOURCE_OPERATOR_INPUT_HASH_MISMATCH")
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object,
                       parse_constant=_constant)
    if not isinstance(value, dict):
        raise OperatorError("BOOK_SOURCE_OPERATOR_JSON_OBJECT_REQUIRED")
    return value


def check_identity(connection):
    """Read only server identity, role membership and forbidden broad privileges."""
    if (connection.info.host != EXPECTED_HOST
            or connection.info.port != 5432
            or connection.info.dbname != EXPECTED_DATABASE
            or connection.info.user != EXPECTED_PRINCIPAL):
        raise OperatorError("BOOK_SOURCE_OPERATOR_CONNECTION_TARGET_MISMATCH")
    with connection.cursor() as cur:
        cur.execute("""
            SELECT pg_catalog.current_database(),session_user,current_user,
                pg_catalog.pg_has_role(current_user,%s,'USAGE'),
                pg_catalog.has_schema_privilege(current_user,'public','CREATE'),
                (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user),
                (SELECT rolbypassrls FROM pg_catalog.pg_roles WHERE rolname=current_user)
            """, (EXPECTED_CAPABILITY,))
        actual = cur.fetchone()
    expected = (EXPECTED_DATABASE, EXPECTED_PRINCIPAL, EXPECTED_PRINCIPAL,
                True, False, False, False)
    if actual != expected:
        raise OperatorError("BOOK_SOURCE_OPERATOR_ACTUAL_IDENTITY_MISMATCH")


def check_capabilities(connection):
    """Read effective privileges before identity writes, without changing grants.

    SHARE ROW EXCLUSIVE table locks require table UPDATE on the identity tables.
    This existing-source operator neither creates a source nor requests school
    write privileges; source creation needs a separately reviewed operation.
    """
    with connection.cursor() as cur:
        cur.execute("""
            SELECT
                pg_catalog.has_table_privilege(current_user,'public.source','SELECT'),
                pg_catalog.has_table_privilege(current_user,'public.asset','SELECT'),
                pg_catalog.has_table_privilege(current_user,'public.source_asset','SELECT'),
                pg_catalog.has_table_privilege(current_user,'public.asset','INSERT'),
                pg_catalog.has_table_privilege(current_user,'public.source_asset','INSERT'),
                pg_catalog.has_table_privilege(current_user,'public.source','UPDATE'),
                pg_catalog.has_table_privilege(current_user,'public.asset','UPDATE'),
                pg_catalog.has_table_privilege(current_user,'public.source_asset','UPDATE'),
                pg_catalog.has_table_privilege(current_user,'public.source_identity','SELECT')
            """)
        actual = cur.fetchone()
    if (not isinstance(actual, tuple) or len(actual) != 9
            or any(value is not True for value in actual)):
        raise OperatorError("BOOK_SOURCE_OPERATOR_CAPABILITY_MISSING")


def _inputs(args):
    if ((args.target_project_id, args.target_branch_id, args.target_endpoint_id,
         args.principal) != (EXPECTED_PROJECT, EXPECTED_BRANCH, EXPECTED_ENDPOINT,
                             EXPECTED_PRINCIPAL)):
        raise OperatorError("BOOK_SOURCE_OPERATOR_EXPECTED_TARGET_MISMATCH")
    _canonical_uuid(args.school_id)
    _canonical_uuid(args.source_id)
    bundle = read_input(args.bundle, args.bundle_sha256)
    receipt = read_input(args.receipt, args.receipt_sha256)
    identity = read_input(args.identity_review, args.identity_review_sha256)
    publication = build_world_publication(bundle, receipt, identity)
    validate_identity_review(publication["source"], receipt, identity,
                             school_id=args.school_id, source_id=args.source_id)
    return bundle, receipt, identity, publication["publication_hash"]


def operate(args, state):
    # The pinned worker cannot hold the school-state lock needed for creation.
    # Reject this mode even if a caller supplies broader observed privileges.
    if args.register_missing_source:
        raise OperatorError("BOOK_SOURCE_OPERATOR_MISSING_SOURCE_UNAVAILABLE")
    with Deadline(args.timeout_seconds) as deadline:
        bundle, receipt, identity, publication_hash = _inputs(args)
        reject_libpq_environment()
        dsn = strict_dsn(os.environ.get("BRIDGE_WORKER_DATABASE_URL", ""))
        state["phase"] = "connecting"
        with psycopg.connect(
            dsn, port=5432, autocommit=True,
            connect_timeout=min(10, max(1, math.ceil(deadline.remaining()))),
            application_name="bridge-book-source-operator",
            options=("-c statement_timeout=15000 -c lock_timeout=5000 "
                     "-c idle_in_transaction_session_timeout=15000"),
        ) as connection:
            deadline.connection = connection
            check_identity(connection)
            check_capabilities(connection)
            if connection.info.transaction_status != 0:
                raise OperatorError("BOOK_SOURCE_OPERATOR_REQUIRES_IDLE_CONNECTION")

            def before_commit():
                deadline.remaining()
                check_identity(connection)
                check_capabilities(connection)
                deadline.remaining()

            deadline.remaining()
            state["phase"] = "transaction"
            result = reconcile_book_source_asset(
                connection, bundle, receipt, school_id=args.school_id,
                source_id=args.source_id, identity_receipt=identity,
                dry_run=not args.commit, before_commit=before_commit,
                register_missing_source=False,
            )
            state["phase"] = "complete"
    return {
        "schema": "book-source-operator-result-v1",
        "target": {"project_id": EXPECTED_PROJECT, "branch_id": EXPECTED_BRANCH,
                   "endpoint_id": EXPECTED_ENDPOINT, "database": EXPECTED_DATABASE,
                   "principal": EXPECTED_PRINCIPAL},
        "input_sha256": {"bundle": args.bundle_sha256, "receipt": args.receipt_sha256,
                         "identity_review": args.identity_review_sha256},
        "publication_hash": publication_hash,
        "school_id": args.school_id, "source_id": args.source_id,
        "asset_id": identity["asset_id"],
        "max_new_rows": {"source": 0,
                         "asset": 1, "source_asset": 1},
        "timeout_seconds": args.timeout_seconds,
        "new_source_rows": result["new_source_rows"],
        "new_asset_rows": result["new_asset_rows"],
        "new_link_rows": result["new_link_rows"],
        "dry_run": not args.commit, "committed": result["committed"],
        "source_updated": False, "publication_performed": False,
    }


def _timeout(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("timeout must be an integer from 1 to 60") from None
    if not 1 <= number <= 60:
        raise argparse.ArgumentTypeError("timeout must be an integer from 1 to 60")
    return number


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes invalid values, which could be a pasted DSN.
        raise OperatorError("BOOK_SOURCE_OPERATOR_ARGUMENTS_INVALID")


def parser():
    value = SafeArgumentParser(description=__doc__)
    for name in ("bundle", "receipt", "identity-review"):
        value.add_argument("--" + name, required=True)
        value.add_argument("--" + name + "-sha256", required=True)
    for name in ("school-id", "source-id", "target-project-id", "target-branch-id",
                 "target-endpoint-id", "principal"):
        value.add_argument("--" + name, required=True)
    value.add_argument("--commit", action="store_true",
                       help="commit PDF identity rows for an existing source; default is forced rollback")
    value.add_argument("--register-missing-source", action="store_true",
                       help="unavailable under the current worker privilege model; rejected locally")
    value.add_argument("--timeout-seconds", type=_timeout, default=30)
    return value


def main(argv=None):
    args = None
    state = {"phase": "validation"}
    try:
        args = parser().parse_args(argv)
        result = operate(args, state)
    except (Exception, KeyboardInterrupt) as exc:
        # Only this CLI boundary hides exception text; contract validators remain
        # explicit. Database/OS messages can contain connection or source inputs.
        code = (str(exc) if isinstance(exc, OperatorError)
                else str(exc) if isinstance(exc, ValueError) and str(exc) in SAFE_CONTRACT_ERRORS
                else "BOOK_SOURCE_OPERATOR_INTERRUPTED" if isinstance(exc, KeyboardInterrupt)
                else "BOOK_SOURCE_OPERATOR_DATABASE_FAILED" if isinstance(exc, psycopg.Error)
                else "BOOK_SOURCE_OPERATOR_INPUT_INVALID" if isinstance(
                    exc, (ValueError, TypeError, KeyError, OSError, UnicodeError))
                else "BOOK_SOURCE_OPERATOR_INTERNAL_FAILURE")
        commit_requested = bool(args and args.commit)
        outcome = ("not_confirmed" if commit_requested and state["phase"] == "transaction"
                   else "confirmed" if commit_requested and state["phase"] == "complete"
                   else "not_started" if commit_requested else "not_requested")
        print(json.dumps({"schema": "book-source-operator-error-v1", "error": code,
                          "phase": state["phase"], "commit_outcome": outcome,
                          "automatic_retry": False}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
