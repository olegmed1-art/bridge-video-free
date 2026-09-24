import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).parent))
import oracle_light_route_lease as target


class RoutingProtocol(unittest.TestCase):
    def test_exact_route_and_rejection(self):
        baseline = {'version':1,'backend':'neon','database':'autopilot','epoch':0}
        for backend in ['neon','paused','postgresql']:
            self.assertEqual(target.validate_route({**baseline,'backend':backend})['backend'],backend)
        for patch in [{'backend':'other'},{'database':'neondb'},{'epoch':True},{'epoch':-1},
                      {'epoch':2**31},{'version':2},{'host':'attacker.invalid'}]:
            with self.subTest(patch=patch), self.assertRaises(AssertionError):
                target.validate_route({**baseline,**patch})

    def test_forced_command_never_executes_input(self):
        from unittest.mock import patch
        for value in ['id','/bin/sh','route-v1; id','internal-sftp','']:
            with self.subTest(value=value), patch.dict(target.os.environ,{'SSH_ORIGINAL_COMMAND':value}):
                self.assertEqual(target.main(),1)

    def test_bundle_has_exact_pinned_program(self):
        import oracle_light_route_install as installer
        program = Path(__file__).with_name('oracle_light_route_lease.py').read_bytes()
        self.assertEqual(installer.hashlib.sha256(program).hexdigest(),installer.PROGRAM_SHA256)
        self.assertIn('command="'+installer.COMMAND+'"',installer.NEW_OPTIONS)
        self.assertIn('ForceCommand '+installer.COMMAND,installer.NEW_CONFIG)


if __name__ == '__main__':
    unittest.main()
