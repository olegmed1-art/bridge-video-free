#!/usr/bin/env python3
"""Prove concurrent activation overlap is blocked by the database constraint."""

from __future__ import annotations

import os
import threading

import psycopg


DATABASE_URL = os.environ["DATABASE_URL"]


SETUP_SQL = r"""
DO $$
DECLARE
    v_school uuid;
    v_source uuid;
    v_item uuid;
    v_version uuid;
    v_rule uuid;
    v_test uuid;
    v_type text;
BEGIN
    SELECT school_id INTO v_school
      FROM public.school
     WHERE stable_name='Школа спортивного бриджа';

    INSERT INTO public.source(
        school_id,source_type,title,canonical_locator,trust_class,status
    ) VALUES (
        v_school,'document','CI activation concurrency source',
        'ci://bidding-activation-concurrency','director_approved','active'
    ) RETURNING source_id INTO v_source;

    INSERT INTO public.knowledge_item(
        school_id,stable_key,knowledge_type,title,status
    ) VALUES (
        v_school,'ci-bidding-activation-concurrency','bidding_rule',
        'CI bidding activation concurrency','active'
    ) RETURNING knowledge_item_id INTO v_item;

    INSERT INTO public.knowledge_version(
        knowledge_item_id,version_no,content,authority_class,review_status,
        bidding_system_key,agreement_scope,level_scope,method_version,provenance,status
    ) VALUES (
        v_item,1,'{"fixture":"concurrency"}'::jsonb,'school_canon','reviewed',
        'ci-concurrency','{}'::jsonb,'{}'::jsonb,'ci-concurrency-v1',
        '{"class":"DIRECT"}'::jsonb,'candidate'
    ) RETURNING knowledge_version_id INTO v_version;

    INSERT INTO public.knowledge_version_source(
        knowledge_version_id,source_id,relation_type,source_locator
    ) VALUES (
        v_version,v_source,'derived_from','{"fixture":"concurrency"}'::jsonb
    );

    INSERT INTO bidding.rule(
        school_id,knowledge_version_id,rule_key,rule_kind,auction_pattern,
        hand_constraints,public_context_constraints,action,lifecycle_status,method_version
    ) VALUES (
        v_school,v_version,'ci.activation.concurrent','bid','{"calls":[]}'::jsonb,
        '{}'::jsonb,'{}'::jsonb,'{"call":"P"}'::jsonb,'validated','ci-concurrency-v1'
    ) RETURNING rule_id INTO v_rule;

    FOREACH v_type IN ARRAY ARRAY['positive','negative','boundary','hidden_information'] LOOP
        INSERT INTO bidding.rule_test(
            school_id,rule_id,test_key,test_type,fixture,expected,method_version
        ) VALUES (
            v_school,v_rule,v_type,v_type,'{}'::jsonb,'{}'::jsonb,'ci-concurrency-v1'
        ) RETURNING rule_test_id INTO v_test;

        INSERT INTO bidding.rule_test_run(
            school_id,rule_test_id,result,method_version
        ) VALUES (
            v_school,v_test,'pass','ci-concurrency-v1'
        );
    END LOOP;

    INSERT INTO public.canon_activation(
        knowledge_version_id,scope_key,valid_from,approval_provenance,status
    ) VALUES (
        v_version,'ci-concurrency',now(),'{"fixture":"concurrency"}'::jsonb,'active'
    );
END $$;
"""


INSERT_SQL = """
INSERT INTO bidding.runtime_activation(
    school_id,rule_id,authority_lane,canon_activation_id,scope_key,
    valid_from,valid_to,status,activation_provenance
)
SELECT
    r.school_id,r.rule_id,'school_canon',ca.canon_activation_id,'ci-concurrency',
    now() + %s::interval,now() + %s::interval,'active',%s::jsonb
FROM bidding.rule AS r
JOIN public.canon_activation AS ca
  ON ca.knowledge_version_id=r.knowledge_version_id
WHERE r.rule_key='ci.activation.concurrent'
  AND ca.scope_key='ci-concurrency';
"""


