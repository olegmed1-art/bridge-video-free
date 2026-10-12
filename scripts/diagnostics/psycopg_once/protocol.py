"""Fixed-schema output boundary. No arbitrary strings are returned."""
import json
from pathlib import Path

from observer import SCHEMA, PHRASES, MAX_RETAINED_ATTEMPTS, MAX_MS, MAX_COUNTER

RESULT_SCHEMA = "incident-1911-native-once-v2"
MAX_BYTES = 65536
HINTS = {name for name, _ in PHRASES} | {"unknown"}
STATUSES = {"initializing", "configuration_rejected", "runtime_rejected", "connecting",
            "connect_failed", "call_returned", "cleanup_failed", "internal_failure"}


def number(value, maximum, minimum=0):
    return type(value) is int and minimum <= value <= maximum


def exact_keys(value, keys):
    return type(value) is dict and set(value) == set(keys.split())


def valid_hints(value):
    return type(value) is list and len(value) <= len(HINTS) and all(type(x) is str and x in HINTS for x in value)


def valid_runtime(value):
    if value is None:
        return True
    return (exact_keys(value, "psycopg binary typing_extensions implementation python libpq build_libpq") and
            value["psycopg"] == value["binary"] == "3.3.4" and value["typing_extensions"] == "4.15.0" and
            value["implementation"] == "binary" and type(value["python"]) is list and
            len(value["python"]) == 3 and value["python"][:2] == [3, 12] and
            all(number(x, 99) for x in value["python"]) and
            number(value["libpq"], 9999999, 100000) and number(value["build_libpq"], 9999999, 100000))


def valid_offline(value, sha):
    try:
        return (exact_keys(value, "schema head_sha status tests failures errors network_namespace_isolated nonroot "
                          "secret_present socket_attempts native_connect_calls runtime") and
                value["schema"] == "incident-1911-offline-v2" and value["head_sha"] == sha and
                value["status"] == "pass" and number(value["tests"], 10000, 40) and
                all(type(value[k]) is int and value[k] == 0 for k in ("failures", "errors", "socket_attempts", "native_connect_calls")) and
                value["network_namespace_isolated"] is True and value["nonroot"] is True and
                value["secret_present"] is False and value["runtime"] is not None and valid_runtime(value["runtime"]))
    except Exception:
        return False


def valid_observation(value):
    if not exact_keys(value, "schema client_source_contract mode hard_attempt_cap_enforced retained_attempt_limit "
                      "observed_starts observed_completions retention_overflow contract_errors ignored_records "
                      "pending_observation paired_observations_complete internal_libpq_transport_attempt_count "
                      "individual_sqlstates_available public_call_returned attempts"):
        return False
    if not (value["schema"] == SCHEMA and value["client_source_contract"] == "3.3.4" and
            value["mode"] == "passive_logging_only" and value["hard_attempt_cap_enforced"] is False and
            value["retained_attempt_limit"] == MAX_RETAINED_ATTEMPTS and
            value["internal_libpq_transport_attempt_count"] is None and
            value["individual_sqlstates_available"] is False and value["public_call_returned"] is None):
        return False
    for key in ("retention_overflow", "pending_observation", "paired_observations_complete"):
        if type(value[key]) is not bool:
            return False
    for key in ("observed_starts", "observed_completions", "contract_errors", "ignored_records"):
        if not number(value[key], MAX_COUNTER):
            return False
    rows = value["attempts"]
    if type(rows) is not list or len(rows) > MAX_RETAINED_ATTEMPTS:
        return False
    for index, row in enumerate(rows, 1):
        if not (exact_keys(row, "index family outcome duration_ms hints") and
                type(row["index"]) is int and row["index"] == index and
                row["family"] in {"ipv4", "ipv6", "unknown"} and
                row["outcome"] in {"incomplete", "failed", "attempt_succeeded"} and
                (row["duration_ms"] is None or number(row["duration_ms"], MAX_MS)) and valid_hints(row["hints"])):
            return False
    if value["observed_completions"] > value["observed_starts"] or len(rows) != min(value["observed_starts"], MAX_RETAINED_ATTEMPTS):
        return False
    complete = bool(value["observed_starts"]) and not (value["retention_overflow"] or value["contract_errors"] or
                 value["pending_observation"]) and value["observed_starts"] == value["observed_completions"]
    return value["paired_observations_complete"] == complete


def _valid_result(value):
    return (exact_keys(value, "schema status connect_calls public_call_returned close_attempted close_succeeded "
                       "publication_failed failure_hints runtime observations") and
            value["schema"] == RESULT_SCHEMA and value["status"] in STATUSES and
            number(value["connect_calls"], 1) and
            all(type(value[x]) is bool for x in ("public_call_returned", "close_attempted", "close_succeeded", "publication_failed")) and
            (not value["public_call_returned"] or value["connect_calls"] == 1) and
            (not value["close_succeeded"] or value["close_attempted"]) and
            valid_hints(value["failure_hints"]) and valid_runtime(value["runtime"]) and
            valid_observation(value["observations"]))


def valid_result(value):
    try:
        return bool(_valid_result(value))
    except Exception:
        return False


def atomic_result(path, value):
    if not valid_result(value):
        raise ValueError("invalid_result")
    data = json.dumps(value, separators=(",", ":"), ensure_ascii=True)
    if len(data) > MAX_BYTES:
        raise ValueError("result_too_large")
    path = Path(path)
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="ascii", newline="\n") as target:
        target.write(data)
        target.flush()
    temp.replace(path)


def read_result(path):
    try:
        with Path(path).open("rb") as source:
            raw = source.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return None
        value = json.loads(raw)
        return value if valid_result(value) else None
    except Exception:
        return None
