import sys
sys.path.insert(0,'meta_school/runtime')
from meta_closed_loop import *

def run():
 m=MetaClosedLoop(Mode.SHADOW)
 assert m.promotion_allowed({k:True for k in ('criterion','guardrails','validator','dependency','budget','stable','governance','recovery','lease','evidence')}) is False
 assert m.decide(finding=False,confidence='HIGH',canonical_change=False)=='NO_CHANGE'
 assert m.decide(finding=True,confidence='HIGH',canonical_change=True)=='OWNER_REVIEW'
 assert m.decide(finding=True,confidence='LOW',canonical_change=False)=='RETEST'
 assert m.decide(finding=True,confidence='HIGH',canonical_change=False,stale=True)=='REBASE_REQUIRED'
 assert m.decide(finding=True,confidence='HIGH',canonical_change=False,dependency_ok=False)=='REJECT'
 assert m.decide(finding=True,confidence='HIGH',canonical_change=False,cost_ok=False)=='REJECT'
 assert m.decide(finding=True,confidence='HIGH',canonical_change=False,validator_ok=False)=='REJECT'
 assert m.decide(finding=True,confidence='HIGH',canonical_change=False)=='SHADOW_PROMOTE_RECOMMENDATION'
 assert m.write_protocol(True,False,False)=='UNKNOWN_EXTERNAL_STATE'
 try: m.classify_risk(Risk.R3,Risk.R1); raise AssertionError('downgrade allowed')
 except GateError: pass
 try: MetaClosedLoop(Mode.SHADOW,True); raise AssertionError('shadow authority allowed')
 except GateError: pass
 print('META_REGRESSION_PASS')
if __name__=='__main__':run()
