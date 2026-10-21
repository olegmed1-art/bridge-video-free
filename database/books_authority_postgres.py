"""SQL adapter source; requires a trusted connector and target attester.

No schema installation, connection defaults, credential lookup or effects on import.
No production binding is supplied by this module.
"""
from contextlib import contextmanager
from copy import deepcopy
import json

from .books_authority import need, REC, PUB
from .book_world_event_readback import read_book_world_event
from bridge_contracts.book_world import world_uuid


OBJECT_SQL = """
 SELECT jsonb_build_object('kind',o.kind,'body',o.body,
                           'status',s.status,'revision',s.revision)
 FROM public.books_authority_object o
 JOIN public.books_authority_state s USING(object_id)
 WHERE o.object_id=%s
"""

# All authority state and domain metadata are selected in ONE new snapshot.
# This is intentionally a separate transaction from Teacher's closed RR read.
VALIDATION_SQL = """
 WITH v AS (
   SELECT o.body FROM public.books_authority_object o WHERE o.object_id=%s
 ), parents AS (
   SELECT o.body FROM public.books_authority_object o,v
    WHERE o.object_id IN (v.body->'parents'->>'DELIVER_RECORDED_OUTBOX',
                         v.body->'parents'->>'DELIVER_PUBLISHED_OUTBOX')
 ), ids AS (
   SELECT %s::text AS id
   UNION SELECT body->>'principal_id' FROM v
   UNION SELECT body->>'policy_id' FROM v
   UNION SELECT body->'parents'->>'DELIVER_RECORDED_OUTBOX' FROM v
   UNION SELECT body->'parents'->>'DELIVER_PUBLISHED_OUTBOX' FROM v
   UNION SELECT body->>'principal_id' FROM parents
   UNION SELECT body->>'policy_id' FROM parents
   UNION SELECT body->'proof_predicates'->>'verifier_id' FROM parents
   UNION SELECT jsonb_array_elements_text(body->'proof_predicates'->'state_ids') FROM parents
   UNION SELECT p.receipt->>'catalog_proof_id'
         FROM public.books_authority_phase p,v
         WHERE p.phase='ACTIVATE_REVIEWED_WORLD'
           AND p.grant_id=v.body->'parents'->>'DELIVER_PUBLISHED_OUTBOX'
 ), objects AS (
   SELECT jsonb_object_agg(o.object_id,jsonb_build_object(
       'kind',o.kind,'body',o.body,'status',s.status,'revision',s.revision)) AS value
   FROM public.books_authority_object o
   JOIN public.books_authority_state s USING(object_id)
   JOIN ids ON ids.id=o.object_id
 ), phases AS (
   SELECT jsonb_object_agg(p.phase,p.receipt) AS value
   FROM public.books_authority_phase p,v
   WHERE (p.phase='DELIVER_RECORDED_OUTBOX' AND
          p.grant_id=v.body->'parents'->>'DELIVER_RECORDED_OUTBOX')
      OR (p.phase='DELIVER_PUBLISHED_OUTBOX' AND
          p.grant_id=v.body->'parents'->>'DELIVER_PUBLISHED_OUTBOX')
 ), predecessors AS (
   SELECT jsonb_object_agg(
     CASE p.phase WHEN 'IMPORT_STAGED' THEN 'DELIVER_RECORDED_OUTBOX'
                  ELSE 'DELIVER_PUBLISHED_OUTBOX' END, p.receipt) AS value
   FROM public.books_authority_phase p,v
   WHERE (p.phase='IMPORT_STAGED' AND
          p.grant_id=v.body->'parents'->>'DELIVER_RECORDED_OUTBOX')
      OR (p.phase='ACTIVATE_REVIEWED_WORLD' AND
          p.grant_id=v.body->'parents'->>'DELIVER_PUBLISHED_OUTBOX')
 ), events AS (
   SELECT e.event_id,
     jsonb_build_object('event_id',e.event_id::text,'event_type',e.event_type,
       'aggregate_version',e.aggregate_version,'school_id',e.school_id::text,
       'changeset_id',e.changeset_id::text,'partition_key',e.partition_key,
       'payload',e.payload,'payload_hash',e.payload_hash,
       'changeset_status',c.status,'outbox_status',o.status,
       'event_position',e.event_position) AS value
   FROM public.domain_event e
   JOIN public.changeset c ON c.changeset_id=e.changeset_id AND c.school_id=e.school_id
   JOIN public.outbox_message o ON o.event_id=e.event_id AND o.changeset_id=e.changeset_id
   CROSS JOIN v
   WHERE e.school_id=(v.body->'scope'->>'school_id')::uuid
     AND e.event_id IN ((v.body->>'recorded_event_id')::uuid,
                       (v.body->>'published_event_id')::uuid)
 )
 SELECT jsonb_build_object('validation',objects.value->%s,
   'objects',objects.value,'phases',phases.value,'predecessors',predecessors.value,
   'recorded',(SELECT value FROM events WHERE event_id=(v.body->>'recorded_event_id')::uuid),
   'published',(SELECT value FROM events WHERE event_id=(v.body->>'published_event_id')::uuid),
   'now',clock_timestamp()::text)
 FROM v,objects,phases,predecessors
"""


