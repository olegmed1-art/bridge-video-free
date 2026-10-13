"""Bounded, byte-exact Linux /proc/net/route emptiness check; never emits rows."""
import re

MAX_BYTES = 4096
MAX_ROWS = 31
# Linux v6.17 fib_route_seq_show: seq_printf("%-127s\\n", this literal).
HEADER_TEXT = b"Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT"
HEADER = HEADER_TEXT.ljust(127, b" ") + b"\n"
STATUSES = ("not_observed", "empty", "header_only", "routes_present", "malformed", "too_large", "read_failed")
ROW = re.compile(rb"(?:[A-Za-z0-9_.:@+\-]{1,15}|\*)\t[0-9A-F]{8}\t[0-9A-F]{8}\t[0-9A-F]{4}\t0\t0\t[0-9]{1,10}\t[0-9A-F]{8}\t[0-9]{1,10}\t[0-9]{1,10}\t[0-9]{1,10}")


def summary(status, header_present=None, row_count=None):
    value = {"row_count": row_count, "header_present": header_present, "parse_status": status}
    validate_summary(value)
    return value


def validate_summary(value):
    if type(value) is not dict or set(value) != {"row_count", "header_present", "parse_status"}:
        raise ValueError("invalid_route_summary")
    status, header, rows = value["parse_status"], value["header_present"], value["row_count"]
    if type(status) is not str or status not in STATUSES:
        raise ValueError("invalid_route_summary")
    if status in ("not_observed", "read_failed"):
        valid = header is None and rows is None
    elif status in ("malformed", "too_large"):
        valid = type(header) is bool and rows is None
    elif status in ("empty", "header_only"):
        valid = type(rows) is int and rows == 0 and header is (status == "header_only")
    else:
        valid = header is True and type(rows) is int and 1 <= rows <= MAX_ROWS
    if not valid:
        raise ValueError("invalid_route_summary")


def parse_ipv4_routes(data):
    if type(data) is not bytes:
        return summary("malformed", False)
    header = data.startswith(HEADER)
    if len(data) > MAX_BYTES:
        return summary("too_large", header)
    if data == b"":
        return summary("empty", False, 0)
    if data == HEADER:
        return summary("header_only", True, 0)
    if not header:
        return summary("malformed", False)
    body = data[len(HEADER):]
    if not body.endswith(b"\n"):
        return summary("malformed", True)
    rows = body.split(b"\n")[:-1]
    if not 1 <= len(rows) <= MAX_ROWS:
        return summary("malformed", True)
    # Classification only: every nonempty body is rejected regardless of format.
    # Recognized canonical rows are counted, never returned or logged.
    if any(len(row) != 127 or ROW.fullmatch(row.rstrip(b" ")) is None for row in rows):
        return summary("malformed", True)
    return summary("routes_present", True, len(rows))


def read_ipv4_routes(path):
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
    except BaseException:
        return summary("read_failed")
    return parse_ipv4_routes(data)
