"""Bind live HOLD continuity to an independently supplied writer guard.

This supplies host checks, not GitHub group ownership or operator agreement.
An approved private HoldIdentity is mandatory; no first-call auto-approval.
"""
from dataclasses import asdict

from ops import oracle_light_active_hold_attest as hold


EXPECTED_TARGET = {
    'database': 'neondb',
    'session_owner': 'neondb_owner',
    'owner': 'neondb_owner',
    'recipient': hold.ROLE,
    'neon': {
        'project_id': 'misty-poetry-18012774',
        'branch_id': 'br-aged-mud-b1i64914',
        'endpoint_id': 'ep-noisy-pine-b1pe30sf',
        'host': hold.HOST,
    },
}


class HoldMaintenanceGuard:
    def __init__(self, target, operation, approved_identity, writer_guard=None):
        hold.require(asdict(target) == EXPECTED_TARGET, 'HOLD_TARGET_MISMATCH')
        hold.require(operation in ('apply', 'rollback'), 'HOLD_OPERATION_INVALID')
        hold.require(type(approved_identity) is hold.HoldIdentity,
                     'APPROVED_HOLD_IDENTITY_REQUIRED')
        hold.require(writer_guard is not None and callable(getattr(writer_guard, 'assert_held', None)),
                     'HOLD_WRITER_COORDINATION_REQUIRED')
        self.target = asdict(target)
        self.operation = operation
        self.approved_identity = approved_identity
        self.writer_guard = writer_guard
        self.failed = False

    def assert_held(self, target, operation):
        hold.require(not self.failed, 'HOLD_GUARD_ALREADY_FAILED')
        try:
            hold.require(asdict(target) == self.target and operation == self.operation,
                         'HOLD_SCOPE_CHANGED')
            self.writer_guard.assert_held(target, operation)
            actual = hold.attest()
            hold.require(actual == self.approved_identity, 'HOLD_IDENTITY_CHANGED')
            self.writer_guard.assert_held(target, operation)
        except BaseException:
            # A failed observation cannot be erased by a later successful one.
            self.failed = True
            raise
