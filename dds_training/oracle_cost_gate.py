from __future__ import annotations

from dataclasses import dataclass
from typing import Any

OCI_E5_OCPU_HOURLY_USD = 0.039318
OCI_E5_MEMORY_GB_HOURLY_USD = 0.0026212
HOST_OCPUS = 6.0
HOST_MEMORY_GB = 12.0
MAX_30K_RUNTIME_SECONDS = 10_800
MAX_30K_COST_USD = 2.0


class CostGateError(RuntimeError):
    pass


@dataclass(frozen=True)
class CostGate:
    max_runtime_seconds: int
    max_cost_usd: float
    estimated_ceiling_usd: float
    hourly_retail_usd: float


def validate_30k_budget(value: Any) -> CostGate:
    if not isinstance(value, dict):
        raise CostGateError("30k request requires an explicit budget object")
    runtime = value.get("max_runtime_seconds")
    cost = value.get("max_cost_usd")
    if isinstance(runtime, bool) or not isinstance(runtime, int) or not 1 <= runtime <= MAX_30K_RUNTIME_SECONDS:
        raise CostGateError("max_runtime_seconds must be 1..10800")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not 0 < float(cost) <= MAX_30K_COST_USD:
        raise CostGateError("max_cost_usd must be >0 and <= 2.00")
    hourly = HOST_OCPUS * OCI_E5_OCPU_HOURLY_USD + HOST_MEMORY_GB * OCI_E5_MEMORY_GB_HOURLY_USD
    ceiling = runtime / 3600 * hourly
    if ceiling > float(cost) + 1e-9:
        raise CostGateError(
            "declared cost limit $%.4f is below runtime ceiling $%.4f" % (float(cost), ceiling)
        )
    return CostGate(runtime, float(cost), round(ceiling, 6), round(hourly, 7))
