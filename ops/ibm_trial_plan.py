"""Offline decision core only. No network, subprocess, credentials or cloud runner.

An executor and its independent ordinary-Stop fallback must be separately reviewed
before use. A requested Stop is not proof of STOPPED or a hard billing cap.
"""
from dataclasses import dataclass
from decimal import Decimal

INSTANCE_ID = "02c7_4463831b-c1a7-45a7-84f4-c0388c8e03b2"
API_URL = "https://eu-de.iaas.cloud.ibm.com/v1/instances/" + INSTANCE_ID + "/actions?version=2026-09-22&generation=2"
LIMIT_SECONDS = 600
ADMIN_DEADLINE_SECONDS = 120
LIMIT_USD = Decimal("10")
SERVICES = ("assistant-lab", "assistant-lab-observer", "assistant-lab-control",
            "assistant-lab-control-bridge", "universal-video-container", "bridge-ben")


def action_proposal(action):
    """Data for review, never submitted. force=false cannot be overridden."""
    if action not in {"start", "stop"}:
        raise ValueError("action_not_allowed")
    return {"method": "POST", "url": API_URL, "body": {"type": action, "force": False}}


def estimate_usd(hourly_usd, seconds, additional_usd):
    """No assumed credits/discounts. Inputs must come from a verified quote.

    Additional cost includes storage/network/licenses/tax not in the hourly rate.
    This arithmetic does not bound guest-originated external work or stop latency.
    """
    if not isinstance(hourly_usd, Decimal) or not isinstance(additional_usd, Decimal):
        raise ValueError("decimal_quote_required")
    if (not hourly_usd.is_finite() or not additional_usd.is_finite()
            or hourly_usd < 0 or additional_usd < 0 or type(seconds) is not int or seconds < 0):
        raise ValueError("invalid_quote")
    return hourly_usd * Decimal(seconds) / Decimal(3600) + additional_usd


@dataclass(frozen=True)
class Preconditions:
    owner_authorized: bool = False
    exact_instance_stopped_fresh: bool = False
    exact_backup_available_fresh: bool = False
    existing_auth_identity_verified: bool = False
    ordinary_stop_path_reviewed: bool = False
    independent_stop_operator_ready: bool = False
    existing_guest_route_identified: bool = False
    quote_verified: bool = False
    costs_within_limit: bool = False
    deadline_and_activity_observer_ready: bool = False


def blockers(preconditions):
    if not isinstance(preconditions, Preconditions):
        raise ValueError("invalid_preconditions")
    return tuple(name for name, value in vars(preconditions).items() if value is not True)


def decide(*, preconditions, started, elapsed_seconds=0, running_seconds=None,
           state="stopped", admin_verified=False, six_services_stopped=False,
           service_stop_failed=False, work_activity=False, observer_healthy=True,
           stop_submitted=False, action_outcome_unknown=False, terminal_stop_reconciled=False):
    """Pure recommendations; elapsed starts BEFORE the single Start request.

    First RUNNING observation starts a separate 120-second admin-access timer.
    Stop can be queued while STARTING; never repeat an uncertain mutation.
    """
    flags = (started, admin_verified, six_services_stopped, service_stop_failed,
             work_activity, observer_healthy, stop_submitted, action_outcome_unknown, terminal_stop_reconciled)
    if any(type(flag) is not bool for flag in flags):
        raise ValueError("invalid_boolean")
    if type(elapsed_seconds) is not int or elapsed_seconds < 0:
        raise ValueError("invalid_elapsed")
    if running_seconds is not None and (type(running_seconds) is not int or not 0 <= running_seconds <= elapsed_seconds):
        raise ValueError("invalid_running_elapsed")
    if not started:
        unsafe = action_outcome_unknown or stop_submitted or work_activity or service_stop_failed or not observer_healthy
        return "BLOCKED" if blockers(preconditions) or state != "stopped" or unsafe else "PROPOSE_SINGLE_START"
    if state == "stopped":
        return "CONFIRMED_STOPPED" if terminal_stop_reconciled and not action_outcome_unknown else "READ_RECONCILE_AND_ALERT_OPERATOR"
    if action_outcome_unknown or stop_submitted:
        return "READ_RECONCILE_AND_ALERT_OPERATOR"
    if state not in {"running", "starting", "stopping"}:
        return "READ_RECONCILE_AND_ALERT_OPERATOR"
    if state == "stopping":
        return "READ_UNTIL_STOPPED_AND_ALERT_IF_DELAYED"
    if (elapsed_seconds >= LIMIT_SECONDS or work_activity or service_stop_failed or not observer_healthy
            or (not admin_verified and running_seconds is not None and running_seconds >= ADMIN_DEADLINE_SECONDS)):
        return "PROPOSE_ORDINARY_STOP"
    if state == "starting":
        return "READ_STATE_ONLY"
    if running_seconds is None:
        return "PROPOSE_ORDINARY_STOP"
    if not admin_verified:
        return "TRY_IDENTIFIED_GUEST_ROUTE_ONLY"
    if not six_services_stopped:
        return "STOP_EXACT_SIX_SERVICES_TEMPORARILY"
    return "READ_ONLY_GUEST_DIAGNOSTICS"
