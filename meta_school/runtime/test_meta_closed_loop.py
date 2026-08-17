import sys
sys.path.insert(0,'meta_school/runtime')
from meta_closed_loop import *

def gates(): return {k:True for k in ('criterion','guardrails','validator','dependency','budget','stable','governance','recovery','lease','evidence','readback_plan')}
def run():
 s=MetaClosedLoop(Mode.SHADOW)
 assert not s.promotion_allowed(gates())
 a2=MetaClosedLoop(Mode.ACTIVE,True)
 assert a2.promotion_allowed(gates(),Risk.R1,True,False)
 assert not a2.promotion_allowed(gates(),Risk.R2,True,False)
 assert not a2.promotion_allowed(gates(),Risk.R1,False,False)
 assert not a2.promotion_allowed(gates(),Risk.R1,True,True)
 g=gates();g['recovery']=False;assert not a2.promotion_allowed(g)
 assert s.decide(finding=False,confidence='HIGH',canonical_change=False)=='NO_CHANGE'
 assert s.decide(finding=True,confidence='HIGH',canonical_change=True)=='OWNER_REVIEW'
 assert s.decide(finding=True,confidence='LOW',canonical_change=False)=='RETEST'
 assert s.decide(finding=True,confidence='HIGH',canonical_change=False,stale=True)=='REBASE_REQUIRED'
 assert s.decide(finding=True,confidence='HIGH',canonical_change=False,dependency_ok=False)=='REJECT'
 assert s.write_protocol(True,False,False)=='UNKNOWN_EXTERNAL_STATE'
 assert s.write_protocol(True,True,False)=='ROLLBACK_REQUIRED'
 assert s.write_protocol(True,True,True)=='CONFIRMED'
 try:s.classify_risk(Risk.R3,Risk.R1);raise AssertionError
 except GateError:pass
 try:MetaClosedLoop(Mode.SHADOW,True);raise AssertionError
 except GateError:pass
 print('META_A2_REGRESSION_PASS')
if __name__=='__main__':run()
