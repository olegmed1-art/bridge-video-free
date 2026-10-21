"""Local immutable receipt retention; consistency is not issuer authenticity.

The trusted Books host supplies actual call returns, never browser fields. This
SQLite store is a private local seam, not a production DB migration or service.
Reading never creates a file/table. Paths and bindings belong to the host.
"""
from copy import deepcopy
from contextlib import closing
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3

from evolutionary_course.book_teacher_consumer import build_card_map
from evolutionary_course.book_teacher_delivery_consumer_v2 import bind_import_receipt_v2, _delivery

MAX_BYTES = 1024 * 1024


class BookReceiptError(ValueError):
    pass


def _need(ok, reason):
    if not ok:
        raise BookReceiptError(reason)


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


@dataclass(frozen=True)
class BookReceiptBinding:
    """Trusted host configuration fingerprint, not an authorization receipt."""
    reference: str
    sha256: str
    school_id: str
    source_id: str


def _validate_bundle(value):
    _need(type(value) is dict and set(value) == {
        'source_packet', 'import_receipt', 'snapshot', 'expected_school_id'}, 'BOOK_RECEIPT_BUNDLE_FIELDS')
    bound = bind_import_receipt_v2(build_card_map(value['source_packet']),
        value['import_receipt'], value['snapshot']['recorded_event'],
        expected_school_id=value['expected_school_id'], test_only=True)
    # Reuse the exact pinned consumer validator, including genuine receipt
    # fields. It proves consistency only; the live issuer is checked separately.
    _delivery(value['snapshot'], bound)
    return bound


class BookReceiptRepository:
    def __init__(self, path):
        self.path = Path(path)
        _need(self.path.is_absolute() and not self.path.is_symlink(), 'BOOK_RECEIPT_PATH_REQUIRED')

    def _connect(self, mode):
        _need(self.path.is_file() and not self.path.is_symlink(), 'BOOK_RECEIPT_STORE_UNAVAILABLE')
        return sqlite3.connect(self.path.as_uri() + '?mode=' + mode, uri=True, timeout=2)

    @classmethod
    def create(cls, path):
        """Explicitly create a NEW private local store; never replace an old one."""
        store = cls(path)
        fd = os.open(store.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with closing(store._connect('rw')) as conn, conn:
            conn.execute('CREATE TABLE book_teacher_receipt ('
                         'reference TEXT PRIMARY KEY NOT NULL, payload BLOB NOT NULL, '
                         'payload_sha256 TEXT NOT NULL)')
        return store

    def retain(self, reference, *, source_packet, import_receipt, snapshot, expected_school_id):
        """Atomically retain complete observed outputs; no producer/publisher call."""
        _need(type(reference) is str and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', reference),
              'BOOK_RECEIPT_REFERENCE_REQUIRED')
        value = deepcopy({'source_packet': source_packet, 'import_receipt': import_receipt,
                          'snapshot': snapshot, 'expected_school_id': expected_school_id})
        _validate_bundle(value)
        raw = _encode(value)
        _need(len(raw) <= MAX_BYTES, 'BOOK_RECEIPT_BUNDLE_TOO_LARGE')
        checksum = sha256(raw).hexdigest()
        with closing(self._connect('rw')) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            old = conn.execute('SELECT payload,payload_sha256 FROM book_teacher_receipt '
                               'WHERE reference=?', (reference,)).fetchone()
            if old is not None:
                _need(old == (raw, checksum), 'BOOK_RECEIPT_REFERENCE_IMMUTABLE')
            else:
                conn.execute('INSERT INTO book_teacher_receipt VALUES (?,?,?)',
                             (reference, raw, checksum))
        return BookReceiptBinding(reference, checksum, expected_school_id,
                                  value['source_packet']['source']['registry_source_id'])

    def load(self, binding):
        """Fresh read-only open; validate bytes, trusted fingerprint and contracts."""
        _need(type(binding) is BookReceiptBinding, 'BOOK_RECEIPT_BINDING_REQUIRED')
        _need(type(binding.reference) is str and
              re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', binding.reference), 'BOOK_RECEIPT_REFERENCE_REQUIRED')
        _need(type(binding.sha256) is str and re.fullmatch(r'[0-9a-f]{64}', binding.sha256),
              'BOOK_RECEIPT_FINGERPRINT_REQUIRED')
        with closing(self._connect('ro')) as conn:
            row = conn.execute('SELECT payload,payload_sha256 FROM book_teacher_receipt '
                               'WHERE reference=?', (binding.reference,)).fetchone()
        _need(row is not None, 'BOOK_RECEIPT_NOT_FOUND')
        raw, stored_hash = row
        _need(type(raw) is bytes and len(raw) <= MAX_BYTES and
              stored_hash == binding.sha256 == sha256(raw).hexdigest(), 'BOOK_RECEIPT_FINGERPRINT_MISMATCH')
        value = json.loads(raw)
        _need(_encode(value) == raw, 'BOOK_RECEIPT_ENCODING')
        _validate_bundle(value)
        _need(value['expected_school_id'] == binding.school_id and
              value['source_packet']['source']['registry_source_id'] == binding.source_id,
              'BOOK_RECEIPT_SCOPE_MISMATCH')
        return value
