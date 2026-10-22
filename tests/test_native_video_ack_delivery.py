"""Real SQLite acceptance + actual recovery branch; explicit owner-port doubles.

No native authentication, transport, PostgreSQL or production proof is implied.
"""
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest

from tests.test_owner_terminal_store import heads, query_for, writer, reader
from universal_video.diana20_terminal_queue import TerminalRecoveryAdapter
from universal_video.owner_terminal_store import TerminalStoreError


class AckDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='native-ack-', dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name).resolve() / 'ledger.sqlite3'
        self.path.touch()
        self.store = writer(self.path)
        self.store.initialize()
        self.all_heads = list(heads())
        previous = None
        import hashlib
        for current in self.all_heads[:7]:
            self.store.compare_and_swap('1'*64, None if previous is None else
                hashlib.sha256(previous).hexdigest(), current)
            previous = current
        self.query = query_for(previous)
        self.sends = 0

    def recover(self):
        # Exercise the original recovery method with persisted head/ACK data.
        # Native guard and read-window capabilities are explicit test doubles.
        a = TerminalRecoveryAdapter.__new__(TerminalRecoveryAdapter)
        a._lock = threading.RLock()
        a._failed = a._restart = False
        a._transport = None
        a._guard = lambda: None
        a._read_head = lambda: reader(self.path).read_head('1'*64)
        a.owner = SimpleNamespace(read_window=lambda query: nullcontext(),
                                  read_ack=reader(self.path).read_ack)
        return a.recover()

    def test_owner_commit_before_wire_is_not_confirmed_delivery(self):
        def lost_before_delivery():
            raise OSError('controlled wire uncertainty')
        with self.assertRaisesRegex(TerminalStoreError, 'ACK_UNKNOWN_NO_RESEND'):
            self.store.accept_ack(self.query, lost_before_delivery)
        result = self.recover()
        self.assertEqual(result['state'], 'OWNER_ACCEPTED_DELIVERY_UNKNOWN')
        self.assertTrue(result['owner_accepted'])
        self.assertFalse(result['delivery_confirmed'])
        with self.assertRaisesRegex(TerminalStoreError, 'ALREADY_ACCEPTED_READ_ONLY'):
            writer(self.path).accept_ack(self.query, lambda: self.fail('must not resend'))

    def test_even_known_wire_success_without_durable_final_head_is_unknown(self):
        self.store.accept_ack(self.query, lambda: None)
        self.assertEqual(self.recover()['state'], 'OWNER_ACCEPTED_DELIVERY_UNKNOWN')

    def test_only_final_committed_head_confirms_delivery(self):
        self.store.accept_ack(self.query, lambda: None)
        import hashlib
        self.store.compare_and_swap('1'*64, hashlib.sha256(self.all_heads[6]).hexdigest(),
                                    self.all_heads[7])
        result = self.recover()
        self.assertEqual(result['state'], 'DELIVERY_CONFIRMED_READBACK')
        self.assertTrue(result['owner_accepted'])
        self.assertTrue(result['delivery_confirmed'])

    def test_no_owner_acceptance_remains_unknown_without_resend(self):
        result = self.recover()
        self.assertEqual(result['state'], 'UNKNOWN_ACK_NO_RESEND')
        self.assertFalse(result['owner_accepted'])
        self.assertFalse(result['delivery_confirmed'])


if __name__ == '__main__':
    unittest.main()
