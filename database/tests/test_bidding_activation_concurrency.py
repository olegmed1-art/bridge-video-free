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
    v_item_b uuid;
    v_version_b uuid;
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

    INSERT INTO public.knowledge_item(
        school_id,stable_key,knowledge_type,title,status
    ) VALUES (
        v_school,'ci-bidding-inactive-reassignment-target','bidding_rule',
        'CI inactive reassignment target','active'
    ) RETURNING knowledge_item_id INTO v_item_b;

    INSERT INTO public.knowledge_version(
        knowledge_item_id,version_no,content,authority_class,review_status,
        bidding_system_key,agreement_scope,level_scope,method_version,provenance,status
    ) VALUES (
        v_item_b,1,'{"fixture":"inactive-target"}'::jsonb,'school_canon','reviewed',
        'ci-concurrency','{}'::jsonb,'{}'::jsonb,'ci-concurrency-v1',
        '{"class":"DIRECT"}'::jsonb,'candidate'
    ) RETURNING knowledge_version_id INTO v_version_b;

    INSERT INTO public.knowledge_version_source(
        knowledge_version_id,source_id,relation_type,source_locator
    ) VALUES (
        v_version_b,v_source,'derived_from','{"fixture":"inactive-target"}'::jsonb
    );

    INSERT INTO bidding.rule(
        school_id,knowledge_version_id,rule_key,rule_kind,auction_pattern,
        hand_constraints,public_context_constraints,action,lifecycle_status,method_version
    ) VALUES (
        v_school,v_version_b,'ci.activation.inactive-target','bid','{"calls":[]}'::jsonb,
        '{}'::jsonb,'{}'::jsonb,'{"call":"P"}'::jsonb,'validated','ci-concurrency-v1'
    );

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


def assert_evidence_waits_then_fails(
    label: str,
    start_offset: str,
    end_offset: str,
    mutation_sql: str,
) -> None:
    scheduler = psycopg.connect(DATABASE_URL)
    mutation_started = threading.Event()
    mutation_finished = threading.Event()
    mutation_sqlstate: list[str | None] = []

    try:
        scheduler.execute(
            INSERT_SQL,
            (start_offset, end_offset, f'{{"runner":"{label}"}}'),
        )

        def insert_evidence() -> None:
            with psycopg.connect(DATABASE_URL) as mutator:
                mutation_started.set()
                try:
                    mutator.execute(mutation_sql)
                    mutator.commit()
                    mutation_sqlstate.append(None)
                except psycopg.Error as exc:
                    mutator.rollback()
                    mutation_sqlstate.append(exc.sqlstate)
                finally:
                    mutation_finished.set()

        mutation_thread = threading.Thread(target=insert_evidence, daemon=True)
        mutation_thread.start()
        if not mutation_started.wait(timeout=5):
            raise AssertionError(f"{label} mutation did not start")
        if mutation_finished.wait(timeout=1):
            raise AssertionError(
                f"{label} mutation did not wait on the uncommitted activation lock"
            )

        scheduler.commit()
        mutation_thread.join(timeout=10)
        if mutation_thread.is_alive():
            raise AssertionError(
                f"{label} mutation did not finish after activation commit"
            )
        if mutation_sqlstate != ["55000"]:
            raise AssertionError(
                f"Expected {label} immutability SQLSTATE 55000, "
                f"got {mutation_sqlstate!r}"
            )
    finally:
        scheduler.close()

    with psycopg.connect(DATABASE_URL) as reset:
        reset.execute(
            """
            UPDATE bidding.runtime_activation AS ra
               SET status='revoked'
              FROM bidding.rule AS r
             WHERE r.rule_id=ra.rule_id
               AND r.rule_key='ci.activation.concurrent'
               AND ra.status='active'
            """
        )


def assert_repeatable_read_activation_fails() -> None:
    with psycopg.connect(DATABASE_URL) as isolated:
        isolated.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        isolated.execute("SELECT 1")  # Establish the transaction snapshot.
        try:
            isolated.execute(
                INSERT_SQL,
                (
                    "21 hours",
                    "23 hours",
                    '{"runner":"repeatable-read"}',
                ),
            )
        except psycopg.Error as exc:
            isolated.rollback()
            if exc.sqlstate != "55000":
                raise AssertionError(
                    "Expected REPEATABLE READ activation SQLSTATE 55000, "
                    f"got {exc.sqlstate!r}"
                ) from exc
        else:
            isolated.rollback()
            raise AssertionError("REPEATABLE READ activation was not rejected")


def assert_repeatable_read_mutation_fails(label: str, mutation_sql: str) -> None:
    with psycopg.connect(DATABASE_URL) as isolated:
        isolated.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        isolated.execute("SELECT 1")  # Establish the transaction snapshot.
        try:
            isolated.execute(mutation_sql)
        except psycopg.Error as exc:
            isolated.rollback()
            if exc.sqlstate != "55000":
                raise AssertionError(
                    f"Expected REPEATABLE READ {label} SQLSTATE 55000, "
                    f"got {exc.sqlstate!r}"
                ) from exc
        else:
            isolated.rollback()
            raise AssertionError(
                f"REPEATABLE READ {label} mutation was not rejected"
            )


