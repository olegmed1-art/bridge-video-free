"""Fresh owner checks at existing terminal effects, without an auth substitute.

The owning application/route binds the actual fresh assertion while its original
serialization scope is held. Context is local to that execution realm; it is
never an IPC/native identity or a cached authority decision. No writer here.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import threading
from types import MappingProxyType
import json

from .private_admission_retention import canonical, exact_claim
from .private_result_handoff import PrivateOneJobConfigurationError

_active = ContextVar('diana20_owner_effects', default=None)


class OwnerEffectRefused(PrivateOneJobConfigurationError):
    error_code = 'OWNER_TERMINAL_EFFECT_UNKNOWN_NO_RETRY'


class _Binding:
    def __init__(self, claim, authorize):
        if not callable(authorize):
            raise OwnerEffectRefused('OWNER_TERMINAL_ASSERTION_REQUIRED')
        self.claim = canonical(exact_claim(claim))
        self.authorize = authorize
        self.lock = threading.RLock()
        self.failed = self.checking = False

    def check(self, claim):
        with self.lock:
            if self.failed or self.checking:
                self.failed = True
                raise OwnerEffectRefused('OWNER_TERMINAL_BINDING_REFUSED')
            self.checking = True
            try:
                if canonical(exact_claim(claim)) != self.claim:
                    raise OwnerEffectRefused('OWNER_TERMINAL_BINDING_REFUSED')
                if self.authorize(MappingProxyType(dict(claim))) is not None:
                    raise OwnerEffectRefused('OWNER_TERMINAL_ASSERTION_REFUSED')
                if canonical(exact_claim(claim)) != self.claim:
                    raise OwnerEffectRefused('OWNER_TERMINAL_BINDING_CHANGED')
            except BaseException:
                self.failed = True
                raise OwnerEffectRefused('OWNER_TERMINAL_AUTHORITY_LOST') from None
            finally:
                self.checking = False


@contextmanager
def owner_effect_scope(claim, authorize):
    binding = _Binding(claim, authorize)
    token = _active.set(binding)
    try:
        # Native binding may still be OPEN at application entry. Authorization
        # is fresh at each effect, after the actual admission has bound a claim.
        yield
    finally:
        binding.failed = True
        _active.reset(token)


@contextmanager
def terminal_effect(claim, *, required=False):
    binding = _active.get()
    if binding is None:
        if required:
            raise OwnerEffectRefused('OWNER_TERMINAL_SCOPE_REQUIRED')
        yield  # Ordinary unrelated worker lanes keep their existing behavior.
        return
    with binding.lock:
        binding.check(claim)  # Immediately before invoking the existing writer.
        try:
            yield
        except BaseException:
            binding.failed = True
            raise
        binding.check(claim)  # Includes the writer's returned durable ACK.


def assert_terminal_authorized(claim, *, required=False):
    binding = _active.get()
    if binding is None:
        if required:
            raise OwnerEffectRefused('OWNER_TERMINAL_SCOPE_REQUIRED')
        return
    binding.check(claim)


def _admission_claim(admission):
    binding = _active.get()
    if binding is None:
        raise OwnerEffectRefused('OWNER_TERMINAL_SCOPE_REQUIRED')
    expected = json.loads(binding.claim)
    try:
        keys = ('job_id', 'lease_token', 'attempt_count')
        if canonical({k: admission[k] for k in keys}) != canonical({k: expected[k] for k in keys}):
            raise OwnerEffectRefused('OWNER_TERMINAL_ADMISSION_MISMATCH')
    except BaseException:
        binding.failed = True
        raise OwnerEffectRefused('OWNER_TERMINAL_ADMISSION_MISMATCH') from None
    # Batch/source are kept from the original authenticated five-field binding,
    # never inferred from the narrower SQL admission argument.
    return expected


@contextmanager
def admission_terminal_effect(admission):
    claim = _admission_claim(admission)
    with terminal_effect(claim, required=True):
        yield


def assert_admission_authorized(admission):
    claim = _admission_claim(admission)
    assert_terminal_authorized(claim, required=True)
