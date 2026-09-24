import base64
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parent))
import oracle_light_pg_tunnel_install as target


class TunnelGuards(unittest.TestCase):
    def test_key_identity_and_options(self):
        raw = base64.b64encode(b'test-public-key').decode()
        pin = 'SHA256:' + base64.b64encode(hashlib.sha256(b'test-public-key').digest()).decode().rstrip('=')
        line = 'restrict ssh-ed25519 ' + raw + ' github-light-access'
        with patch.object(target, 'FINGERPRINT', pin):
            self.assertEqual(target.public_key(line), 'ssh-ed25519 ' + raw)
            for invalid in [line + '\n' + line, line.replace('restrict ', ''),
                            line.replace('restrict ', 'restrict,port-forwarding '), '']:
                with self.subTest(invalid=invalid), self.assertRaises(AssertionError):
                    target.public_key(invalid)

    def test_effective_config_rejects_shadowing_or_broadening(self):
        settings = {line.strip().split(' ', 1)[0].lower(): line.strip().split(' ', 1)[1]
                    for line in target.CONFIG.splitlines() if line.startswith('    ')}
        settings['disableforwarding'] = 'no'
        target.validate_effective(settings)
        for name, value in [('allowtcpforwarding', 'yes'), ('permitopen', 'any'),
                            ('permitlisten', 'any'), ('forcecommand', 'none'),
                            ('allowstreamlocalforwarding', 'yes'), ('passwordauthentication', 'yes'),
                            ('authorizedkeysfile', '.ssh/authorized_keys')]:
            with self.subTest(name=name), self.assertRaises(AssertionError):
                target.validate_effective({**settings, name: value})


if __name__ == '__main__':
    unittest.main()