def main() -> None:
    with psycopg.connect(DATABASE_URL) as setup_conn:
        setup_conn.execute(SETUP_SQL)

    first = psycopg.connect(DATABASE_URL)
    second_started = threading.Event()
    second_finished = threading.Event()
    second_sqlstate: list[str | None] = []

    try:
        first.execute(INSERT_SQL, ("1 hour", "3 hours", '{"runner":"first"}'))

        def insert_overlap() -> None:
            with psycopg.connect(DATABASE_URL) as second:
                second_started.set()
                try:
                    second.execute(
                        INSERT_SQL,
                        ("2 hours", "4 hours", '{"runner":"second"}'),
                    )
                    second.commit()
                    second_sqlstate.append(None)
                except psycopg.Error as exc:
                    second.rollback()
                    second_sqlstate.append(exc.sqlstate)
                finally:
                    second_finished.set()

        thread = threading.Thread(target=insert_overlap, daemon=True)
        thread.start()
        if not second_started.wait(timeout=5):
            raise AssertionError("Second transaction did not start")
        if second_finished.wait(timeout=1):
            raise AssertionError(
                "Overlapping activation did not wait on the uncommitted exclusion key"
            )

        first.commit()
        thread.join(timeout=10)
        if thread.is_alive():
            raise AssertionError("Second transaction did not finish after first commit")
        if second_sqlstate != ["23P01"]:
            raise AssertionError(
                f"Expected exclusion_violation 23P01, got {second_sqlstate!r}"
            )

        with psycopg.connect(DATABASE_URL) as verify:
            count = verify.execute(
                """
                SELECT count(*)
                  FROM bidding.runtime_activation AS ra
                  JOIN bidding.rule AS r ON r.rule_id=ra.rule_id
                 WHERE r.rule_key='ci.activation.concurrent'
                   AND ra.status='active'
                """
            ).fetchone()[0]
            if count != 1:
                raise AssertionError(f"Expected one committed activation, got {count}")

            verify.execute(
                """
                UPDATE bidding.runtime_activation AS ra
                   SET status='revoked'
                  FROM bidding.rule AS r
                 WHERE r.rule_id=ra.rule_id
                   AND r.rule_key='ci.activation.concurrent'
                   AND ra.status='active'
                """
            )

        scheduler = psycopg.connect(DATABASE_URL)
        mutation_started = threading.Event()
        mutation_finished = threading.Event()
        mutation_sqlstate: list[str | None] = []

        try:
            scheduler.execute(
                INSERT_SQL,
                ("5 hours", "7 hours", '{"runner":"scheduler"}'),
            )

            def mutate_scheduled_rule() -> None:
                with psycopg.connect(DATABASE_URL) as mutator:
                    mutation_started.set()
                    try:
                        mutator.execute(
                            """
                            UPDATE bidding.rule
                               SET priority=priority+1
                             WHERE rule_key='ci.activation.concurrent'
                            """
                        )
                        mutator.commit()
                        mutation_sqlstate.append(None)
                    except psycopg.Error as exc:
                        mutator.rollback()
                        mutation_sqlstate.append(exc.sqlstate)
                    finally:
                        mutation_finished.set()

            mutation_thread = threading.Thread(
                target=mutate_scheduled_rule,
                daemon=True,
            )
            mutation_thread.start()
            if not mutation_started.wait(timeout=5):
                raise AssertionError("Concurrent rule mutation did not start")
            if mutation_finished.wait(timeout=1):
                raise AssertionError(
                    "Rule mutation did not wait on the uncommitted activation lock"
                )

            scheduler.commit()
            mutation_thread.join(timeout=10)
            if mutation_thread.is_alive():
                raise AssertionError(
                    "Rule mutation did not finish after scheduled activation commit"
                )
            if mutation_sqlstate != ["55000"]:
                raise AssertionError(
                    "Expected active-rule immutability SQLSTATE 55000, "
                    f"got {mutation_sqlstate!r}"
                )
        finally:
            scheduler.close()
    finally:
        first.close()


if __name__ == "__main__":
    main()
