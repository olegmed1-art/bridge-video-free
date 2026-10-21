"""Explicit test-only Teacher routing; no production route or authority admission.

Compose the existing L1 controller and Books consumer without fallback between
them. Maps/readers/renderers are trusted host dependencies, never request JSON.
Positive Books results explain reviewed WORLD principles, never select a bid or
card, create mastery evidence, import knowledge or activate school canon.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

REQUEST_SCHEMA = 'teacher-request-v04-test-v1'
ANSWER_SCHEMA = 'teacher-answer-v04-test-v1'
VERSION = 'teacher-entrypoint-books-local-test-v1'
WORLD = 'WORLD_EXTERNAL'


def _withheld(reason: str, *, scope='TEACHER_ROUTING_TEST_ONLY', missing=()):
    return {'schema': ANSWER_SCHEMA, 'controller_version': VERSION,
            'router_version': VERSION, 'scope': scope, 'test_only': True,
            'production_ready': False, 'status': 'ABSTAIN', 'reason': reason,
            'missing_context': list(missing), 'answer': None, 'decision': None,
            'proposal': None, 'binding': None, 'knowledge_versions': [], 'trace': []}


def answer_teacher(request: Any, *, read_eligible_skills=None, book_card_map=None,
                   query_book_knowledge=None, render_book=None, consume_book=None,
                   test_only: bool = False) -> dict[str, Any]:
    """One local entry seam, with explicit task/lane and a fresh Books read.

    This additive module does not register an API, replace the protected Books
    route or establish an authenticated provider. No request flag can make this
    a production reader. FULL_CALL and all unsupported tasks fail before IO.
    """
    if test_only is not True:
        return _withheld('TEST_ONLY_OPT_IN_REQUIRED')
    if type(request) is not dict or request.get('schema') != REQUEST_SCHEMA:
        return _withheld('TEACHER_REQUEST_SCHEMA')
    task = request.get('task_class')
    if task == 'SYMBOLIC_ENTRY_ORDER':
        if set(request) != {'schema', 'task_class', 'observation'}:
            return _withheld('TEACHER_REQUEST_FIELDS')
        try:
            from .teacher_v04_integration import answer_entry_order
        except ImportError:
            return _withheld('L1_CONTROLLER_UNAVAILABLE')
        result = answer_entry_order(request['observation'], read_eligible_skills,
                                    test_only=True)
        return {**result, 'router_version': VERSION}
    if task != 'BOOK_PRINCIPLE_EXPLANATION':
        return _withheld('UNSUPPORTED_TEACHER_TASK')
    if set(request) != {'schema', 'task_class', 'authority_lane', 'system_profile',
                        'card_label', 'context'}:
        return _withheld('TEACHER_REQUEST_FIELDS')
    if request['authority_lane'] != WORLD or request['system_profile'] != 'SYSTEM_NEUTRAL':
        return _withheld('EXPLICIT_WORLD_BOOK_SCOPE_REQUIRED')
    context = request['context']
    if type(context) is not dict or set(context) - {
            'topic', 'stage', 'dummy_exposed', 'opening_lead_made', 'asks_for'}:
        return _withheld('BOOK_CONTEXT_FIELDS')
    if type(request['card_label']) is not str or request['card_label'] not in ('E01','E02','E03'):
        return _withheld('BOOK_CARD_SCOPE')
    if type(book_card_map) is not dict:
        return _withheld('BOOK_HOST_BINDING_REQUIRED', missing=('book_card_map',))
    if not callable(query_book_knowledge) or not callable(render_book):
        return _withheld('BOOK_READER_RENDERER_REQUIRED')
    try:
        if consume_book is None:
            from evolutionary_course.book_teacher_consumer import consume_card
        elif callable(consume_book):
            consume_card = consume_book
        else:
            return _withheld('BOOK_CONSUMER_REQUIRED', scope='BOOK_PRINCIPLE_TEST_ONLY')
        before_request, before_map = deepcopy(request), deepcopy(book_card_map)
        initial = consume_card(book_card_map, request['card_label'], context,
                               query_knowledge=query_book_knowledge,
                               render_teacher_book=render_book, authority_lane=WORLD)
        if request != before_request or book_card_map != before_map:
            return _withheld('BOOK_CONTEXT_OR_BINDING_CHANGED')
        if initial.get('status') != 'EXPLANATION_READY':
            return _withheld(initial['reason'], scope='BOOK_PRINCIPLE_TEST_ONLY')
        fresh = consume_card(book_card_map, request['card_label'], context,
                             query_knowledge=query_book_knowledge,
                             render_teacher_book=render_book, authority_lane=WORLD)
        if request != before_request or book_card_map != before_map:
            return _withheld('BOOK_CONTEXT_OR_BINDING_CHANGED')
        if fresh.get('status') != 'EXPLANATION_READY':
            return _withheld(fresh['reason'], scope='BOOK_PRINCIPLE_TEST_ONLY')
        if fresh != initial:
            return _withheld('BOOK_FRESH_READ_CHANGED')
    except Exception:
        # No old explanation/citation or arbitrary service exception escapes.
        return _withheld('BOOK_CONSUMER_UNAVAILABLE', scope='BOOK_PRINCIPLE_TEST_ONLY')

    book = fresh['book_response']
    version = {'authority_lane': WORLD, 'knowledge_version_id': book['knowledge_version_id'],
               'stable_key': book['stable_key'], 'source_review_id': book['source_review_id'],
               'citations': deepcopy(book['citations'])}
    return {'schema': ANSWER_SCHEMA, 'controller_version': VERSION,
            'router_version': VERSION, 'scope': 'BOOK_PRINCIPLE_TEST_ONLY',
            'test_only': True, 'production_ready': False,
            'authority_lane': WORLD, 'system_profile': 'SYSTEM_NEUTRAL',
            'status': 'EXPLANATION_READY', 'reason': 'FRESH_REVIEWED_WORLD_BOOK_EXPLANATION',
            'missing_context': [],
            'answer': {'paragraphs_ru': [fresh['explanation_with_page']],
                       'selection_node': None, 'comparison_node': None},
            'decision': None, 'proposal': None,
            'binding': {'kind': 'BOOK_WORLD_REFETCH_TEST_ONLY',
                        'knowledge_version_id': book['knowledge_version_id'],
                        'stable_key': book['stable_key']},
            'knowledge_versions': [version],
            'trace': [{'node_id': 'book_world', 'authority_lane': WORLD,
                       'knowledge_version_id': book['knowledge_version_id'],
                       'events': deepcopy(fresh['trace'])},
                      {'node_id': 'book_fresh_read', 'status': 'UNCHANGED'}],
            'book_response': deepcopy(book), 'book_skill': deepcopy(fresh['skill']),
            'domain_writes_performed': False, 'canon_activation_performed': False,
            'fallback_performed': False, 'mastery_event_created': False}
