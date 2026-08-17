from dataclasses import dataclass
from enum import Enum
class Mode(str,Enum): SHADOW='SHADOW'; ACTIVE='ACTIVE'
class Risk(str,Enum): R0='R0'; R1='R1'; R2='R2'; R3='R3'; R4='R4'
class GateError(RuntimeError): pass
@dataclass(frozen=True)
class FrozenContract:
 run_id:str; stable_version:str; risk:Risk; max_candidates:int=3; max_retests:int=2
class MetaClosedLoop:
 def __init__(self,mode:Mode,promotion_authority=False):
  if mode is Mode.SHADOW and promotion_authority: raise GateError('shadow cannot have promotion authority')
  self.mode=mode; self.promotion_authority=promotion_authority
 def classify_risk(self,current:Risk,proposed:Risk)->Risk:
  order=list(Risk)
  if order.index(proposed)<order.index(current): raise GateError('automatic risk downgrade forbidden')
  return proposed
 def promotion_allowed(self,gates:dict)->bool:
  if self.mode is Mode.SHADOW or not self.promotion_authority:return False
  req=('criterion','guardrails','validator','dependency','budget','stable','governance','recovery','lease','evidence')
  return all(gates.get(k) is True for k in req)
 def decide(self,*,finding,confidence,canonical_change,stale=False,inconclusive=False,dependency_ok=True,cost_ok=True,validator_ok=True):
  if canonical_change:return 'OWNER_REVIEW'
  if stale:return 'REBASE_REQUIRED'
  if not finding:return 'NO_CHANGE'
  if confidence=='LOW' or inconclusive:return 'RETEST'
  if not dependency_ok or not cost_ok or not validator_ok:return 'REJECT'
  return 'SHADOW_PROMOTE_RECOMMENDATION' if self.mode is Mode.SHADOW else 'PROMOTE_CANDIDATE'
 @staticmethod
 def write_protocol(intent_persisted,response_known,readback_matches):
  if not intent_persisted:return 'BLOCKED'
  if not response_known:return 'UNKNOWN_EXTERNAL_STATE'
  return 'CONFIRMED' if readback_matches else 'UNKNOWN_EXTERNAL_STATE'
