"""Offline cross-contract checks for combined mailbox/header diagnostics."""
import contextlib
import io
import sys
import unittest
from unittest.mock import patch

from ops import github_autopilot_db_route as target


PRIVATE = 'SYNTHETIC_PRIVATE_COMBINED_VALUE'


class UntrustedValue:
    def __str__(self):
        raise AssertionError('untrusted value rendered')

    __repr__ = __str__

    def __hash__(self):
        raise AssertionError('untrusted value hashed')


class CombinedDiagnostics(unittest.TestCase):
    def cli_error(self, stage, error):
        def fail_main(_):
            target._failure_stage = stage
            raise error

        out, err = io.StringIO(), io.StringIO()
        with patch.object(target, '_failure_stage', target.RoutingStage.ARGUMENTS), \
                patch.object(target, 'main', side_effect=fail_main), \
                patch.object(sys, 'argv', ['route', 'mailbox']), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = target.cli()
        self.assertEqual((code, out.getvalue()), (1, ''))
        self.assertNotIn(PRIVATE, err.getvalue())
        return err.getvalue()

    def test_all_typed_header_reasons_keep_fixed_two_line_output(self):
        for reason in target.LeaseHeaderReason:
            with self.subTest(reason=reason.value):
                self.assertEqual(
                    self.cli_error(target.RoutingStage.LEASE_HEADER, target.LeaseHeaderError(reason)),
                    'AUTOPILOT_DATABASE_ROUTING_FAILED stage=LEASE_HEADER\n'
                    'AUTOPILOT_LEASE_HEADER_DIAGNOSTIC reason=' + reason.value + '\n')

    def test_header_reason_cannot_leak_into_other_stages(self):
        for stage in target.RoutingStage:
            if stage is target.RoutingStage.LEASE_HEADER:
                continue
            with self.subTest(stage=stage.value):
                detail = ' error_type=OTHER sqlstate=NONE' if stage.name.startswith('MAILBOX_') else ''
                self.assertEqual(self.cli_error(stage, target.LeaseHeaderError(target.LeaseHeaderReason.EOF)),
                    'AUTOPILOT_DATABASE_ROUTING_FAILED stage=' + stage.value + detail + '\n')

    def test_untyped_header_error_never_inspects_private_metadata(self):
        class PrivateError(RuntimeError):
            @property
            def sqlstate(self):
                raise AssertionError('unexpected metadata access')

            @property
            def reason(self):
                raise AssertionError('unexpected reason access')

            def __str__(self):
                raise AssertionError('exception rendered')

        self.assertEqual(self.cli_error(target.RoutingStage.LEASE_HEADER, PrivateError(PRIVATE)),
            'AUTOPILOT_DATABASE_ROUTING_FAILED stage=LEASE_HEADER\n')

    def test_mailbox_allowed_type_and_sqlstate_tokens_remain_exact(self):
        names = {'ImportError', 'ModuleNotFoundError', 'KeyError', 'ValueError',
                 'OperationalError', 'InterfaceError', 'ProgrammingError',
                 'InvalidPassword', 'InvalidAuthorizationSpecification',
                 'InvalidCatalogName', 'InsufficientPrivilege'}
        states = {'28P01', '28000', '08000', '08001', '08006', '3D000', '42501', '53300', '57P03'}
        for name in names:
            for sqlstate in states:
                with self.subTest(name=name, sqlstate=sqlstate):
                    error = type(name, (Exception,), {'sqlstate': sqlstate})(PRIVATE)
                    self.assertEqual(self.cli_error(target.RoutingStage.MAILBOX_CONNECT, error),
                        'AUTOPILOT_DATABASE_ROUTING_FAILED stage=MAILBOX_CONNECT error_type='
                        + name + ' sqlstate=' + sqlstate + '\n')

    def test_invalid_reason_never_rendered_or_promoted(self):
        out = io.StringIO()
        with patch.object(target, '_failure_stage', target.RoutingStage.LEASE_HEADER), \
                contextlib.redirect_stderr(out):
            target.report_routing_failure(RuntimeError(PRIVATE), reason=UntrustedValue())
        self.assertEqual(out.getvalue(), 'AUTOPILOT_DATABASE_ROUTING_FAILED stage=LEASE_HEADER\n')

    def test_untrusted_sqlstate_metadata_fails_to_fixed_none(self):
        class PrivateState(str):
            def __hash__(self):
                raise AssertionError('string subclass hashed')

        class InjectedState(str):
            def __radd__(self, prefix):
                return prefix + PRIVATE

        class RaisingStateError(RuntimeError):
            @property
            def sqlstate(self):
                raise RuntimeError(PRIVATE)

        errors = [type('PrivateError', (Exception,), {'sqlstate': value})(PRIVATE)
                  for value in ([], {}, UntrustedValue(), PrivateState('28P01'),
                                InjectedState('28P01'), PRIVATE)]
        errors.append(RaisingStateError(PRIVATE))
        for error in errors:
            with self.subTest(error_type=type(error).__name__):
                self.assertEqual(self.cli_error(target.RoutingStage.MAILBOX_CONNECT, error),
                    'AUTOPILOT_DATABASE_ROUTING_FAILED stage=MAILBOX_CONNECT error_type=OTHER sqlstate=NONE\n')

    def test_reason_enum_contains_exact_approved_tokens(self):
        self.assertEqual({reason.value for reason in target.LeaseHeaderReason}, {
            'HEADER_EOF', 'HEADER_PROCESS_EXITED', 'HEADER_TIMEOUT', 'HEADER_TOO_LARGE',
            'HEADER_DECODE_INVALID', 'HEADER_JSON_INVALID', 'HEADER_RECORD_INVALID',
            'HEADER_LEASE_CLOSED', 'HEADER_UNKNOWN',
        })

    def test_untrusted_exception_class_name_fails_to_fixed_other(self):
        class HashedName(str):
            def __hash__(self):
                raise AssertionError('untrusted name hashed')

        class InjectedName(str):
            def __radd__(self, prefix):
                return prefix + PRIVATE

        class RaisingName(type):
            def __getattribute__(cls, key):
                if key == '__name__':
                    raise RuntimeError(PRIVATE)
                return super().__getattribute__(key)

        errors = []
        for name in (HashedName('OperationalError'), InjectedName('OperationalError')):
            cls = type('PrivateError', (RuntimeError,), {})
            cls.__name__ = name
            errors.append(cls(PRIVATE))
        errors.append(RaisingName('PrivateError', (RuntimeError,), {})(PRIVATE))
        for index, error in enumerate(errors):
            with self.subTest(case=index):
                self.assertEqual(self.cli_error(target.RoutingStage.MAILBOX_CONNECT, error),
                    'AUTOPILOT_DATABASE_ROUTING_FAILED stage=MAILBOX_CONNECT error_type=OTHER sqlstate=NONE\n')


if __name__ == '__main__':
    unittest.main()
