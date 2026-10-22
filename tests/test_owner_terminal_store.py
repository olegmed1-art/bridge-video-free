"""Real temporary SQLite/filesystem/process tests, not native owner authority.

Guards are explicit test inputs. No UID/ACL/fsync implementation is mocked.
Windows exercises the portable data engine; native UID1001 binding refuses.
OWNER_TEST_TEMP_ROOT optionally selects an explicitly writable test directory;
workspace snapshots may create hard links, which the unchanged guard refuses.
"""
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from universal_video.owner_terminal_store import (
    HEAD_FIELDS, PHASES, QUERY_FIELDS, SQLiteTerminalLedger, StorageIdentity,
    StoreWatermark, TerminalOwnerStore, TerminalStoreError, ZERO)

ROOT = Path(__file__).resolve().parents[1]


def raw(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def writer(path, **kwargs):
    return SQLiteTerminalLedger(path=path, storage_identity=StorageIdentity.observe(path),
        owner_guard=lambda: None, write_enabled=True, **kwargs)


def reader(path, **kwargs):
    return SQLiteTerminalLedger(path=path, storage_identity=StorageIdentity.observe(path),
        owner_guard=lambda: None, **kwargs)


def heads():
    value = dict.fromkeys(HEAD_FIELDS)
    value.update(schema='terminal-queue-head/v1', work_key='1'*64,
        intent_sha256='2'*64, target_identity_sha256='3'*64, principal='video_worker',
        seq=1, phase='ARMED')
    previous = None
    for index, phase in enumerate(PHASES):
        value.update(seq=index+1, phase=phase,
                     previous_sha256=None if previous is None else sha(previous))
        if index == 1:
            value['claim_sha256'] = '4'*64
        if index == 2:
            value.update(selector='5'*64, assignment_sha256='6'*64,
                enrollment_sha256='7'*64, admission_record_sha256='8'*64,
                admission_anchor_sha256='9'*64)
        if index >= 3:
            value['watermark'] = '{}'
        if index == 5:
            value.update(receipt_sha256='a'*64, receipt_size=55,
                transport_job_sha256='b'*64, stream_sha256='c'*64, result_sha256='d'*64)
        if index == 6:
            value['ack_attempt_id'] = 'e'*64
        current = raw(value)
        yield current
        previous = current


def query_for(head):
    value = json.loads(head)
    return raw(dict(schema='terminal-ack-query/v1', **{k:value[k] for k in QUERY_FIELDS}))


class PhysicalStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='owner-store-', dir=os.environ.get('OWNER_TEST_TEMP_ROOT', ROOT/'tests'))
        self.path = Path(self.temp.name).resolve()/'terminal.sqlite3'
        self.path.touch()
        self.store = writer(self.path)
        self.store.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def advance_to_ack(self):
        previous = None
        for current in list(heads())[:7]:
            self.store.compare_and_swap('1'*64, None if previous is None else sha(previous), current)
            previous = current
        return query_for(previous)

    def child(self, script, *args):
        return subprocess.run([sys.executable, '-B', '-c', script, *map(str,args)],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=False)

    def test_initialize_and_readonly_restart(self):
        self.assertEqual(reader(self.path).watermark(), StoreWatermark(0, ZERO))
        with self.assertRaisesRegex(TerminalStoreError, 'WRITE_DISABLED'):
            reader(self.path).compare_and_swap('1'*64, None, next(heads()))

    def test_missing_path_does_not_create(self):
        missing = self.path.parent/'unprovisioned.sqlite3'
        with self.assertRaises(FileNotFoundError):
            SQLiteTerminalLedger(path=missing, storage_identity=self.store.identity,
                owner_guard=lambda: None, write_enabled=True)
        self.assertFalse(missing.exists())

    def test_schema_init_is_explicit_and_once(self):
        other = self.path.parent/'empty.sqlite3'
        other.touch()
        ledger = writer(other)
        with self.assertRaisesRegex(TerminalStoreError, 'SCHEMA_REQUIRED'):
            ledger.compare_and_swap('1'*64, None, next(heads()))
        self.assertEqual(other.stat().st_size, 0)
        with self.assertRaisesRegex(TerminalStoreError, 'NOT_EMPTY'):
            writer(self.path).initialize()

    def test_cas_survives_new_connection(self):
        first = next(heads())
        self.store.compare_and_swap('1'*64, None, first)
        self.assertEqual(reader(self.path).read_head('1'*64), first)
        self.assertEqual(reader(self.path).watermark().sequence, 1)

    def test_stale_writer_cannot_repeat(self):
        first = next(heads())
        self.store.compare_and_swap('1'*64, None, first)
        stale = writer(self.path)
        with self.assertRaisesRegex(TerminalStoreError, 'CAS_STALE'):
            stale.compare_and_swap('1'*64, None, first)
        with self.assertRaisesRegex(TerminalStoreError, 'WRITE_DISABLED'):
            stale.compare_and_swap('1'*64, sha(first), list(heads())[1])
        self.assertEqual(reader(self.path).read_head('1'*64), first)

    def test_skip_phase_rejected(self):
        with self.assertRaisesRegex(TerminalStoreError, 'HEAD_ORDER'):
            self.store.compare_and_swap('1'*64, None, list(heads())[2])
        self.assertIsNone(reader(self.path).read_head('1'*64))

    def test_rebinding_frozen_identity_rejected(self):
        first, second = list(heads())[:2]
        self.store.compare_and_swap('1'*64, None, first)
        changed = json.loads(second)
        changed['principal'] = 'different_worker'
        with self.assertRaisesRegex(TerminalStoreError, 'HEAD_REBIND'):
            self.store.compare_and_swap('1'*64, sha(first), raw(changed))
        self.assertEqual(reader(self.path).read_head('1'*64), first)

    def test_owner_acceptance_is_durable_before_wire(self):
        query = self.advance_to_ack()
        seen = []
        def wire():
            # Different real SQLite connection observes acceptance before wire.
            evidence = reader(self.path).read_ack(query)
            self.assertEqual(json.loads(evidence)['state'], 'ACKED')
            seen.append(evidence)
        result = self.store.accept_ack(query, wire)
        self.assertEqual(seen, [result])
        self.assertEqual(reader(self.path).read_ack(query), result)

    def test_acceptance_requires_exact_committed_head(self):
        query = self.advance_to_ack()
        changed = json.loads(query)
        changed['receipt_sha256'] = 'f'*64
        calls = []
        with self.assertRaisesRegex(TerminalStoreError, 'ACK_HEAD'):
            self.store.accept_ack(raw(changed), lambda:calls.append(1))
        self.assertEqual(calls, [])
        self.assertIsNone(reader(self.path).read_ack(query))

    def test_duplicate_never_resends_and_conflict_denies(self):
        query = self.advance_to_ack()
        calls = []
        accepted = self.store.accept_ack(query, lambda:calls.append(1))
        with self.assertRaisesRegex(TerminalStoreError, 'ALREADY_ACCEPTED_READ_ONLY'):
            writer(self.path).accept_ack(query, lambda:calls.append(2))
        changed = json.loads(query)
        changed['principal'] = 'different_worker'
        with self.assertRaisesRegex(TerminalStoreError, 'ACK_CONFLICT'):
            writer(self.path).accept_ack(raw(changed), lambda:calls.append(3))
        with self.assertRaisesRegex(TerminalStoreError, 'ACK_CONFLICT'):
            reader(self.path).read_ack(raw(changed))
        self.assertEqual(calls, [1])
        self.assertEqual(reader(self.path).read_ack(query), accepted)

    def test_lost_wire_reply_retains_acceptance_and_poisons_writer(self):
        query = self.advance_to_ack()
        def lost():
            raise OSError('test lost response')
        with self.assertRaisesRegex(TerminalStoreError, 'ACK_UNKNOWN_NO_RESEND'):
            self.store.accept_ack(query, lost)
        self.assertIsNotNone(reader(self.path).read_ack(query))
        with self.assertRaisesRegex(TerminalStoreError, 'WRITE_DISABLED'):
            self.store.accept_ack(query, lambda:self.fail('duplicate wire'))

    def test_actual_process_death_after_commit_before_wire(self):
        query = self.advance_to_ack()
        script = """import os,sys
from pathlib import Path
from universal_video.owner_terminal_store import *
p=Path(sys.argv[1]);s=SQLiteTerminalLedger(path=p,storage_identity=StorageIdentity.observe(p),owner_guard=lambda:None,write_enabled=True)
s.accept_ack(sys.argv[2].encode(),lambda:os._exit(73))
"""
        done = self.child(script, self.path, query.decode())
        self.assertEqual(done.returncode, 73, done.stderr.decode())
        self.assertIsNotNone(reader(self.path).read_ack(query))
        with self.assertRaisesRegex(TerminalStoreError, 'ALREADY_ACCEPTED_READ_ONLY'):
            writer(self.path).accept_ack(query, lambda:self.fail('must not resend'))

    def test_actual_process_death_before_commit_does_not_accept(self):
        query = self.advance_to_ack()
        script = """import os,sys
from pathlib import Path
from universal_video.owner_terminal_store import *
from universal_video.owner_terminal_store import _canonical
p=Path(sys.argv[1]);s=SQLiteTerminalLedger(path=p,storage_identity=StorageIdentity.observe(p),owner_guard=lambda:None,write_enabled=True)
c=s._open(write=True);c.execute('BEGIN IMMEDIATE');b=s._history(c)
q=__import__('json').loads(sys.argv[2]);r=_canonical(dict(schema='terminal-owner-acked/v1',state='ACKED',binding=q))
c.execute('INSERT INTO terminal_events VALUES (?,?,?,?,?,?)',s._append('ACK',q['work_key'],r,b))
os._exit(74)
"""
        done = self.child(script, self.path, query.decode())
        self.assertEqual(done.returncode, 74, done.stderr.decode())
        try:
            self.assertIsNone(reader(self.path).read_ack(query))
        except TerminalStoreError as error:
            # A hot rollback journal may require explicit native DB recovery.
            # Read-only UNKNOWN is safe and never authorizes wire replay.
            self.assertIn('READ_UNKNOWN', str(error))

    def test_real_lock_busy_fails_without_retry(self):
        blocker = sqlite3.connect(self.path, isolation_level=None)
        try:
            blocker.execute('BEGIN IMMEDIATE')
            with self.assertRaisesRegex(TerminalStoreError, 'CAS_UNKNOWN'):
                self.store.compare_and_swap('1'*64, None, next(heads()))
        finally:
            blocker.close()
        self.assertIsNone(reader(self.path).read_head('1'*64))

    def test_real_competing_processes_have_one_cas_winner(self):
        script = """import sys
from pathlib import Path
from universal_video.owner_terminal_store import *
try:
 p=Path(sys.argv[1]);s=SQLiteTerminalLedger(path=p,storage_identity=StorageIdentity.observe(p),owner_guard=lambda:None,write_enabled=True)
 s.compare_and_swap('1'*64,None,sys.argv[2].encode());sys.exit(0)
except Exception:sys.exit(2)
"""
        args = [sys.executable, '-B', '-c', script, str(self.path), next(heads()).decode()]
        a = subprocess.Popen(args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        b = subprocess.Popen(args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        a.communicate(timeout=15)
        b.communicate(timeout=15)
        self.assertEqual(sorted([a.returncode,b.returncode]), [0,2])
        self.assertEqual(reader(self.path).watermark().sequence, 1)

    def test_retained_watermark_detects_older_database(self):
        first = next(heads())
        self.store.compare_and_swap('1'*64, None, first)
        minimum = self.store.watermark()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute('DELETE FROM terminal_events')
            connection.commit()
        with self.assertRaisesRegex(TerminalStoreError, 'BELOW_RETAINED_MINIMUM'):
            reader(self.path, minimum=minimum).watermark()

    def test_history_tampering_rejected(self):
        self.store.compare_and_swap('1'*64, None, next(heads()))
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute('UPDATE terminal_events SET record_sha=?', ('a'*64,))
            connection.commit()
        with self.assertRaisesRegex(TerminalStoreError, 'HISTORY'):
            reader(self.path).read_head('1'*64)

    def test_inode_replacement_rejected(self):
        self.path.rename(self.path.parent/'retained-old.sqlite3')
        self.path.touch()
        with self.assertRaisesRegex(TerminalStoreError, 'STORAGE_CHANGED'):
            self.store.read_head('1'*64)

    def test_unknown_authorization_fails_closed(self):
        with self.assertRaisesRegex(TerminalStoreError, 'OWNER_REFUSED'):
            SQLiteTerminalLedger(path=self.path, storage_identity=StorageIdentity.observe(self.path),
                owner_guard=lambda:False)

    @unittest.skipUnless(os.name != 'posix' or getattr(os,'geteuid',lambda:-1)() != 1001,
                         'This test verifies refusal outside the selected native realm')
    def test_native_wrapper_does_not_fake_uid_on_portable_tests(self):
        with self.assertRaisesRegex(TerminalStoreError, 'NATIVE_UID1001_REQUIRED'):
            TerminalOwnerStore(path=self.path, storage_identity=StorageIdentity.observe(self.path),
                owner_guard=lambda:None, write_enabled=True)


if __name__ == '__main__':
    unittest.main()
