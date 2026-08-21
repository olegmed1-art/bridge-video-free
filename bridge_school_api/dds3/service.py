"""Universal fail-closed Bridge School DDS3 computation service."""
from __future__ import annotations
import json, os, subprocess
from dataclasses import dataclass
from typing import Any
from .model import BridgeDeal
from .screenshot import ObservedField, ScreenshotDealObservation
DDS_UPSTREAM="v3.0.0+cdd13cf5b700788ac8c1391501b42445b3129b45"
class DDSUnavailable(RuntimeError): pass
@dataclass(frozen=True)
class DDS3Config:
    executable:str=os.getenv("DDS3_CLI","/opt/bridge-school-dds3/dds_pbn_cli")
    timeout_seconds:float=float(os.getenv("DDS3_TIMEOUT_SECONDS","15"))
def solve_table(*,pbn:str,dealer:str="N",vulnerability:str="None",config:DDS3Config|None=None)->dict[str,Any]:
    cfg=config or DDS3Config()
    if not pbn or not pbn.strip(): raise ValueError("pbn is required")
    try: proc=subprocess.run([cfg.executable,dealer,vulnerability,pbn],check=False,capture_output=True,text=True,timeout=cfg.timeout_seconds)
    except (OSError,subprocess.TimeoutExpired) as exc: raise DDSUnavailable("DDS_UNAVAILABLE") from exc
    if proc.returncode!=0: raise DDSUnavailable("DDS_UNAVAILABLE")
    try: result=json.loads(proc.stdout)
    except json.JSONDecodeError as exc: raise DDSUnavailable("DDS_UNAVAILABLE") from exc
    result.update({"engine":"DDS3","engine_version":DDS_UPSTREAM,"operation":"dd_table","input_validated":True,"fallback_used":False})
    return result
def solve_deal(deal:BridgeDeal,*,config:DDS3Config|None=None)->dict[str,Any]:
    deal.validate(); return solve_table(pbn=deal.to_pbn(),dealer=deal.dealer,vulnerability=deal.vulnerability,config=config)
def solve_screenshot_observation(observation:ScreenshotDealObservation,*,config:DDS3Config|None=None)->dict[str,Any]:
    """Vision-adapter output -> canonical deal -> DDS3, preserving metadata provenance."""
    deal, provenance=observation.canonicalize(); result=solve_deal(deal,config=config); result["board"]=provenance; return result
def _field(v:Any)->ObservedField|None:
    if v is None:return None
    if isinstance(v,dict):return ObservedField(v.get("value"),v.get("confidence"),v.get("source","screenshot"))
    return ObservedField(v)
def compute(request:dict[str,Any],*,config:DDS3Config|None=None)->dict[str,Any]:
    operation=request.get("operation","dd_table")
    if operation!="dd_table": raise ValueError(f"unsupported DDS3 operation: {operation}")
    if "screenshot_observation" in request:
        raw=request["screenshot_observation"]
        extra={k:_field(v) for k,v in raw.get("extra_metadata",{}).items()}
        obs=ScreenshotDealObservation(hands=raw["hands"],board_number=_field(raw.get("board_number")),dealer=_field(raw.get("dealer")),vulnerability=_field(raw.get("vulnerability")),extra_metadata={k:v for k,v in extra.items() if v is not None})
        return solve_screenshot_observation(obs,config=config)
    if "deal" in request:
        raw=request["deal"]; return solve_deal(BridgeDeal(raw["hands"],raw.get("dealer","N"),raw.get("vulnerability","None")),config=config)
    return solve_table(pbn=request["pbn"],dealer=request.get("dealer","N"),vulnerability=request.get("vulnerability","None"),config=config)
