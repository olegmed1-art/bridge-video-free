"""Receipt protocol and real POSIX persistence; no network or package execution."""
from dataclasses import asdict
import json
import os
import stat
import types
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ops.ibm_diagnostic_v9 import owner_admission as m
from ops import ibm_oracle_admission_readback as remote


NOW = 2_000_000


def fixtures():
    cycle = m.Cycle(1234567, "a" * 32, "b" * 40, "c" * 64)
    pins = m.Pins("d" * 64, "e" * 64, "f" * 64)
    ack = dict(kind="COLLECT_ADMIT_HANDOFF_RETAINED", control_run=cycle.control_run,
               nonce=cycle.nonce, proof_sha256=pins.proof_sha256,
               window_sha256=pins.window_sha256, live_go=False)
    readback = dict(kind="COLLECT_ADMIT_RETAINED_READBACK_V1", **asdict(cycle), **asdict(pins),
                    observed_wall_ms=NOW, expires_ms=NOW + 360000, live_go=False)
    return cycle, pins, ack, readback


class RecordingJournal:
    """An explicit fake durable port. Does not qualify POSIX persistence."""
    def __init__(self, cycle, pins):
        self.cycle, self.pins = cycle, pins
        self.events = ["durable_intent"]
        self.ack = None
        self.ack_wire_sha256 = None
        self.readback = None

    def receive_ack(self, ack):
        raw = json.dumps(ack, indent=2).encode()
        self.ack = m.validate_ack(self.cycle, self.pins, ack)
        self.ack_wire_sha256 = m.validate_ack_wire(raw, ack)
        self.events.append("durable_ack")
        return dict(receipt=ack, request_sha256=self.pins.request_sha256,
                    receipt_wire_sha256=self.ack_wire_sha256)

    def complete(self, value, now_ms):
        self.readback = m.validate_readback(self.cycle, self.pins, value, now_ms)
        self.events.append("durable_readback")

    def require_fresh(self, now_ms):
        return m.validate_readback(self.cycle, self.pins, self.readback, now_ms)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.cycle, self.pins, self.ack, self.readback = fixtures()

    def test_ack_requires_exact_kind_binding_literal_false_and_fieldset(self):
        for key, value in (("kind", "COLLECT_ADMIT_HANDOFF_VERIFIED"), ("control_run", True),
                           ("control_run", 1234568), ("nonce", "0" * 32), ("live_go", 0),
                           ("proof_sha256", "0" * 64), ("window_sha256", "0" * 64),
                           ("unexpected", False)):
            with self.subTest(key=key, value=value), self.assertRaises(m.Refused):
                m.validate_ack(self.cycle, self.pins, dict(self.ack, **{key: value}))

    def test_exact_wire_is_preserved_not_reconstructed_canonical_bytes(self):
        raw = json.dumps(self.ack, indent=3).encode() + b"\n"
        self.assertEqual(m.validate_ack_wire(raw, self.ack), m.digest(raw))
        self.assertNotEqual(m.digest(raw), m.digest(m.canonical(self.ack)))

    def test_duplicate_nonfinite_or_extra_wire_member_refused(self):
        raws = (b'{"live_go":false,"live_go":false}', b'{"a":NaN}',
                json.dumps(dict(self.ack, extra="secret-like-untrusted-data")).encode())
        for raw in raws:
            with self.subTest(raw=raw), self.assertRaises(m.Refused):
                m.validate_ack_wire(raw, self.ack)

    def test_readback_is_exact_and_does_not_imply_live_go(self):
        for key, value in (("source_sha", "0" * 40), ("runtime_package_sha", "0" * 64),
                           ("request_sha256", "0" * 64), ("nonce", "0" * 32),
                           ("control_run", True), ("live_go", 0), ("instance_id", "other")):
            with self.subTest(key=key), self.assertRaises(m.Refused):
                m.validate_readback(self.cycle, self.pins, dict(self.readback, **{key: value}), NOW)

    def test_readback_freshness_and_remaining_envelope(self):
        for value in (dict(self.readback, observed_wall_ms=NOW + 1),
                      dict(self.readback, observed_wall_ms=NOW - 10000),
                      dict(self.readback, expires_ms=NOW + 274999),
                      dict(self.readback, observed_wall_ms=True)):
            with self.subTest(value=value), self.assertRaises(m.Refused):
                m.validate_readback(self.cycle, self.pins, value, NOW)

    def test_finish_calls_readback_after_durable_original_ack(self):
        journal = RecordingJournal(self.cycle, self.pins)
        delivery = journal.receive_ack(self.ack)
        def readback(cycle, pins, ack):
            self.assertEqual(journal.events, ["durable_intent", "durable_ack"])
            self.assertEqual((cycle, pins, ack), (self.cycle, self.pins, self.ack))
            journal.events.append("authenticated_readback")
            return self.readback
        frame = m.finish_before_arm(journal, delivery, readback, clock_ms=lambda: NOW)
        self.assertEqual(journal.events[-2:], ["authenticated_readback", "durable_readback"])
        self.assertFalse(frame["live_go"])
        self.assertFalse(frame["armed"])
        self.assertFalse(frame["power_post_attempted"])
        self.assertEqual(m.validate_public_frame(frame), frame)

    def test_missing_or_changed_ack_blocks_readback(self):
        for mutation in ("absent", "request", "wire"):
            journal = RecordingJournal(self.cycle, self.pins)
            delivery = journal.receive_ack(self.ack)
            if mutation == "absent": journal.ack = None
            if mutation == "request": delivery["request_sha256"] = "0" * 64
            if mutation == "wire": delivery["receipt_wire_sha256"] = "0" * 64
            with self.subTest(mutation=mutation), self.assertRaisesRegex(m.Refused, "UNKNOWN_DO_NOT_RETRY"):
                m.finish_before_arm(journal, delivery, lambda *_: self.fail("readback reached"), clock_ms=lambda: NOW)

    def test_clock_advances_after_receipt_write_stops_success(self):
        journal = RecordingJournal(self.cycle, self.pins)
        delivery = journal.receive_ack(self.ack)
        ticks = iter((NOW, NOW + 10000))
        with self.assertRaisesRegex(m.Refused, "UNKNOWN_DO_NOT_RETRY"):
            m.finish_before_arm(journal, delivery, lambda *_: self.readback, clock_ms=lambda: next(ticks))

    def test_readback_error_never_triggers_second_receive(self):
        journal = RecordingJournal(self.cycle, self.pins)
        delivery = journal.receive_ack(self.ack)
        calls = []
        def failed(*_):
            calls.append("readback")
            raise OSError("untrusted backend detail must not escape")
        with self.assertRaisesRegex(m.Refused, "^ADMIT_OUTCOME_UNKNOWN_DO_NOT_RETRY$"):
            m.finish_before_arm(journal, delivery, failed, clock_ms=lambda: NOW)
        self.assertEqual(calls, ["readback"])

    def test_remote_readback_rechecks_permissions_of_same_inode(self):
        safe=stat.S_IFDIR|0o700
        for uid,gid,mode in ((1001,1001,stat.S_IFDIR|0o777),(0,1001,safe),(1001,0,safe)):
            changed=types.SimpleNamespace(st_dev=1,st_ino=2,st_uid=uid,st_gid=gid,st_mode=mode)
            with self.subTest(uid=uid,gid=gid,mode=mode),patch.object(remote.os,'stat',return_value=changed),patch.object(remote.os,'fstat',return_value=changed),self.assertRaises(remote.ReadbackRefused):
                remote.check_chain([(1,'fixed',2,(1001,1001,safe))])

    def test_local_receipt_directory_same_inode_mode_drift_refuses(self):
        safe=stat.S_IFDIR|0o700
        changed=types.SimpleNamespace(st_dev=1,st_ino=2,st_uid=1001,st_mode=stat.S_IFDIR|0o755)
        with patch.object(m.os,'stat',return_value=changed),patch.object(m.os,'fstat',return_value=changed),patch.object(m.os,'getuid',return_value=1001,create=True),self.assertRaises(m.Refused):
            m._check([(1,'fixed',2,(1001,safe))])


