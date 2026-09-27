"""Fixed, nonrenewing stage clocks and pre-effect startup admission.

READY is emitted only from an authenticated, already-running PID1 stage unit.
At START, its whole possible remaining lifetime must fit the ORIGINAL runner
RPC deadline. No comparison of monotonic clocks from different machines occurs.
"""
import time
from ops.native_maintenance_lifetime import STAGE_RUNTIME_SECONDS, STAGE_WRAPPER_SECONDS
from ops.native_maintenance_workflow_pause import require

STAGE_WORKFLOW_SECONDS = 480
STAGE_CLOCK_MARGIN_SECONDS = 30
STAGE_CLOCK_DRIFT_SECONDS = 5
STAGE_HOST_SECONDS = 120
STAGE_KILL_SECONDS = 2
STAGE_CLEANUP_SECONDS = 10  # two independently bounded systemctl calls
STAGE_TRANSPORT_RESERVE_SECONDS = 12
STAGE_READY_REQUIRED_SECONDS = (max(STAGE_RUNTIME_SECONDS + STAGE_KILL_SECONDS,
                                    STAGE_WRAPPER_SECONDS)
                                + STAGE_CLEANUP_SECONDS + STAGE_TRANSPORT_RESERVE_SECONDS)
STAGE_STARTUP_SECONDS = 40
STAGE_RPC_SECONDS = STAGE_STARTUP_SECONDS + STAGE_READY_REQUIRED_SECONDS
STAGE_COMPLETION_RESERVE_SECONDS = 60
STAGE_PRELAUNCH_REQUIRED_SECONDS = STAGE_RPC_SECONDS + STAGE_COMPLETION_RESERVE_SECONDS
STAGE_PREPARATION_SECONDS = 40
STAGE_LAUNCHER_SECONDS = STAGE_PREPARATION_SECONDS + STAGE_PRELAUNCH_REQUIRED_SECONDS


def startup_record(kind, channel, request_digest):
    return dict(kind=kind, binding=channel.binding, request_digest=request_digest)


def admit_runner(channel, request_digest, guard):
    """One READY/START exchange; callers invoke once before serving any RPC."""
    try:
        require(not channel.__dict__.get('stage_admission_started', False), 'STAGE_ADMISSION_REUSED')
        channel.stage_admission_started = True
        require(channel.receive() == startup_record('NATIVE_STAGE_READY', channel, request_digest),
                'STAGE_READY_BINDING')
        guard()
        channel.alive()
        require(channel.deadline - time.monotonic() >= STAGE_READY_REQUIRED_SECONDS,
                'STAGE_STARTUP_TOO_SLOW')
        channel.send(startup_record('NATIVE_STAGE_START', channel, request_digest))
    except BaseException:
        channel.failed = True
        raise


def admit_host(channel, request_digest, supervisor, guard):
    """Verify PID1 first; no claim, SQL or remote mutation before START."""
    try:
        require(not channel.__dict__.get('stage_admission_started', False), 'STAGE_ADMISSION_REUSED')
        channel.stage_admission_started = True
        supervisor.assert_alive()
        guard()
        channel.send(startup_record('NATIVE_STAGE_READY', channel, request_digest))
        require(channel.receive() == startup_record('NATIVE_STAGE_START', channel, request_digest),
                'STAGE_START_BINDING')
        supervisor.assert_alive()
        guard()
        channel.alive()
    except BaseException:
        channel.failed = True
        raise