class PostgresStore:
    def __init__(self, *, connect, assert_target, target):
        need(callable(connect) and callable(assert_target) and type(target) is str)
        self.connect, self.assert_target, self.target = connect, assert_target, target

    @contextmanager
    def transaction(self, policy, *, readonly=False):
        policy.check()
        with self.connect() as conn:
            need(conn.info.transaction_status == 0 and conn.autocommit is False)
            # psycopg properties, assigned before BEGIN. Numeric value 2 is READ_COMMITTED.
            from psycopg import IsolationLevel
            conn.isolation_level = IsolationLevel.READ_COMMITTED
            conn.read_only = readonly
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT set_config('statement_timeout',%s,true),"
                                "set_config('lock_timeout',%s,true)",
                                (str(policy.statement_timeout_ms), str(policy.lock_timeout_ms)))
                need(self.assert_target(conn, self.target) is True,
                     "BOOK_AUTHORITY_TARGET_UNATTESTED")
                yield Transaction(conn)

    def begin_attempt(self, attempt, grant, phase, binding, policy):
        need(binding.target == self.target)
        with self.transaction(policy) as tx:
            tx.sql("""INSERT INTO public.books_authority_attempt
                (attempt_id,grant_id,phase,principal,target,school_id,status)
                VALUES (%s,%s,%s,%s,%s,%s,'unresolved')""",
                (attempt, grant, phase, binding.principal, binding.target, binding.school))

    def validation_snapshot(self, validation_id, policy):
        with self.transaction(policy, readonly=True) as tx:
            return tx.one(VALIDATION_SQL, (validation_id, validation_id, validation_id))


