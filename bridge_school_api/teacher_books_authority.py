"""Explicit source-only join to the real Books read issuer; no provisioning.

All arguments are trusted host configuration, never request fields. Constructing
a session is not policy acceptance. The issuer must still find the exact accepted
policy and genuine retained records in the authoritative primary on every call.
No issuer/evidence is synthesized and no I/O occurs during composition.
"""
from contextlib import contextmanager
from psycopg.rows import tuple_row

from database.books_authority import Binding, Denied, Policy, ReadIssuer, PUB, active, need
from database.books_authority_postgres import PostgresStore

from .book_teacher_receipts import BookReceiptBinding, BookReceiptRepository
from .teacher_books_host import BookTeacherSession


class _ReadIssuerStore(PostgresStore):
    """Adapt existing dict-row connector for the Books scalar SQL reader only.

    The attester runs first with original connector semantics. Tuple row mode
    applies only to the fresh issuer transaction body, and is then restored.
    WORLD/host readers and the owner Books source are unchanged.
    This composition supports only the current catalog-ingestion profile;
    generic Books legacy read compatibility is deliberately outside this path.
    """
    @contextmanager
    def transaction(self, policy, *, readonly=False):
        if readonly is not True:
            raise Denied('BOOK_TEACHER_AUTHORITY_READ_ONLY')
        with super().transaction(policy, readonly=True) as tx:
            original = tx.connection.row_factory
            tx.connection.row_factory = tuple_row
            try:
                yield tx
            finally:
                tx.connection.row_factory = original

    def validation_snapshot(self, validation_id, policy):
        snapshot = super().validation_snapshot(validation_id, policy)
        # One fresh SELECT only. Never let the generic issuer's optional legacy
        # branch turn incomplete NEW-profile bindings into delivery authority.
        from bridge_contracts.book_material import digest
        from database.books_catalog import CONTRACT, check_dependencies
        try:
            validation = active(snapshot['validation'], 'validation')
            objects = snapshot['objects']
            grant = active(objects[validation['parents'][PUB]], 'grant')
            descriptor = check_dependencies(grant, objects)  # mandatory, no .get gate
            activation = snapshot['predecessors'][PUB]
            proof = active(objects[activation['catalog_proof_id']], 'catalog')
            evidence = proof['evidence']
            need(evidence['ingestion_contract'] == CONTRACT
                 and evidence['binding_hash'] == digest(descriptor)
                 and evidence['acceptance_hash'] == digest(evidence['acceptance']))
        except (KeyError, TypeError, ValueError, AttributeError):
            raise Denied('BOOK_TEACHER_CATALOG_BINDING_REQUIRED') from None
        return snapshot


def compose_book_teacher_authority(*, repository=None, receipt_binding=None,
                                   connect=None, assert_target=None,
                                   policy=None, authority_binding=None,
                                   validation_id=None, retained=None,
                                   query_knowledge=None, read_eligible_skills=None):
    """Bind existing host to ReadIssuer without a caller-supplied auth callback.

    `retained` is the complete pre-existing issuer retained object, including its
    phase records. It is passed unchanged to ReadIssuer, not projected/filled in.
    The host's independently retained book bundle is still loaded and validated
    by the unchanged host; ReadIssuer checks both against current ledger data.
    Invalid/missing configuration yields the old unbound session before I/O.
    SQL schema, import/activation, catalog ingestion and HTTP wiring are absent.
    """
    unbound = BookTeacherSession(read_eligible_skills=read_eligible_skills)
    if (type(policy) is not Policy or type(authority_binding) is not Binding
            or not isinstance(repository, BookReceiptRepository)
            or type(receipt_binding) is not BookReceiptBinding
            or not all(callable(value) for value in (connect, assert_target, query_knowledge))
            or type(validation_id) is not str or not validation_id.strip()
            or type(retained) is not dict or not retained):
        return unbound
    try:
        policy.check()
        if (not all(type(value) is str and bool(value.strip()) for value in
                    (authority_binding.principal, authority_binding.target, authority_binding.school))
                or authority_binding.school != receipt_binding.school_id):
            return unbound
        store = _ReadIssuerStore(connect=connect, assert_target=assert_target,
                                 target=authority_binding.target)
        issuer = ReadIssuer(store=store, policy=policy, binding=authority_binding,
                            validation_id=validation_id, retained=retained)
    except (ValueError, TypeError, KeyError):
        return unbound
    return BookTeacherSession(repository=repository, binding=receipt_binding,
        connect=connect, authorization_provider=issuer,
        query_knowledge=query_knowledge, read_eligible_skills=read_eligible_skills)
