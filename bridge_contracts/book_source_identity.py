"""Book locator identity only; never fetches or authorizes a source.

Only the observed registry form drive:<file-ID> is supported as an alias.
Aliases additionally require an independently verified immutable asset binding.
"""
import re
from urllib.parse import urlsplit

_DRIVE = re.compile(r"drive:([A-Za-z0-9_-]{20,128})\Z")


def canonical_book_locator(value: str) -> str:
    if not isinstance(value, str) or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise ValueError("BOOK_SOURCE_LOCATOR_REJECTED")
    match = _DRIVE.fullmatch(value)
    if match:
        return "https://drive.google.com/file/d/" + match[1] + "/view"
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError
        parsed.port
    except ValueError:
        raise ValueError("BOOK_SOURCE_LOCATOR_REJECTED") from None
    return value


def is_public_book_locator(value: str) -> bool:
    try:
        return canonical_book_locator(value) == value
    except (ValueError, TypeError):
        return False


def book_source_matches(stored: str, citation: str) -> bool:
    if not is_public_book_locator(citation):
        return False
    try:
        return canonical_book_locator(stored) == citation
    except (ValueError, TypeError):
        return False


def needs_asset_binding(stored: str) -> bool:
    return isinstance(stored, str) and bool(_DRIVE.fullmatch(stored))
