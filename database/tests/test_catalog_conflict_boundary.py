"""Ephemeral SQL/catalog integration; synthetic ties, no bridge applicability claim."""
from __future__ import annotations

import os
from functools import partial
from uuid import uuid4

import psycopg

from bridge_school_api.bidding_catalog_reader import read_research_catalog, read_school_catalog
from bridge_school_api.l1_canonical_runtime import RuleEvaluation
from bridge_school_api.l1_canonical_runtime_v2 import resolve_registered_with_world_fallback


FIXTURE = r"""
CREATE TEMP TABLE catalog_boundary_ids(rule_id uuid, lane text);
DO $$
DECLARE s uuid; src uuid; item uuid; version uuid; rule uuid; ca uuid;
        test_id uuid; kind text; label text; lane text;
BEGIN
 SELECT school_id INTO STRICT s FROM public.school WHERE stable_name='Школа спортивного бриджа';
 INSERT INTO public.source(school_id,source_type,title,canonical_locator,trust_class,status)
 VALUES(s,'document','Synthetic catalog boundary fixture','ci://catalog-boundary','director_approved','active')
 RETURNING source_id INTO src;
 FOREACH label IN ARRAY ARRAY['a','b','world'] LOOP
  lane:=CASE WHEN label='world' THEN 'external' ELSE 'school_canon' END;
  INSERT INTO public.knowledge_item(school_id,stable_key,knowledge_type,title,status)
  VALUES(s,'ci-catalog-boundary-'||label,'bidding_rule','Synthetic boundary fixture','active')
  RETURNING knowledge_item_id INTO item;
  INSERT INTO public.knowledge_version(knowledge_item_id,version_no,content,authority_class,
    review_status,bidding_system_key,agreement_scope,level_scope,method_version,provenance,status)
  VALUES(item,1,'{"synthetic":true}',lane,'reviewed','ci-boundary','{}','{}','ci-boundary-v1',
    '{"class":"DIRECT"}','candidate') RETURNING knowledge_version_id INTO version;
  INSERT INTO public.knowledge_version_source(knowledge_version_id,source_id,relation_type,source_locator)
  VALUES(version,src,'derived_from','{"synthetic":true}');
  INSERT INTO bidding.rule(school_id,knowledge_version_id,rule_key,rule_kind,auction_pattern,
    hand_constraints,public_context_constraints,action,priority,specificity,lifecycle_status,method_version)
  VALUES(s,version,'ci.boundary.'||label,'bid','{"calls":[]}','{}','{}',
    jsonb_build_object('call',CASE WHEN label='a' THEN '1C' ELSE '1D' END),100,10,'validated','ci-boundary-v1')
  RETURNING rule_id INTO rule;
  FOREACH kind IN ARRAY ARRAY['positive','negative','boundary','hidden_information'] LOOP
   INSERT INTO bidding.rule_test(school_id,rule_id,test_key,test_type,fixture,expected,method_version)
   VALUES(s,rule,kind,kind,'{"synthetic":true}','{"fixture_only":true}','ci-boundary-v1')
   RETURNING rule_test_id INTO test_id;
   INSERT INTO bidding.rule_test_run(school_id,rule_test_id,result,method_version)
   VALUES(s,test_id,'pass','ci-boundary-v1');
  END LOOP;
  ca:=NULL;
  IF lane='school_canon' THEN
   INSERT INTO public.canon_activation(knowledge_version_id,scope_key,valid_from,approval_provenance,status)
   VALUES(version,'ci-boundary',now(),'{"synthetic_fixture_only":true}','active')
   RETURNING canon_activation_id INTO ca;
  END IF;
  INSERT INTO bidding.runtime_activation(school_id,rule_id,authority_lane,canon_activation_id,scope_key,status)
  VALUES(s,rule,CASE WHEN lane='external' THEN 'world_external' ELSE lane END,ca,'ci-boundary','active');
  INSERT INTO catalog_boundary_ids VALUES(rule,lane);
 END LOOP;
END $$;
"""


class RecordingCursor:
    def __init__(self, cursor, queries):
        self.cursor, self.queries = cursor, queries

    def __enter__(self):
        self.cursor.__enter__()
        return self

    def __exit__(self, *args):
        return self.cursor.__exit__(*args)

    def execute(self, sql, params):
        self.queries.append(sql)
        return self.cursor.execute(sql, params)

    def fetchall(self):
        return self.cursor.fetchall()


class RecordingConnection:
    def __init__(self, connection):
        self.connection = connection
        self.queries = []

    def cursor(self, **kwargs):
        return RecordingCursor(self.connection.cursor(**kwargs), self.queries)


def main():
    connection = psycopg.connect(os.environ["DATABASE_URL"])
    try:
        # Even a successful run rolls back all sources/rules/activations.
        connection.execute(FIXTURE)
        school = connection.execute("SELECT school_id FROM public.school WHERE stable_name=%s",
                                    ('Школа спортивного бриджа',)).fetchone()[0]
        active = read_school_catalog(connection, school, "ci-boundary")
        expected = {r[0] for r in connection.execute(
            "SELECT rule_id FROM catalog_boundary_ids WHERE lane='school_canon'").fetchall()}
        assert len(active) == 2 and {r['rule_id'] for r in active} == expected
        for row in active:
            assert row['school_id'] == school and row['scope_key'] == 'ci-boundary'
            proof = connection.execute("""SELECT ra.authority_lane,r.lifecycle_status,kv.authority_class,
                ca.status,ra.valid_from<=now() AND (ra.valid_to IS NULL OR ra.valid_to>now())
                FROM bidding.runtime_activation ra JOIN bidding.rule r USING(rule_id)
                JOIN public.knowledge_version kv USING(knowledge_version_id)
                JOIN public.canon_activation ca ON ca.canon_activation_id=ra.canon_activation_id
                WHERE ra.runtime_activation_id=%s""", (row['runtime_activation_id'],)).fetchone()
            assert proof == ('school_canon','validated','school_canon','active',True)
        observed = RecordingConnection(connection)
        lookup = partial(read_research_catalog, observed, school, 'ci-boundary')
        # Positive control: the callback really queries the production SQL function.
        research = lookup()
        assert len(research) == 3 and {r['authority_lane'] for r in research} == {'school_canon','world_external'}
        assert len(observed.queries) == 1 and 'bidding.get_research_rule_catalog' in observed.queries[0]
        observed.queries.clear()
        # Inputs are explicitly pre-evaluated synthetic ties, not real deal matches.
        evaluations = [RuleEvaluation(str(r['rule_id']), 'MATCH', r['action'],
            r['priority'], r['specificity'], 0, (str(r['knowledge_version_id']),)) for r in active]
        result = resolve_registered_with_world_fallback(evaluations, lookup)
        assert result.status == 'CANON_CONFLICT' and result.action is None
        assert observed.queries == []
        assert read_school_catalog(connection, school, 'wrong-scope') == []
        assert read_school_catalog(connection, uuid4(), 'ci-boundary') == []
        connection.execute("UPDATE bidding.runtime_activation SET status='revoked' WHERE rule_id=%s",
                           (active[0]['rule_id'],))
        assert {r['rule_id'] for r in read_school_catalog(connection, school, 'ci-boundary')} == {active[1]['rule_id']}
        result = resolve_registered_with_world_fallback([], lookup)
        assert result.status == 'BLOCK' and result.action == 'CANON_CATALOG_UNVERIFIED'
        assert observed.queries == []
        print('CATALOG_CONFLICT_SQL_BOUNDARY_PASS')
    finally:
        connection.rollback()
        connection.close()


if __name__ == '__main__':
    main()
