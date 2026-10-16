"""Wait for the root supervisor to verify PID1's effective pilot sandbox.

Before release this module imports only stdlib and the private-file reader;
there is no database connection, queue reservation or provider operation.
"""
from pathlib import Path
import time

from .light_native_control import root_bytes, require

ADMISSION = Path('/etc/bridge-school/light-native-pilot/admission')


def wait_for_admission():
    deadline = time.monotonic()+1800
    while time.monotonic()<deadline:
        value=root_bytes(ADMISSION,16)
        require(value in (b'HOLD\n',b'PILOT\n'),'PILOT_LAUNCH_GATE_STATE')
        if value==b'PILOT\n':
            return
        time.sleep(0.1)
    raise RuntimeError('PILOT_LAUNCH_GATE_EXPIRED')


def main():
    wait_for_admission()
    from .light_native_loader import main as execute
    execute()
