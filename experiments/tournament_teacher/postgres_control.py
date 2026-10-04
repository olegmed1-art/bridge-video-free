"""Positive/negative SQL gate controls, unrelated to tournament canon content."""
from psycopg.types.json import Jsonb
from .postgres_rehearsal import scalar, rejects


def activation_control(conn, school, unused_source):
    source = scalar(conn,"INSERT INTO public.source(school_id,source_type,title,status) VALUES (%s,'synthetic_rehearsal','Gate control only','active') RETURNING source_id",(school,))
    item = scalar(conn,"INSERT INTO public.knowledge_item(school_id,stable_key,knowledge_type,title) VALUES (%s,'SYNTHETIC-GATE-CONTROL','bidding_rule','Synthetic control, never a school rule') RETURNING knowledge_item_id",(school,))
    version = scalar(conn,"""INSERT INTO public.knowledge_version
        (knowledge_item_id,version_no,content,authority_class,review_status,status)
        VALUES (%s,1,'{"test_only":true,"condition":"public_count equals 1"}','school_canon','unreviewed','candidate')
        RETURNING knowledge_version_id""",(item,))
    conn.execute("INSERT INTO public.knowledge_version_source(knowledge_version_id,source_id) VALUES (%s,%s)",(version,source))
    rule = scalar(conn,"""INSERT INTO bidding.rule(school_id,knowledge_version_id,rule_key,rule_kind,action,lifecycle_status)
        VALUES (%s,%s,'SYNTHETIC-GATE-CONTROL','bid','{"call":"PASS","test_only":true}','validated') RETURNING rule_id""",(school,version))
    gate = lambda: scalar(conn,"SELECT bidding.rule_passes_activation_gates(%s)",(rule,))
    assert gate() is False
    conn.execute("UPDATE public.knowledge_version SET review_status='reviewed' WHERE knowledge_version_id=%s",(version,))
    assert gate() is False  # Missing executed tests, even with reviewed/validated labels.
    tests = []
    for kind,count,expected in [('positive',1,True),('negative',2,False),('boundary',0,False),('hidden_information',1,False)]:
        fixture = dict(public_count=count)
        if kind == 'hidden_information':
            fixture['partner_hand'] = ['SA']
        observed = fixture['public_count']==1 and 'partner_hand' not in fixture
        assert observed == expected
        test = scalar(conn,"""INSERT INTO bidding.rule_test(school_id,rule_id,test_key,test_type,fixture,expected)
            VALUES (%s,%s,%s,%s,%s,%s) RETURNING rule_test_id""",(school,rule,kind,kind,Jsonb(fixture),Jsonb({'match':expected})))
        conn.execute("INSERT INTO bidding.rule_test_run(school_id,rule_test_id,result,result_details,method_version) VALUES (%s,%s,'pass',%s,'synthetic-count-control-v1')",
                     (school,test,Jsonb({'observed':observed,'expected':expected})))
        tests.append(test)
    assert gate() is True
    conn.execute("UPDATE public.source SET status='inactive' WHERE source_id=%s",(source,))
    assert gate() is False
    conn.execute("UPDATE public.source SET status='active' WHERE source_id=%s",(source,))
    conn.execute("INSERT INTO bidding.rule_test_run(school_id,rule_test_id,result,method_version) VALUES (%s,%s,'fail','synthetic-failure-injection')",(school,tests[0]))
    assert gate() is False
    observed = 1 == 1  # Re-execute the synthetic positive predicate after failure injection.
    conn.execute("INSERT INTO bidding.rule_test_run(school_id,rule_test_id,result,result_details,method_version) VALUES (%s,%s,'pass',%s,'synthetic-count-control-recheck')",(school,tests[0],Jsonb({'observed':observed,'expected':True})))
    assert gate() is True
    with rejects(conn,'BID_ACTIVATION_CANON_LANE_MISMATCH'):
        conn.execute("INSERT INTO bidding.runtime_activation(school_id,rule_id,authority_lane,scope_key,status) VALUES (%s,%s,'world_external','control-only','active')",(school,rule))
    canon = scalar(conn,"""INSERT INTO public.canon_activation(knowledge_version_id,scope_key,valid_from,status,approval_provenance)
        VALUES (%s,'control-only',now(),'active','{"test_only":true,"approval":"synthetic gate-control fixture"}') RETURNING canon_activation_id""",(version,))
    with rejects(conn,'BID_ACTIVATION_CANON_APPROVAL_REQUIRED'):
        conn.execute("INSERT INTO bidding.runtime_activation(school_id,rule_id,authority_lane,canon_activation_id,scope_key,status) VALUES (%s,%s,'school_canon',%s,'wrong-scope','active')",(school,rule,canon))
    runtime = scalar(conn,"""INSERT INTO bidding.runtime_activation(school_id,rule_id,authority_lane,canon_activation_id,scope_key,status,activation_provenance)
        VALUES (%s,%s,'school_canon',%s,'control-only','active','{"test_only":true}') RETURNING runtime_activation_id""",(school,rule,canon))
    assert scalar(conn,"SELECT count(*) FROM bidding.active_school_canon_rule_v WHERE rule_id=%s",(rule,)) == 1
    with rejects(conn,'BID_ACTIVE_RULE_IMMUTABLE'):
        conn.execute("UPDATE bidding.rule SET priority=999 WHERE rule_id=%s",(rule,))
    with conn.transaction():
        conn.execute("UPDATE bidding.runtime_activation SET status='revoked',valid_to=clock_timestamp() WHERE runtime_activation_id=%s",(runtime,))
        conn.execute("UPDATE public.canon_activation SET status='revoked',valid_to=clock_timestamp() WHERE canon_activation_id=%s",(canon,))
    assert scalar(conn,"SELECT count(*) FROM bidding.active_school_canon_rule_v WHERE rule_id=%s",(rule,)) == 0
    assert scalar(conn,"SELECT status FROM bidding.runtime_activation WHERE runtime_activation_id=%s",(runtime,)) == 'revoked'
    assert scalar(conn,"SELECT status FROM public.canon_activation WHERE canon_activation_id=%s",(canon,)) == 'revoked'
    assert scalar(conn,"SELECT count(*) FROM bidding.rule_test_run WHERE school_id=%s",(school,)) == 6
    return dict(test_only=True,active_before_rollback=1,active_after_rollback=0,
                retained_activation_rows=2,retained_test_runs=6)
