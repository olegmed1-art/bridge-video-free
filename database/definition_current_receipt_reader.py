"""Connect existing native evidence observer to the existing sixteen-gate reader.

No connection, target, context provider, receipt issuer, grant or route is
created here. The exact native backend still requires authenticated host wiring.
Explicit SOURCE_ONLY backends can exercise the interface but never verify real
admission. Read callbacks expire at lease exit and cannot cache a permit.
"""
from contextlib import contextmanager
from copy import deepcopy
from bridge_contracts.card_play_definition_admission import CanonReceiptReadBinding, _need, _digest, _sha, _text
from database.definition_initial_admission import SCOPE_FIELDS
from database.definition_initial_postgres import DefinitionInitialPostgres, InitialTransaction


def bind_current_definition_receipts(*, backend=None, scope=None, stable_key=None, request_key=None):
    """Private host composition; arguments must never come from request JSON."""
    host_scope=deepcopy(scope)
    native=type(backend) is DefinitionInitialPostgres

    @contextmanager
    def lease(request):
        _need(backend is not None and callable(getattr(backend,'observer',None)), 'CURRENT_OBSERVER_UNBOUND')
        _need(native or getattr(backend,'mode',None)=='SOURCE_ONLY','NATIVE_RECEIPT_BACKEND_REQUIRED')
        _need(type(host_scope) is dict and set(host_scope)==SCOPE_FIELDS
            and all(_text(v) for v in host_scope.values()),'HOST_SCOPE_REQUIRED')
        _need(_text(stable_key) and _text(request_key) and len(request_key)<=200,'HOST_READ_SELECTOR_REQUIRED')
        _need(type(request) is dict and set(request)=={'candidate_payload_hash','verification_bundle_sha256','scope'}
            and _sha(request['candidate_payload_hash']) and _sha(request['verification_bundle_sha256'])
            and _digest(request['scope'])==_digest(host_scope),'CURRENT_READ_REQUEST_BINDING')
        frozen=deepcopy(request);commit_complete=False;tx=None;active=False
        query=dict(frozen,operation='READ_ACCEPTED_DEFINITION',stable_key=stable_key,request_key=request_key)
        query_sha=_digest(query)
        with backend.observer(deepcopy(query)) as tx:
            if native:
                _need(type(tx) is InitialTransaction and tx.backend is backend and tx.mode=='OBSERVED'
                    and tx.readonly is True,'NATIVE_READ_TRANSACTION_REQUIRED')
            else:
                _need(getattr(tx,'mode',None)=='SOURCE_ONLY','SOURCE_ONLY_TRANSACTION_REQUIRED')
            _need(callable(getattr(tx,'authorize',None)) and callable(getattr(tx,'evidence',None)),
                'CURRENT_EVIDENCE_INTERFACE_UNBOUND')
            tx.authorize(deepcopy(query),write=False)
            active=True

            def read_current(current):
                _need(active,'CURRENT_READ_LEASE_CLOSED')
                _need(_digest(current)==_digest(frozen) and _digest(request)==_digest(frozen)
                    and _digest(query)==query_sha,'CURRENT_READ_SELECTOR_CHANGED')
                # Native .evidence joins real retained bundle/checks, registry,
                # memberships, source policy and CURRENT test state itself.
                # This read never calls .load/.insert_initial or another issuer.
                return deepcopy(tx.evidence(deepcopy(query),archived=False))

            try:
                yield read_current
                _need(_digest(request)==_digest(frozen) and _digest(query)==query_sha,
                    'CURRENT_READ_SELECTOR_CHANGED')
                tx.authorize(deepcopy(query),write=False)
                commit_complete=True
            finally:
                active=False
        # Native observer attests root/fences through COMMIT ACK and connection
        # close before setting finished. Suppressed exceptions cannot pass.
        _need(commit_complete and getattr(tx,'finished',False) is True,'CURRENT_READ_LEASE_INCOMPLETE')

    return CanonReceiptReadBinding(lease=lease,source_only=not native)


__all__=['bind_current_definition_receipts']
