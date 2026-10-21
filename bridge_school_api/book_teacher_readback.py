"""Fresh existing metadata11 reader + retained genuine receipts; no issuer minting."""
from copy import deepcopy

import psycopg
from psycopg.rows import tuple_row

from bridge_contracts.book_world_events import validate_book_outbox_event
from database.book_world_event_readback import read_book_world_event
from evolutionary_course.book_teacher_delivery_consumer_v2 import _delivery


def read_book_teacher_delivery(query, *, bundle, bound_map, connect,
                               authorization_provider, assert_unchanged):
    """Own a new read-only PG transaction for EACH callback; never cached events.

    connect is the existing trusted application connector, supplied by the host.
    authorization_provider is an existing issuer binding; None is never allowed.
    Its purpose remains BOOK_WORLD_OUTBOX_DELIVERY_ONLY, not user/WORLD access.
    """
    if not callable(connect) or not callable(authorization_provider):
        raise ValueError('BOOK_LIVE_READBACK_INPUTS_REQUIRED')
    retained = bundle['snapshot']
    old = retained['recorded_event']
    expected = {'recorded_event_id': old['event_id'],
                'recorded_changeset_id': old['changeset_id'],
                'publication_hash': bundle['import_receipt']['publication_hash'],
                'source_id': bundle['source_packet']['source']['registry_source_id'],
                'expected_school_id': bundle['expected_school_id']}
    if type(query) is not dict or query != expected:
        raise ValueError('BOOK_DELIVERY_QUERY_SCOPE')
    # The repository returns owned copies; callbacks get additional copies.
    baseline = deepcopy((query, bundle, bound_map))
    with connect() as conn:
        try:
            if conn.info.transaction_status != 0:
                raise ValueError('BOOK_READBACK_IDLE_CONNECTION_REQUIRED')
            if conn.autocommit is not False:
                raise ValueError('BOOK_READBACK_TRANSACTION_REQUIRED')
            conn.read_only = True
            conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            with conn.cursor(row_factory=tuple_row) as cur:
                cur.execute("SET LOCAL statement_timeout='5s'")
                cur.execute("SET LOCAL lock_timeout='1s'")
                recorded = read_book_world_event(cur, school_id=expected['expected_school_id'],
                                                 event_id=expected['recorded_event_id'])
                published = read_book_world_event(cur, school_id=expected['expected_school_id'],
                                                  event_id=retained['published_event']['event_id'])
        finally:
            try:
                conn.rollback()
            finally:
                conn.close()
    if recorded != old or published != retained['published_event']:
        raise ValueError('BOOK_EVENT_RECEIPT_BINDING_CHANGED')
    auth_query = {'recorded_event': deepcopy(recorded)}
    before_auth_query = deepcopy(auth_query)
    authorization = deepcopy(authorization_provider(auth_query))
    assert_unchanged()
    if auth_query != before_auth_query or (query, bundle, bound_map) != baseline:
        raise ValueError('BOOK_READBACK_INPUT_MUTATED')
    validate_book_outbox_event(recorded, authorization=authorization)
    validate_book_outbox_event(published, authorization=authorization, recorded_event=recorded)
    snapshot = deepcopy(retained)
    snapshot['recorded_event'] = recorded
    snapshot['published_event'] = published
    _delivery(snapshot, bound_map)
    assert_unchanged()
    return snapshot
