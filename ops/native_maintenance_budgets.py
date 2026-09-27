"""Fixed source-authenticated stage budgets; no caller-selected renewal.

The runner starts its RPC clock before the host starts either of its clocks.
PID1's existing 100-second host cap and two-second kill grace are unchanged.
The completion reserve is admission headroom, not proof that cleanup completed:
any failed exchange still requires independent host/cgroup reconciliation.
"""

STAGE_RPC_SECONDS = 80
STAGE_HOST_SECONDS = 80
STAGE_LAUNCHER_SECONDS = 160
STAGE_COMPLETION_RESERVE_SECONDS = 45
STAGE_PRELAUNCH_REQUIRED_SECONDS = STAGE_RPC_SECONDS + STAGE_COMPLETION_RESERVE_SECONDS
