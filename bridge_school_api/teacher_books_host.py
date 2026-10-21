"""Trusted local host/session binding to the unchanged common Teacher entry.

No HTTP/session envelope, parallel Teacher, issuer, ambient credential lookup,
WORLD publisher or working-DB write. Runtime dependencies remain explicit.
"""
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from bridge_contracts.book_world import render_teacher_book
from evolutionary_course.book_teacher_consumer import build_card_map
from evolutionary_course.book_teacher_delivery_consumer_v2 import bind_import_receipt_v2, consume_card_v2
from .book_teacher_receipts import BookReceiptBinding, BookReceiptRepository
from .book_teacher_readback import read_book_teacher_delivery
from .teacher_entrypoint import answer_teacher, _withheld


@dataclass(frozen=True)
class BookTeacherSession:
    """Dependencies selected by an existing trusted host, never request JSON.

    The immutable binding comes from trusted receipt ingestion; it is not user
    authorization. A production caller/session locator remains a runtime input.
    """
    repository: BookReceiptRepository | None = None
    binding: BookReceiptBinding | None = None
    connect: Any = None
    authorization_provider: Any = None
    query_knowledge: Any = None
    read_eligible_skills: Any = None

    def answer(self, request, *, test_only=False):
        if test_only is not True:
            return _withheld('TEST_ONLY_OPT_IN_REQUIRED')
        if type(request) is dict and request.get('task_class') == 'SYMBOLIC_ENTRY_ORDER':
            return answer_teacher(request, read_eligible_skills=self.read_eligible_skills, test_only=True)
        preflight = answer_teacher(request, test_only=True)
        if preflight.get('reason') != 'BOOK_HOST_BINDING_REQUIRED':
            return preflight
        if not isinstance(self.repository, BookReceiptRepository) or type(self.binding) is not BookReceiptBinding:
            return _withheld('BOOK_RECEIPT_BINDING_REQUIRED', missing=('repository', 'binding'))
        if not callable(self.authorization_provider):
            return _withheld('BOOK_DELIVERY_AUTHORIZATION_PROVIDER_REQUIRED', missing=('authorization_provider',))
        if not callable(self.connect) or not callable(self.query_knowledge):
            return _withheld('BOOK_EXISTING_READERS_REQUIRED', missing=('connect', 'query_knowledge'))
        try:
            before_request = deepcopy(request)
            bundle = self.repository.load(self.binding)
            before_bundle = deepcopy(bundle)
            bound = bind_import_receipt_v2(build_card_map(bundle['source_packet']),
                bundle['import_receipt'], bundle['snapshot']['recorded_event'],
                expected_school_id=bundle['expected_school_id'], test_only=True)
            before_map = deepcopy(bound)

            def unchanged():
                if request != before_request or bundle != before_bundle or bound != before_map:
                    raise ValueError('BOOK_HOST_INPUT_MUTATED')
                # A replaced/corrupt local bundle cannot keep serving a cached
                # explanation. No path creation or write occurs in this load.
                if self.repository.load(self.binding) != before_bundle:
                    raise ValueError('BOOK_RECEIPT_BINDING_CHANGED')

            def read_delivery(query):
                return read_book_teacher_delivery(query, bundle=bundle, bound_map=bound,
                    connect=self.connect, authorization_provider=self.authorization_provider,
                    assert_unchanged=unchanged)

            def consume(card_map, label, context, *, query_knowledge, render_teacher_book, authority_lane):
                unchanged()
                return consume_card_v2(card_map, label, context, query_knowledge=query_knowledge,
                    render_teacher_book=render_teacher_book, read_delivery=read_delivery,
                    authority_lane=authority_lane, test_only=True)

            result = answer_teacher(request, book_card_map=bound,
                query_book_knowledge=self.query_knowledge, render_book=render_teacher_book,
                consume_book=consume, test_only=True)
            unchanged()
            return {**result, 'delivery_authorization_purpose': 'BOOK_WORLD_OUTBOX_DELIVERY_ONLY',
                    'teacher_user_authorization_verified': False,
                    'runtime_identity_admitted': False, 'production_ready': False}
        except Exception:
            return _withheld('BOOK_HOST_BINDING_UNAVAILABLE')
