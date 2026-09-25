"""Dormant target-bound Light provider. No entrypoint or admission controller.

Use only behind LightNativeAdapter and a separately reviewed production loader.
The target must come from verified live environment/repository evidence, never
from a task. Constructor validation cannot establish cloud access or identity.
"""
from dataclasses import asdict, dataclass
from pathlib import Path
import subprocess

from . import codex_cli_bridge as bridge
from .light_native_adapter import ProviderTarget

LIGHT_ROOT = Path('/opt/bridge-school/school-autopilot-production-light')


@dataclass(frozen=True)
class LightProvider:
    target: ProviderTarget

    def __post_init__(self):
        if type(self.target) is not ProviderTarget:
            raise ValueError('LIGHT_PROVIDER_TARGET_REQUIRED')
        bridge.validate_provider_binding(asdict(self.target))

    def _binding(self, target):
        if type(target) is not ProviderTarget or target != self.target:
            raise ValueError('PROVIDER_BINDING_CONFLICT')
        binding = asdict(target)
        bridge.validate_provider_binding(binding)
        return binding

    @staticmethod
    def _run(arguments, input_text=None, timeout=90):
        root = LIGHT_ROOT
        return subprocess.run(
            [str(root / 'runtime-bin/codex'), '-c', 'forced_login_method="chatgpt"',
             *arguments], input=input_text, text=True, capture_output=True,
            timeout=timeout, env={
                'HOME': str(root / 'runtime'),
                'CODEX_HOME': str(root / 'runtime/codex-home'),
                'PATH': '/usr/local/bin:/usr/bin:/bin',
                'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
            })

    def lookup(self, request, *, target):
        binding = self._binding(target)
        return bridge.lookup(request, state_dir=LIGHT_ROOT / 'runtime/codex-dispatch',
                             binding=binding)

    def submit(self, request, *, target):
        binding = self._binding(target)
        return bridge.submit(request, state_dir=LIGHT_ROOT / 'runtime/codex-dispatch',
                             binding=binding, runner=self._run)

    def collect(self, dispatch_id, *, target):
        binding = self._binding(target)
        return bridge.collect(dispatch_id, state_dir=LIGHT_ROOT / 'runtime/codex-dispatch',
                              binding=binding, runner=self._run)
