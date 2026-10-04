"""Independent POSIX owner-process deadline; no credential/network lookup."""
from contextlib import contextmanager
from datetime import timedelta
import signal
import threading
from .launch_contract import require
from .resident_preflight import Refused


@contextmanager
def supervise(launch, stage, clock):
    require(hasattr(signal, "SIGALRM") and threading.current_thread() is threading.main_thread(),
            "owner_posix_supervisor_required")
    require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), "existing_owner_alarm_refused")
    now = clock()
    if stage == "watchdog":
        launch.admit(now)
        seconds = (launch.stage_until-now).total_seconds()+60
    elif stage in ("inspect", "emergency"):
        seconds = 60
    else:
        launch.normal(now)
        seconds = min(60, (launch.stage_until-now).total_seconds())
    require(seconds > 0, "owner_stage_deadline_refused")
    previous = signal.getsignal(signal.SIGALRM)
    def stop(*args):
        raise Refused("owner_stage_process_deadline")
    signal.signal(signal.SIGALRM, stop)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
