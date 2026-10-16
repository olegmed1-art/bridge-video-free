"""Bounded, fixed-value hints from psycopg 3.3.4 exception text.

This is a text parser, not a socket trace. Even valid framing can be forged by
server text. Never log the input or use these hints to change connection policy.
"""
from __future__ import annotations

import ast
import ipaddress
import re

MAX_MESSAGE_CHARS = 16_384
MAX_REPORTED_ATTEMPTS = 8
_BANNER = "Multiple connection attempts failed. All failures were:"
_LITERAL = r"(?:None|'(?:[^'\\\r\n]|\\[^\r\n])*'|\"(?:[^\"\\\r\n]|\\[^\r\n])*\")"
_HEADER = re.compile(
    rf"^- host: ({_LITERAL}), port: ({_LITERAL}), hostaddr: ({_LITERAL}): (.*)$"
)
_EXPIRED = re.compile(
    r"\b(?:timeout expired|timed out|canceling statement due to statement timeout|"
    r"connection timeout expired|connection timed out)\b", re.IGNORECASE
)
_IDENTIFIER = re.compile(r"\b(?:statement_timeout|connect_timeout)\b", re.IGNORECASE)
_REJECT = re.compile(
    r"\bunsupported startup parameter(?: in options)?\s*:\s*"
    r"[\"']?([a-z_][a-z_0-9.]*)?", re.IGNORECASE
)
_REASONS = (
    ("network_unreachable", ("network is unreachable",)),
    ("no_route", ("no route to host",)),
    ("refused", ("connection refused",)),
    ("reset", ("connection reset by peer",)),
    ("dns", ("could not translate host name", "name or service not known",
             "temporary failure in name resolution", "nodename nor servname provided")),
    ("tls", ("ssl error:", "ssl syscall error:", "certificate verify failed",
             "ssl handshake failed", "tls handshake failed", "server does not support ssl")),
    ("channel_binding", ("channel binding required", "channel binding is required",
                         "channel binding mismatch")),
    ("auth", ("authentication failed", "no password supplied", "no pg_hba.conf entry")),
)


def has_explicit_timeout(message: str) -> bool:
    """A narrowly recognized expiration phrase, never a parameter name alone."""
    return type(message) is str and len(message) <= MAX_MESSAGE_CHARS and bool(_EXPIRED.search(message))


def _hints(message: str, timeout_exception: bool = False) -> dict[str, object]:
    message = message.lower()
    expired = has_explicit_timeout(message)
    evidence = ("exception_type" if timeout_exception else "explicit_phrase" if expired
                else "identifier_only" if _IDENTIFIER.search(message)
                else "token_only" if "timeout" in message else "none")
    reasons = [key for key, phrases in _REASONS if any(p in message for p in phrases)]
    if expired or timeout_exception:
        reasons.append("explicit_timeout")
    rejected = list(_REJECT.finditer(message))
    parameters = set()
    for match in rejected:
        parameter = match.group(1)
        parameters.add(parameter if parameter in {"statement_timeout", "default_transaction_read_only"}
                       else "other" if parameter else "unknown")
    if rejected:
        reasons.append("startup_parameter_rejected")
    return {"reasons": reasons or ["unknown"], "startup_parameters": sorted(parameters),
            "timeout_evidence": evidence}


def _family(address: object) -> str:
    if type(address) is not str or "%" in address:
        return "unknown"
    try:
        return "ipv4" if ipaddress.ip_address(address).version == 4 else "ipv6"
    except ValueError:
        return "unknown"


def connection_error_detail(message: str | None, *, expected_host: str,
                            timeout_exception: bool = False) -> dict[str, object]:
    """Return only enums, booleans, bounded integers, null and fixed field names.

    A multiple report must have the pinned banner, at least two valid repr
    headers, and a prefix equal to its last error. The repeated prefix is not
    counted twice. Unrecognized/missing framing never invents address attempts.
    The exception type applies to the aggregate only, not every reported address.
    """
    result: dict[str, object] = {
        "schema": "incident-1911-error-detail-v1", "evidence": "untrusted_text_hints",
        "aggregate_format": "unrecognized", "reported_attempt_count": None,
        "attempts": [], "parse_issue": None,
    }
    if type(message) is not str:
        result.update(parse_issue="unreadable_input", message_hints=_hints("", timeout_exception))
        return result
    if len(message) > MAX_MESSAGE_CHARS:
        result.update(parse_issue="input_limit", message_hints=_hints("", timeout_exception))
        return result
    # Do not parse framing containing non-printing controls or alternative line
    # separators. Ordinary libpq diagnostics use newlines and tabs.
    if any((ord(c) < 32 and c not in "\n\r\t") or c in "\x7f\x85\u2028\u2029" for c in message):
        result.update(parse_issue="control_character", message_hints=_hints("", timeout_exception))
        return result
    message = message.replace("\r\n", "\n")
    if "\r" in message:
        result.update(parse_issue="control_character", message_hints=_hints("", timeout_exception))
        return result
    result["message_hints"] = _hints(message, timeout_exception)
    lines = message.split("\n")
    if _BANNER not in message and not any(line.startswith("- host:") for line in lines):
        result["aggregate_format"] = "single"
        return result
    result["parse_issue"] = "ambiguous_format"
    if message.count(_BANNER) != 1 or _BANNER not in lines:
        return result
    banner_index = lines.index(_BANNER)
    prefix = "\n".join(lines[:banner_index]).strip()
    blocks: list[tuple[str | None, str | None, list[str]]] = []
    for line in lines[banner_index + 1:]:
        if line.startswith("- host:"):
            if len(blocks) == MAX_REPORTED_ATTEMPTS:
                result["parse_issue"] = "attempt_limit"
                return result
            match = _HEADER.fullmatch(line)
            # Bound the captured repr literals before parsing them. Delimiter
            # searches would be ambiguous when a quoted host contains ': '.
            if not match or sum(len(match.group(i)) for i in (1, 2, 3)) > 1024:
                return result
            try:
                host, port, address = (ast.literal_eval(match.group(i)) for i in (1, 2, 3))
            except (SyntaxError, ValueError):
                return result
            if port is not None and (not port.isascii() or not port.isdecimal() or not 0 < int(port) <= 65535):
                return result
            blocks.append((host, address, [match.group(4)]))
        elif not blocks or line.startswith("- host"):
            return result
        else:
            blocks[-1][2].append(line)
    if len(blocks) < 2 or not prefix or prefix != "\n".join(blocks[-1][2]).strip():
        return result
    if any(not "\n".join(body).strip() for _, _, body in blocks):
        return result
    attempts = []
    for index, (host, address, body) in enumerate(blocks, 1):
        attempts.append({"ordinal": index, "family": _family(address),
                         "host_matches_expected": type(host) is str and host == expected_host,
                         **_hints("\n".join(body))})
    result.update(aggregate_format="multiple", reported_attempt_count=len(attempts),
                  attempts=attempts, parse_issue=None)
    return result
