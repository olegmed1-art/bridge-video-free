import contextlib
from dataclasses import dataclass, field, replace
import io
import json
import unittest
from unittest.mock import patch

from ops import native_permission_hold_guard as guard
from ops import oracle_light_active_hold_attest as hold


@dataclass
class Target:
    database: str = 'neondb'
    session_owner: str = 'neondb_owner'
    owner: str = 'neondb_owner'
    recipient: str = hold.ROLE
    neon: dict = field(default_factory=lambda: dict(guard.EXPECTED_TARGET['neon']))


class Writer:
    def __init__(self, fail_at=None):
        self.calls = 0
        self.fail_at = fail_at

    def assert_held(self, target, operation):
        self.calls += 1
        if self.calls == self.fail_at:
            raise RuntimeError('WRITER_LOST')


class HoldGuardTests(unittest.TestCase):
    def setUp(self):
        self.target = Target()
        self.identity = hold.HoldIdentity('autopilot-lite-vnic', 123, 'a' * 32,
            '/opt/bridge-school/school-autopilot-production-light/releases/' + 'b' * 40,
            'c' * 64)

    def test_requires_explicit_writer_and_approved_identity(self):
        with self.assertRaisesRegex(hold.Blocked, 'HOLD_WRITER_COORDINATION_REQUIRED'):
            guard.HoldMaintenanceGuard(self.target, 'apply', self.identity)
        with self.assertRaisesRegex(hold.Blocked, 'APPROVED_HOLD_IDENTITY_REQUIRED'):
            guard.HoldMaintenanceGuard(self.target, 'apply', None, Writer())
        with self.assertRaisesRegex(hold.Blocked, 'HOLD_TARGET_MISMATCH'):
            guard.HoldMaintenanceGuard(replace(self.target, recipient='wrong'), 'apply', self.identity, Writer())
        with self.assertRaisesRegex(hold.Blocked, 'HOLD_TARGET_MISMATCH'):
            target = replace(self.target, neon=dict(self.target.neon, branch_id='wrong'))
            guard.HoldMaintenanceGuard(target, 'apply', self.identity, Writer())

    def test_live_checks_are_bracketed_by_writer_checks(self):
        writer = Writer()
        instance = guard.HoldMaintenanceGuard(self.target, 'apply', self.identity, writer)
        with patch.object(hold, 'attest', return_value=self.identity) as attest:
            instance.assert_held(self.target, 'apply')
            instance.assert_held(self.target, 'apply')
            self.assertEqual(attest.call_count, 2)
        self.assertEqual(writer.calls, 4)

    def test_every_identity_change_latches_failure(self):
        for field_name, value in (('hostname', 'wrong'), ('pid', 124),
                                  ('invocation_id', 'd' * 32), ('release', 'wrong'),
                                  ('fingerprint', 'e' * 64)):
            with self.subTest(field=field_name):
                instance = guard.HoldMaintenanceGuard(self.target, 'apply', self.identity, Writer())
                with patch.object(hold, 'attest', return_value=replace(self.identity, **{field_name: value})):
                    with self.assertRaisesRegex(hold.Blocked, 'HOLD_IDENTITY_CHANGED'):
                        instance.assert_held(self.target, 'apply')
                with patch.object(hold, 'attest', return_value=self.identity) as attest:
                    with self.assertRaisesRegex(hold.Blocked, 'HOLD_GUARD_ALREADY_FAILED'):
                        instance.assert_held(self.target, 'apply')
                    attest.assert_not_called()

    def test_writer_loss_before_or_after_audit_latches(self):
        for position in (1, 2):
            instance = guard.HoldMaintenanceGuard(self.target, 'apply', self.identity, Writer(position))
            with patch.object(hold, 'attest', return_value=self.identity) as attest:
                with self.assertRaisesRegex(RuntimeError, 'WRITER_LOST'):
                    instance.assert_held(self.target, 'apply')
                self.assertEqual(attest.call_count, position - 1)
                with self.assertRaisesRegex(hold.Blocked, 'HOLD_GUARD_ALREADY_FAILED'):
                    instance.assert_held(self.target, 'apply')

    def test_failed_audit_and_operation_change_refuse(self):
        instance = guard.HoldMaintenanceGuard(self.target, 'apply', self.identity, Writer())
        with patch.object(hold, 'attest', side_effect=hold.Blocked('POST_CHECK_DRIFT')):
            with self.assertRaisesRegex(hold.Blocked, 'POST_CHECK_DRIFT'):
                instance.assert_held(self.target, 'apply')
        self.assertTrue(instance.failed)
        instance = guard.HoldMaintenanceGuard(self.target, 'apply', self.identity, Writer())
        with patch.object(hold, 'attest') as attest:
            with self.assertRaisesRegex(hold.Blocked, 'HOLD_SCOPE_CHANGED'):
                instance.assert_held(self.target, 'rollback')
            attest.assert_not_called()

    def test_cli_does_not_emit_private_identity(self):
        output = io.StringIO()
        with patch.object(hold, 'attest', return_value=self.identity), contextlib.redirect_stdout(output):
            hold.main()
        result = json.loads(output.getvalue())
        self.assertEqual(result['audit'], 'ACTIVE_HOLD_PASS')
        self.assertNotIn(self.identity.fingerprint, output.getvalue())
        self.assertNotIn(self.identity.invocation_id, output.getvalue())
        self.assertNotIn(self.identity.release, output.getvalue())


if __name__ == '__main__':
    unittest.main()