def main() -> None:
    with psycopg.connect(DATABASE_URL) as setup_conn:
        setup_conn.execute(SETUP_SQL)

    first = psycopg.connect(DATABASE_URL)
    second_started = threading.Event()
    second_finished = threading.Event()
    second_sqlstate: list[str | None] = []
    second_message: list[str] = []

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
                    second_message.append("")
                except psycopg.Error as exc:
                    second.rollback()
                    second_sqlstate.append(exc.sqlstate)
                    second_message.append(exc.diag.message_primary or "")
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
        overlap_blocked = (
            second_sqlstate == ["23P01"]
            or (
                second_sqlstate == ["23514"]
                and second_message == ["BID_RUNTIME_ACTIVATION_OVERLAP"]
            )
        )
        if not overlap_blocked:
            raise AssertionError(
                "Expected exclusion_violation 23P01 or the serialized "
                "BID_RUNTIME_ACTIVATION_OVERLAP guard, got "
                f"{list(zip(second_sqlstate, second_message))!r}"
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

        with psycopg.connect(DATABASE_URL) as reset:
            reset.execute(
                """
                UPDATE bidding.runtime_activation AS ra
                   SET status='revoked'
                  FROM bidding.rule AS r
                 WHERE r.rule_id=ra.rule_id
                   AND r.rule_key='ci.activation.concurrent'
                   AND ra.status='active'
                """
            )

        dependency_scheduler = psycopg.connect(DATABASE_URL)
        reassignment_started = threading.Event()
        reassignment_finished = threading.Event()
        reassignment_sqlstate: list[str | None] = []

        try:
            dependency_scheduler.execute(
                INSERT_SQL,
                ("9 hours", "11 hours", '{"runner":"dependency-scheduler"}'),
            )

            def reassign_active_test() -> None:
                with psycopg.connect(DATABASE_URL) as mutator:
                    reassignment_started.set()
                    try:
                        mutator.execute(
                            """
                            UPDATE bidding.rule_test AS t
                               SET rule_id=target.rule_id
                              FROM bidding.rule AS source
                              JOIN bidding.rule AS target
                                ON target.rule_key='ci.activation.inactive-target'
                             WHERE t.rule_id=source.rule_id
                               AND source.rule_key='ci.activation.concurrent'
                               AND t.test_key='positive'
                            """
                        )
                        mutator.commit()
                        reassignment_sqlstate.append(None)
                    except psycopg.Error as exc:
                        mutator.rollback()
                        reassignment_sqlstate.append(exc.sqlstate)
                    finally:
                        reassignment_finished.set()

            reassignment_thread = threading.Thread(
                target=reassign_active_test,
                daemon=True,
            )
            reassignment_thread.start()
            if not reassignment_started.wait(timeout=5):
                raise AssertionError("Concurrent test reassignment did not start")
            if reassignment_finished.wait(timeout=1):
                raise AssertionError(
                    "Test reassignment did not wait on the old owner's activation lock"
                )

            dependency_scheduler.commit()
            reassignment_thread.join(timeout=10)
            if reassignment_thread.is_alive():
                raise AssertionError(
                    "Test reassignment did not finish after activation commit"
                )
            if reassignment_sqlstate != ["55000"]:
                raise AssertionError(
                    "Expected old-owner immutability SQLSTATE 55000, "
                    f"got {reassignment_sqlstate!r}"
                )
        finally:
            dependency_scheduler.close()

        with psycopg.connect(DATABASE_URL) as reset:
            reset.execute(
                """
                UPDATE bidding.runtime_activation AS ra
                   SET status='revoked'
                  FROM bidding.rule AS r
                 WHERE r.rule_id=ra.rule_id
                   AND r.rule_key='ci.activation.concurrent'
                   AND ra.status='active'
                """
            )

        assert_evidence_waits_then_fails(
            "late-test-run",
            "13 hours",
            "15 hours",
            """
            INSERT INTO bidding.rule_test_run(
                school_id,rule_test_id,result,result_details,method_version
            )
            SELECT r.school_id,t.rule_test_id,'fail','{"late":true}'::jsonb,
                   'ci-concurrency-v1'
              FROM bidding.rule AS r
              JOIN bidding.rule_test AS t ON t.rule_id=r.rule_id
             WHERE r.rule_key='ci.activation.concurrent'
               AND t.test_key='positive'
            """,
        )

        assert_evidence_waits_then_fails(
            "open-conflict",
            "17 hours",
            "19 hours",
            """
            INSERT INTO bidding.rule_conflict(
                school_id,left_rule_id,right_rule_id,conflict_type,status
            )
            SELECT source.school_id,source.rule_id,target.rule_id,
                   'contradiction','open'
              FROM bidding.rule AS source
              JOIN bidding.rule AS target
                ON target.rule_key='ci.activation.inactive-target'
             WHERE source.rule_key='ci.activation.concurrent'
            """,
        )
        assert_repeatable_read_activation_fails()
        assert_repeatable_read_mutation_fails(
            "test-run evidence",
            """
            INSERT INTO bidding.rule_test_run(
                school_id,rule_test_id,result,result_details,method_version
            )
            SELECT r.school_id,t.rule_test_id,'fail','{"rr":true}'::jsonb,
                   'ci-concurrency-v1'
              FROM bidding.rule AS r
              JOIN bidding.rule_test AS t ON t.rule_id=r.rule_id
             WHERE r.rule_key='ci.activation.concurrent'
               AND t.test_key='positive'
            """,
        )
        assert_repeatable_read_mutation_fails(
            "open-conflict evidence",
            """
            INSERT INTO bidding.rule_conflict(
                school_id,left_rule_id,right_rule_id,conflict_type,status
            )
            SELECT source.school_id,source.rule_id,target.rule_id,
                   'contradiction','open'
              FROM bidding.rule AS source
              JOIN bidding.rule AS target
                ON target.rule_key='ci.activation.inactive-target'
             WHERE source.rule_key='ci.activation.concurrent'
            """,
        )
    finally:
        first.close()


if __name__ == "__main__":
    main()
