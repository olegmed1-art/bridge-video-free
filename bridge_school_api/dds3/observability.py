from __future__ import annotations
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable

ERRORS={"DDS_UNAVAILABLE","INVALID_DEAL","INVALID_IMAGE","AMBIGUOUS_VISION","INVALID_POSITION","TIMEOUT","ENGINE_OUTPUT_INVALID"}
@dataclass
class DDSMetrics:
    calls:int=0; failures:int=0; total_ms:float=0.0; max_ms:float=0.0
    def record(self,ms:float,ok:bool)->None:
        self.calls+=1; self.total_ms+=ms; self.max_ms=max(self.max_ms,ms); self.failures+=0 if ok else 1
    def snapshot(self)->dict[str,Any]:
        return {"calls":self.calls,"failures":self.failures,"avg_ms":self.total_ms/self.calls if self.calls else 0.0,"max_ms":self.max_ms}

def measured(metrics:DDSMetrics,fn:Callable[[],Any])->Any:
    start=perf_counter(); ok=False
    try:
        value=fn(); ok=True; return value
    finally: metrics.record((perf_counter()-start)*1000,ok)