class Transaction:
    def __init__(self, connection):
        self.connection = connection

    def sql(self, query, params=()):
        with self.connection.cursor() as cur:
            cur.execute(query, params)
            return cur.rowcount

    def one(self, query, params=()):
        with self.connection.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
            need(len(rows) == 1 and rows[0][0] is not None)
            return rows[0][0]

    def object(self, object_id):
        return self.one(OBJECT_SQL, (object_id,))

    def lock_states(self, ids, *, exclusive):
        result = {}
        for object_id in sorted(set(ids)):
            # Bodies immutable. Every mutable authority row uses this lock protocol.
            result[object_id] = self.one(
                OBJECT_SQL + (" FOR UPDATE OF s" if exclusive else " FOR SHARE OF s"),
                (object_id,))
        return result

    def clock(self):
        return self.one("SELECT clock_timestamp()::text")

    def phase(self, grant, phase):
        with self.connection.cursor() as cur:
            cur.execute("SELECT receipt FROM public.books_authority_phase WHERE grant_id=%s AND phase=%s",
                        (grant, phase))
            rows = cur.fetchall()
            need(len(rows) <= 1)
            return rows[0][0] if rows else None

    def event(self, scope, phase):
        event_id = (world_uuid("event", scope["school_id"], scope["run"]["run_id"])
                    if phase == REC else
                    world_uuid("publish-event", scope["school_id"], scope["recorded_changeset_id"]))
        with self.connection.cursor() as cur:
            return read_book_world_event(cur, school_id=scope["school_id"], event_id=event_id)

    def publish(self, event, authorization, *, before_effect):
        from .outbox_publisher import publish_changeset_outbox_in_transaction
        return publish_changeset_outbox_in_transaction(
            self.connection, event["changeset_id"],
            expected_book_event_id=event["event_id"],
            before_publication=before_effect,
            authorization_provider=lambda query: deepcopy(authorization))

    def persist(self, bundle, review_receipt, identity_receipt, scope, authorization,
                *, before_effect):
        from .book_world_persistence import persist_book_world_in_transaction
        return persist_book_world_in_transaction(
            self.connection, bundle, review_receipt,
            school_id=scope["school_id"], source_id=scope["source_id"],
            identity_receipt=identity_receipt, delivery_authorization=authorization,
            before_effect=before_effect)

    def activate(self, scope, delivery, authorization, *, before_effect):
        from .book_world_persistence import publish_book_world_in_transaction
        return publish_book_world_in_transaction(
            self.connection, school_id=scope["school_id"],
            changeset_id=scope["recorded_changeset_id"], delivery_receipt=delivery,
            delivery_authorization=authorization, before_effect=before_effect)

    def catalog_acceptance(self, proof_id):
        with self.connection.cursor() as cur:
            cur.execute("SELECT body FROM public.books_catalog_acceptance WHERE proof_id=%s",
                        (proof_id,))
            rows = cur.fetchall()
            need(len(rows) <= 1)
            return rows[0][0] if rows else None

    def insert_catalog(self, proof_id, acceptance, body, actor):
        self.sql("INSERT INTO public.books_authority_object VALUES (%s,'catalog',%s::jsonb)",
                 (proof_id, json.dumps(body)))
        self.sql("""INSERT INTO public.books_authority_state
                 (object_id,revision,status,audit_actor) VALUES (%s,0,'active',%s)""",
                 (proof_id, actor))
        self.sql("INSERT INTO public.books_catalog_acceptance VALUES (%s,%s::jsonb)",
                 (proof_id, json.dumps(acceptance)))

    def save_observation(self, observation_id, body):
        self.sql("""INSERT INTO public.books_catalog_observation
                 (observation_id,proof_id,body) VALUES (%s,%s,%s::jsonb)
                 ON CONFLICT(observation_id) DO NOTHING""",
                 (observation_id, body["proof_id"], json.dumps(body)))
        need(self.one("SELECT body FROM public.books_catalog_observation WHERE observation_id=%s",
                      (observation_id,)) == body, "CATALOG_OBSERVATION_CONFLICT")

    def save_phase(self, grant, phase, receipt):
        self.sql("INSERT INTO public.books_authority_phase(grant_id,phase,receipt) VALUES (%s,%s,%s::jsonb)",
                 (grant, phase, json.dumps(receipt)))

    def finish_attempt(self, attempt, status):
        need(self.sql("UPDATE public.books_authority_attempt SET status=%s WHERE attempt_id=%s AND status='unresolved'",
                      (status, attempt)) == 1, "BOOK_AUTHORITY_AUDIT_WRITE_FAILED")

    def revoke(self, grant, revision, actor):
        need(self.sql("""UPDATE public.books_authority_state SET status='revoked',
                    revision=revision+1,audit_actor=%s
                    WHERE object_id=%s AND revision=%s AND status='active'""",
                 (actor, grant, revision)) == 1, "BOOK_AUTHORITY_REVOKE_WRITE_FAILED")
