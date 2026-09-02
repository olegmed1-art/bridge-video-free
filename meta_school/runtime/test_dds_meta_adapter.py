from dds_meta_adapter import *

def event(**kw):
    base=dict(event_id='e1',run_id='r1',component_id='DDS-C05',stable_sha='e7e5616',algorithm_version='dds-learning-v2.3',solver_identity='DDS3-local',deal_id='d1',root_deal_id='d1',task_id='t1',split='train',fold='f0',input_hash='h1',result_type='decision',chosen_card='SA',legal_moves=('SA','SK'),optimal_moves=('SA','SK'),regret=0,qc_status='PASS')
    base.update(kw); return DDSMetaEvent(**base)

def run():
    assert event().validate()
    assert event(chosen_card='SK').validate()
    try: event(chosen_card='HQ').validate(); raise AssertionError('illegal card accepted')
    except ValueError: pass
    try: event(chosen_card='SA',regret=1).validate(); raise AssertionError('optimal regret accepted')
    except ValueError: pass
    try: event(chosen_card='SA',optimal_moves=('SK',),regret=0).validate(); raise AssertionError('zero regret mismatch accepted')
    except ValueError: pass
    assert classify_dds_meta_case(intentional_legacy_block=True)=='NO_CHANGE'
    assert classify_dds_meta_case(canonical_change=True)=='OWNER_REVIEW'
    assert classify_dds_meta_case(stale=True)=='REBASE_REQUIRED'
    assert classify_dds_meta_case(insufficient=True)=='RETEST'
    assert classify_dds_meta_case(dependency_ok=False)=='REJECT'
    assert classify_dds_meta_case(solver_available=False)=='BLOCKED_UNKNOWN'
    assert classify_dds_meta_case()=='SHADOW_CANDIDATE_ELIGIBLE'
    assert A1_STABLE_WRITE_ALLOWED is False
    assert A1_MASS_TRAINING_ALLOWED is False
    print('DDS_META_A1_REGRESSION_PASS')

if __name__=='__main__': run()