@unittest.skipUnless(os.name == "posix", "Real private no-follow persistence requires POSIX; never weaken its guards on Windows")
class PosixPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ibm-owner-admission-tests-")
        self.parent = Path(self.tmp.name)
        os.chmod(self.parent, 0o700)
        self.cycle, self.pins, self.ack, self.readback = fixtures()
        self.journal = m.Journal(str(self.parent), self.cycle, self.pins)

    def tearDown(self):
        self.journal.close()
        self.tmp.cleanup()

    def commit(self):
        self.journal.begin_delivery()
        self.journal.acknowledge(self.pins.request_sha256, self.ack, json.dumps(self.ack, indent=2).encode())
        self.journal.complete(self.readback, NOW)

    def test_intent_exists_before_delivery_and_no_second_delivery(self):
        self.assertTrue((self.journal.path / "intent.json").is_file())
        self.journal.begin_delivery()
        self.assertTrue((self.journal.path / "dispatch.json").is_file())
        with self.assertRaises(m.Refused): self.journal.begin_delivery()

    def test_reopen_attempt_refused_even_when_only_intent_exists(self):
        with self.assertRaisesRegex(m.Refused, "UNKNOWN_NO_RETRY"):
            m.Journal(str(self.parent), self.cycle, self.pins)

    def test_commit_retains_original_wire_and_separate_authenticated_request_pin(self):
        self.commit()
        stored = json.loads((self.journal.path / "ack.json").read_bytes())
        self.assertEqual(stored["receipt"], self.ack)
        self.assertEqual(stored["pins"]["request_sha256"], self.pins.request_sha256)
        self.assertEqual(stored["receipt_wire_sha256"], self.journal.ack_wire_sha256)
        self.journal.require_fresh(NOW)

    def test_deleted_ack_after_commit_refuses(self):
        self.commit()
        (self.journal.path / "ack.json").unlink()
        with self.assertRaises((m.Refused, OSError)): self.journal.require_fresh(NOW)

    def test_replaced_or_modified_receipt_refuses(self):
        self.commit()
        path = self.journal.path / "ack.json"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaises(m.Refused): self.journal.require_fresh(NOW)

    def test_parent_permission_drift_refuses(self):
        self.commit()
        os.chmod(self.parent, 0o755)
        with self.assertRaises(m.Refused): self.journal.require_fresh(NOW)

    def test_symlink_ack_after_commit_refuses(self):
        self.commit()
        path = self.journal.path / "ack.json"
        path.unlink()
        path.symlink_to(self.journal.path / "intent.json")
        with self.assertRaises((m.Refused, OSError)): self.journal.require_fresh(NOW)


if __name__ == "__main__":
    unittest.main()
