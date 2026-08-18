from lesson_materials_meta_adapter import *

def run():
    assert MaterialClaim('Открытие 1 мажор','CANON','course-notes').validate()
    try:
        MaterialClaim('Новое правило','CANON',None).validate(); raise AssertionError('unsupported canon accepted')
    except ValueError: pass
    assert terminology_ok('Продолжение торговли')
    assert not terminology_ok('Фактический аукцион')
    assert material_change_decision(technical_only=True)=='R1_CANDIDATE_ELIGIBLE'
    assert material_change_decision(bidding_system_change=True)=='OWNER_REVIEW_R4'
    assert material_change_decision(methodology_change=True)=='OWNER_REVIEW_R4'
    assert material_change_decision(source_missing=True)=='ASK_OWNER_OR_MARK_UNKNOWN'
    assert material_change_decision(source_conflict=True)=='CONFLICTED_RETEST'
    assert material_change_decision(shared_template_change=True)=='ESCALATE_R2'
    assert A1_CANON_WRITE_ALLOWED is False
    assert A1_SOURCE_DELETE_ALLOWED is False
    assert A1_TEMPLATE_WRITE_ALLOWED is False
    print('LESSON_MATERIALS_META_A1_REGRESSION_PASS')

if __name__=='__main__': run()
