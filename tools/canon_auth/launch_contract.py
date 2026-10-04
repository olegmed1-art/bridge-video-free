"""Explicit finite launch and permit contracts; no environment, network or SQL."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from uuid import UUID
from .resident_preflight import Refused

STAGES = ("baseline", "initial", "revoke", "reactivate", "emergency")
PHASES = {"baseline": "preflight", "initial": "baseline",
          "revoke": "active", "reactivate": "revoked"}
ORIGIN = "https://bridge-video-free.vercel.app"


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, default=str).encode()).hexdigest()


def require(condition, code):
    if not condition:
        raise Refused(code)


def timestamp(value):
    require(isinstance(value, str) and bool(re.fullmatch(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z", value)), "utc_timestamp_required")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise Refused("utc_timestamp_required") from None


@dataclass(frozen=True)
class Launch:
    intent: str
    code_sha: str
    runtime_sha: str
    deployment_sha: str
    deployment_id: str
    validation_build_id: str
    controller_run_id: int
    plan_hash: str
    module_hash: str
    open_at: datetime
    admission_until: datetime
    stage_until: datetime

    @classmethod
    def parse(cls, value):
        fields = set(cls.__dataclass_fields__)
        require(isinstance(value, dict) and set(value) == fields, "launch_fields_refused")
        try:
            UUID(value["intent"])
        except (ValueError, TypeError, AttributeError):
            raise Refused("intent_required") from None
        for key in ("code_sha", "runtime_sha", "deployment_sha"):
            require(isinstance(value[key], str) and bool(re.fullmatch(r"[a-f0-9]{40}", value[key])),
                    "launch_source_required")
        require(value["deployment_sha"] == value["code_sha"], "original_code_binding_required")
        for key in ("plan_hash", "module_hash"):
            require(isinstance(value[key], str) and bool(re.fullmatch(r"[a-f0-9]{64}", value[key])),
                    "launch_hash_required")
        for key in ("deployment_id", "validation_build_id"):
            require(isinstance(value[key], str) and bool(re.fullmatch(r"dpl_[A-Za-z0-9]{8,100}", value[key])),
                    "deployment_binding_required")
        require(value["deployment_id"] != value["validation_build_id"], "validation_no_promotion_required")
        require(type(value["controller_run_id"]) is int and value["controller_run_id"] > 0,
                "first_controller_run_required")
        prepared = dict(value)
        for key in ("open_at", "admission_until", "stage_until"):
            prepared[key] = timestamp(value[key])
        require(timedelta(0) < prepared["admission_until"] - prepared["open_at"] <= timedelta(minutes=5)
                and prepared["admission_until"] <= prepared["stage_until"]
                and prepared["stage_until"] - prepared["open_at"] <= timedelta(minutes=15),
                "finite_window_required")
        return cls(**prepared)

    def public(self):
        return {key: (getattr(self, key).isoformat().replace("+00:00", "Z")
                      if isinstance(getattr(self, key), datetime) else getattr(self, key))
                for key in self.__dataclass_fields__}

    @property
    def fingerprint(self):
        return digest(self.public())

    def admit(self, now):
        require(now.tzinfo is not None and self.open_at <= now < self.admission_until,
                "admission_window_refused")

    def normal(self, now):
        require(now.tzinfo is not None and self.open_at <= now < self.stage_until,
                "stage_window_refused")


@dataclass(frozen=True)
class Permit:
    contract_hash: str
    stage: str
    phase: str
    evidence_hash: str
    recovery_hash: str
    observed_at: datetime

    def verify(self, launch, stage, now):
        require(stage in PHASES and self.stage == stage and self.phase == PHASES[stage]
                and self.contract_hash == launch.fingerprint, "phase_permit_refused")
        require(bool(re.fullmatch(r"[a-f0-9]{64}", self.evidence_hash))
                and bool(re.fullmatch(r"[a-f0-9]{64}", self.recovery_hash))
                and self.observed_at.tzinfo is not None
                and timedelta(0) <= now - self.observed_at <= timedelta(seconds=60),
                "phase_permit_stale")

    def public(self):
        return {**self.__dict__, "observed_at": self.observed_at.isoformat().replace("+00:00", "Z")}

    @classmethod
    def parse(cls, value):
        require(isinstance(value, dict) and set(value) == set(cls.__dataclass_fields__),
                "phase_permit_refused")
        return cls(**(value | {"observed_at": timestamp(value["observed_at"])}))
