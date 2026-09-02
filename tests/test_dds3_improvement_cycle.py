import pytest
from bridge_school_api.dds3.position_contract import MoveValue,rank_move_values,trajectory
from bridge_school_api.dds3.observability import DDSMetrics,ERRORS,measured

def test_equal_optimal_moves_and_regret():
 r=rank_move_values([MoveValue('SA',10),MoveValue('SK',10),MoveValue('S2',8)])
 assert r['optimal_cards']==['SA','SK']; assert r['moves'][2]['regret']==2; assert r['moves'][2]['regret_class']=='2+'; assert r['fallback_used'] is False

def test_trajectory_first_swing_and_final_damage():
 r=trajectory([10,10,9,9,10,8]); assert r['first_swing']['after_play']==2; assert r['first_swing']['delta']==-1; assert r['final_delta']==-2

def test_metrics_success_and_failure():
 m=DDSMetrics(); assert measured(m,lambda:3)==3
 with pytest.raises(RuntimeError): measured(m,lambda:(_ for _ in ()).throw(RuntimeError()))
 s=m.snapshot(); assert s['calls']==2 and s['failures']==1

def test_error_taxonomy_has_fail_closed_categories():
 assert {'DDS_UNAVAILABLE','AMBIGUOUS_VISION','INVALID_POSITION','ENGINE_OUTPUT_INVALID'} <= ERRORS
