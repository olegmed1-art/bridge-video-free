"""Join the original one-job queue to control v2 after terminal readback.

ClaimReceiptJournal.acknowledge_commit is a PRE-compute claim ACK. It must
remain the original claim_committer; a post-terminal journal is NOT its adapter.
No queue return, Mapping or receipt digest proves durability. The trusted
source-bound receipt_reader must verify the actual persisted terminal result.
"""
from collections.abc import Mapping
from types import MappingProxyType
import threading

from . import diana20_transport_protocol as p
from .diana20_application_transport import ApplicationTransport
from .owner_effects import OwnerEffectRefused


def terminal_work_key(intent, namespace):
    """Existing stable recovery identity DATA, never namespace approval."""
    p.need(type(intent) is p.Intent and type(namespace) is str and 0 < len(namespace) <= 128,
           'TERMINAL_RECOVERY_IDENTITY_REQUIRED')
    i = intent.value
    return p.digest(p.canonical([namespace] + [i[k] for k in (
        'batch_id','job_id','source_file_id','source_fingerprint','source_sha256',
        'generation_sha256','manifest_sha256')]))


class TerminalAckQueue:
    """Single-use original queue + mandatory post-finish receipt readback.

    receipt_reader(claim, terminal_result, queue_result)->bytes must validate
    actual durable terminal persistence for this exact claim and selected
    result. All three arguments are detached snapshots. This is an explicit
    trusted launcher port, not a default reader or a native UID bridge.
    The unchanged parent invokes this adapter while its effect/owner scope
    and original accepted control connection are still alive.
    """
    def __init__(self, queue_once, transport, receipt_reader, *, recovery=None):
        p.need(callable(queue_once) and type(transport) is ApplicationTransport
               and (callable(receipt_reader) or (receipt_reader is None and recovery is not None)),
               'TERMINAL_QUEUE_PORTS')
        self.queue_once, self.transport, self.receipt_reader = queue_once, transport, receipt_reader
        self.attempted = False
        self.lock = threading.Lock()
        p.need(recovery is None or type(recovery) is TerminalRecoveryAdapter, 'TERMINAL_RECOVERY_TYPE')
        self.recovery = recovery

    def __call__(self, **arguments):
        acquired = self.lock.acquire(blocking=False)
        try:
            p.need(acquired and not self.attempted, 'TERMINAL_QUEUE_REPLAY_NO_RETRY')
            self.attempted = True
            if self.recovery is not None:
                self.recovery.arm()  # External reservation before ANY claim.
            processor, committer = arguments.get('processor'), arguments.get('claim_committer')
            p.need(callable(processor) and callable(committer), 'TERMINAL_QUEUE_PROCESSOR')
            selected, committed = [], []
            commit_attempted = callback_failed = False
            callback_lock = threading.RLock()

            def exact(claim):
                p.need(isinstance(claim, Mapping) and
                       p.canonical({k: claim[k] for k in p.CLAIM}) == self.transport.job.claim_raw,
                       'TERMINAL_QUEUE_CLAIM')
                return p.canonical(dict(claim))

            def claim_commit(claim):
                nonlocal commit_attempted, callback_failed
                with callback_lock:
                    try:
                        p.need(not commit_attempted and not callback_failed, 'TERMINAL_QUEUE_CLAIM_REPLAY')
                        commit_attempted = True  # Sticky even if original ACK is lost.
                        raw = exact(claim)
                        result = committer(claim)  # Original PRE-compute position/return.
                        p.need(exact(claim) == raw and not callback_failed, 'TERMINAL_QUEUE_CLAIM_MUTATION')
                        committed.append(raw)  # Confirm only after the real ACK returns.
                        if self.recovery is not None:
                            self.recovery.claim_committed(claim)
                            # Explicit trusted source port, not wire-selected
                            # issuance. Actual UID1001 capability remains external.
                            self.receipt_reader = self.recovery.bind_committed(claim)
                        return result
                    except BaseException:
                        callback_failed = True
                        raise

            def capture(claim):
                nonlocal callback_failed
                with callback_lock:
                    try:
                        p.need(not selected and not callback_failed and isinstance(claim, Mapping),
                               'TERMINAL_QUEUE_PROCESSOR_REPLAY')
                        claim_raw = exact(claim)
                        p.need(len(committed) == 1 and committed[0] == claim_raw,
                               'TERMINAL_QUEUE_COMMIT_BINDING')
                        detached = MappingProxyType(p.parse(claim_raw))
                        selected.append([claim_raw, None])
                        if self.recovery is not None:
                            self.recovery.assert_bound(claim, self.receipt_reader)
                        result = processor(detached)
                        p.need(isinstance(result, Mapping), 'TERMINAL_QUEUE_RESULT')
                        result_raw = p.canonical(dict(result))
                        p.need(self.transport.completed and result_raw == self.transport.result_raw
                               and not callback_failed, 'TERMINAL_QUEUE_RESULT_BINDING')
                        selected[0][1] = result_raw
                        return result
                    except BaseException:
                        callback_failed = True
                        raise

            options = dict(arguments, processor=capture, claim_committer=claim_commit)
            if 'terminal_recovery' in arguments:
                p.need(self.recovery is not None and arguments['terminal_recovery'] is self.recovery,
                       'TERMINAL_RECOVERY_OVERRIDE')
            result = self.queue_once(**options)  # Original claim, stage and finish.
            p.need(not callback_failed and len(committed) == 1
                   and len(selected) == 1 and selected[0][1] is not None
                   and isinstance(result, Mapping), 'TERMINAL_QUEUE_INCOMPLETE')
            queue_raw = p.canonical(dict(result))
            claim_raw, terminal_raw = selected[0]
            claim = MappingProxyType(p.parse(claim_raw))
            self.transport._guard(claim)
            terminal_view, queue_view = p.parse(terminal_raw), p.parse(queue_raw)
            if self.recovery is not None:
                self.recovery.ack(claim, terminal_view, queue_view, self.receipt_reader, self.transport)
                p.need(p.canonical(dict(result)) == queue_raw
                       and self.transport.result_raw == terminal_raw,'TERMINAL_QUEUE_READBACK_MUTATION')
                self.transport._guard(claim)
                return result
            receipt = self.receipt_reader(claim, terminal_view, queue_view)
            p.need(p.canonical(dict(claim)) == claim_raw and
                   p.canonical(terminal_view) == terminal_raw and p.canonical(queue_view) == queue_raw
                   and p.canonical(dict(result)) == queue_raw
                   and self.transport.result_raw == terminal_raw, 'TERMINAL_QUEUE_READBACK_MUTATION')
            self.transport._guard(claim)
            self.transport.terminal_persistence_ack(claim, receipt)
            return result  # Preserve the original queue return object.
        except Exception:
            if self.transport.control is not None:
                self.transport.control.fail()
            raise OwnerEffectRefused('OWNER_TERMINAL_QUEUE_UNKNOWN_NO_RETRY') from None
        finally:
            if acquired:
                self.lock.release()


class TerminalRecoveryAdapter:
    """Opt-in queue/handoff/restart join over existing injected owner ports.

    Public types/callables are NOT authority. There is no runtime loader and
    broker/issuer gates remain OFF. The trusted launcher must supply actual
    owner operations and checked reader ports. Tests supply explicit doubles.
    All restart paths are read/reconcile only, never compute/finish/ACK retry.
    """
    PHASES = ('ARMED', 'CLAIM_COMMITTED', 'BOUND', 'PREPARED', 'FINISH_ATTEMPTED',
              'READ_VERIFIED', 'ACK_ATTEMPTED', 'ACKED')
    FIELDS = {'schema','work_key','intent_sha256','seq','previous_sha256','phase',
              'claim_sha256','selector','assignment_sha256','watermark','receipt_sha256',
              'receipt_size','ack_attempt_id','enrollment_sha256','admission_record_sha256',
              'admission_anchor_sha256','target_identity_sha256','principal',
              'transport_job_sha256','stream_sha256','result_sha256'}

    def __init__(self, *, owner, retention, journal, intent, namespace, recovery_ports,
                 native_completion=None):
        from .owner_application_channel import TerminalOwnerCapabilities
        from .private_admission_retention import TerminalHandoffRetention
        from .private_one_job_operator import ClaimReceiptJournal
        p.need(type(owner) is TerminalOwnerCapabilities and type(retention) is TerminalHandoffRetention
               and type(journal) is ClaimReceiptJournal and type(intent) is p.Intent,
               'TERMINAL_EXISTING_CAPABILITIES_REQUIRED')
        p.need(type(namespace) is str and 0 < len(namespace) <= 128
               and type(recovery_ports) is dict and set(recovery_ports) <=
               {'open_connection','connection_validator','access_token','metadata_reader','downloader'}
               and all(callable(recovery_ports.get(k)) for k in
                       ('open_connection','connection_validator','access_token')), 'TERMINAL_RECOVERY_PORTS')
        self.owner,self.retention,self.journal,self.intent=owner,retention,journal,intent
        self.ports=dict(recovery_ports)
        i=intent.value
        # Stable across changed lease/Assignment/nonce/policy epoch. Changing a
        # namespace/generation requires independent owner approval, not a retry.
        self.work_key=terminal_work_key(intent, namespace)
        self.scope=p.canonical(dict(work_key=self.work_key,namespace=namespace,
            intent_sha256=intent.sha256,target_identity_sha256=i['target_identity_sha256'],
            principal=i['worker_login']))
        self.head_raw=None
        self.prepared_job=self.reader=self._claim_raw=None
        self._transport=None
        self._failed=self._armed=self._restart=False
        self._lock=threading.RLock()
        self._worker_inputs=None
        # Supplied by the original owner launcher, never by a worker payload.
        # A native task must have its real terminal transition before claiming.
        self.native_completion = native_completion
        self._completion_binding = native_completion
        if intent.value['worker_login'] == 'autopilot_light_worker_login':
            from .native_video_terminal import NativeVideoTerminalCompletion
            p.need(type(native_completion) is NativeVideoTerminalCompletion
                   and native_completion.enabled is True,
                   'TERMINAL_NATIVE_COMPLETION_REQUIRED')
        else:
            p.need(native_completion is None, 'TERMINAL_UNEXPECTED_NATIVE_COMPLETION')

    def _guard(self):
        p.need(self.owner.assert_live(self.scope) is None,'TERMINAL_OWNER_REFUSED')

    def _read_head(self):
        self._guard()
        raw=self.owner.read_head(self.work_key)
        self._guard()
        if raw is None:return None
        p.need(type(raw) is bytes and 0<len(raw)<=4096,'TERMINAL_HEAD_BOUND')
        v=p.parse(raw)
        i=self.intent.value
        p.need(type(v) is dict and set(v)==self.FIELDS and p.canonical(v)==raw
               and v['schema']=='terminal-queue-head/v1' and v['work_key']==self.work_key
               and v['intent_sha256']==self.intent.sha256 and v['phase'] in self.PHASES
               and type(v['seq']) is int and 1<=v['seq']<=8
               and v['target_identity_sha256']==i['target_identity_sha256']
               and v['principal']==i['worker_login'], 'TERMINAL_HEAD_BINDING')
        index=self.PHASES.index(v['phase'])
        p.need(v['seq']==index+1 and (v['previous_sha256'] is None if index==0
                                      else p.pin(v['previous_sha256'])), 'TERMINAL_HEAD_SEQUENCE')
        for key in ('claim_sha256','selector','assignment_sha256','receipt_sha256',
                    'ack_attempt_id','enrollment_sha256','admission_record_sha256','admission_anchor_sha256',
                    'transport_job_sha256','stream_sha256','result_sha256'):
            p.need(v[key] is None or p.pin(v[key]),'TERMINAL_HEAD_PIN')
        p.need(v['receipt_size'] is None or (type(v['receipt_size']) is int and 0<v['receipt_size']<=65536),
               'TERMINAL_HEAD_RECEIPT_SIZE')
        if v['watermark'] is not None:
            p.need(type(v['watermark']) is str and len(v['watermark'])<=4096,'TERMINAL_HEAD_WATERMARK')
            mark=self.retention._watermark(v['watermark'].encode())
            p.need(mark['selector']==v['selector'] and mark['phase']==v['phase']
                   and mark['ack_attempt_id']==v['ack_attempt_id'],'TERMINAL_HEAD_SELECTOR')
        for required_after,keys in ((1,('claim_sha256',)),
                (2,('selector','assignment_sha256','enrollment_sha256','admission_record_sha256','admission_anchor_sha256')),
                (3,('watermark',)),(5,('receipt_sha256','receipt_size','transport_job_sha256',
                                      'stream_sha256','result_sha256')),(6,('ack_attempt_id',))):
            p.need(all((v[k] is not None) if index>=required_after else (v[k] is None) for k in keys),
                   'TERMINAL_HEAD_PHASE_FIELDS')
        return raw

    def _publish(self, phase, **changes):
        prior=self.head_raw
        p.need(self._read_head()==prior,'TERMINAL_CAS_STALE')
        if prior is None:
            p.need(phase=='ARMED','TERMINAL_HEAD_ORDER')
            v=dict.fromkeys(self.FIELDS)
            i=self.intent.value
            v.update(schema='terminal-queue-head/v1',work_key=self.work_key,
                intent_sha256=self.intent.sha256,seq=1,phase=phase,
                target_identity_sha256=i['target_identity_sha256'],principal=i['worker_login'])
        else:
            v=p.parse(prior)
            p.need(self.PHASES.index(phase)==self.PHASES.index(v['phase'])+1,'TERMINAL_HEAD_ORDER')
            v.update(seq=v['seq']+1,previous_sha256=p.digest(prior),phase=phase)
        v.update(changes)
        raw=p.canonical(v)
        p.need(len(raw)<=4096,'TERMINAL_HEAD_BOUND')
        self._guard()
        # This MUST be an existing atomic cross-process CAS, including its
        # own durable external watermark. Local locks/readback do not supply it.
        p.need(self.owner.compare_and_swap(self.work_key,None if prior is None else p.digest(prior),raw) is None,
               'TERMINAL_CAS_REFUSED')
        p.need(self._read_head()==raw,'TERMINAL_CAS_ACK_UNKNOWN')
        self.head_raw=raw

    def _phase(self, phase):
        p.need(not self._failed and not self._restart and self.head_raw is not None and self._read_head()==self.head_raw
               and p.parse(self.head_raw)['phase']==phase,'TERMINAL_PHASE_UNKNOWN_NO_RETRY')

    def _operation(self):
        from contextlib import contextmanager
        @contextmanager
        def held():
            with self._lock:
                p.need(not self._failed,'TERMINAL_ADAPTER_UNKNOWN_NO_RETRY')
                try:yield
                except BaseException:
                    self._failed=True
                    if self._transport is not None and self._transport.control is not None:
                        self._transport.control.fail()
                    raise OwnerEffectRefused('OWNER_TERMINAL_RECOVERY_UNKNOWN_NO_RETRY') from None
        return held()

    def arm(self):
        with self._operation():
            p.need(not self._armed and not self._restart and self.head_raw is None
                   and self.journal.inspect_state()=='ABSENT','TERMINAL_RESTART_READ_ONLY')
            self._armed=True
            self._publish('ARMED')

    def claim_committed(self, claim):
        from .receipt_encoding import canonical_bytes
        with self._operation():
            self._phase('ARMED')
            raw=canonical_bytes(dict(claim),max_bytes=16384)
            evidence=p.parse(self.journal.committed_evidence(claim))
            i=self.intent.value
            p.need(all(claim.get(k)==i[k] and type(claim.get(k)) is type(i[k]) for k in
                       ('batch_id','job_id','source_file_id','attempt_count','processing_profile','algorithm_revision'))
                   and evidence['claim_sha256']==p.digest(raw),'TERMINAL_COMMITTED_CLAIM')
            self._publish('CLAIM_COMMITTED',claim_sha256=p.digest(raw))
            self._claim_raw=raw

    def bind_private_worker_inputs(self, arguments):
        """Register actual LOCAL preparation ports; never serialize or authorize.

        Original owner-client binder uses these exact inputs AFTER genuine
        issuance/enrollment. This method does not accept an Assignment or mint
        credentials. The original worker/DB checks and prepare still apply.
        """
        with self._operation():
            self._phase('ARMED')
            p.need(self._worker_inputs is None and type(arguments) is dict,
                   'TERMINAL_WORKER_INPUTS_ONCE')
            keys={'queue_dsn','expected_database','expected_login','expected_batch_id',
                  'expected_job_id','expected_attempt_count','expected_source_file_id',
                  'worker_key','admission_id','source_fingerprint','subset_adapter',
                  'approval_waiter','processor','private_failure_handler'}
            p.need(set(arguments)==keys,'TERMINAL_WORKER_INPUT_FIELDS')
            self._worker_inputs=dict(arguments)

    def private_worker_inputs(self, claim, assignment):
        """Existing prepare_private_one_job arguments, detached claim, same ports.

        Trusted in-process client only. Never a wire/native-owner ABI; contains
        private DB connection inputs. Returned Assignment remains INPUT until
        prepare and bind repeat actual worker enrollment/readback checks.
        """
        from .receipt_encoding import canonical_bytes,strict_loads
        from .terminal_finish_contract import Assignment
        with self._operation():
            self._phase('CLAIM_COMMITTED')
            p.need(self._worker_inputs is not None and type(assignment) is Assignment
                   and canonical_bytes(dict(claim),max_bytes=16384)==self._claim_raw,
                   'TERMINAL_WORKER_INPUT_BINDING')
            return dict(self._worker_inputs,claim=strict_loads(self._claim_raw,max_bytes=16384),
                        expected_lease_token=claim['lease_token'],assignment=assignment)

    def bind(self, prepared, *, metadata):
        from .private_one_job_entrypoint import PreparedPrivateOneJob
        from .receipt_encoding import canonical_bytes,strict_loads
        from database.video_post_finish_receipt import assert_enrolled_assignment_on_connection
        with self._operation():
            self._phase('CLAIM_COMMITTED')
            p.need(type(prepared) is PreparedPrivateOneJob and type(metadata) is dict
                   and set(metadata)==self.retention.META,'TERMINAL_BIND_PORTS')
            prepared.check()
            if self._worker_inputs is not None:
                ports=self._worker_inputs
                p.need(all(a is b for a,b in zip(prepared._ports[:-1],
                    (ports['subset_adapter'],ports['approval_waiter'],ports['processor'],
                     ports['private_failure_handler']))),'TERMINAL_WORKER_PORTS_CHANGED')
                from .private_one_job_entrypoint import _prepared_binding
                claimed=strict_loads(self._claim_raw,max_bytes=16384)
                expected=_prepared_binding(ports['queue_dsn'],ports['expected_database'],
                    ports['expected_login'],ports['expected_batch_id'],ports['expected_job_id'],
                    claimed['lease_token'],ports['expected_attempt_count'],ports['expected_source_file_id'],
                    ports['worker_key'],ports['admission_id'],ports['source_fingerprint'],
                    prepared.handoff.assignment)
                p.need(prepared._binding==expected,'TERMINAL_WORKER_TARGET_CHANGED')
            assignment=prepared.handoff.assignment
            claim=strict_loads(self._claim_raw,max_bytes=16384)
            assignment.check_claim(claim,self.intent.value['worker_key'])
            assigned=assignment.value
            p.need(all(assigned[k]==self.intent.value[k] for k in (
                'admission_id','source_fingerprint','source_sha256','unit_sha256','binding_sha256',
                'generation_sha256','manifest_sha256','policy_id','policy_epoch')),
                'TERMINAL_ASSIGNMENT_INTENT_CHANGED')
            p.need(prepared._claim_raw==self._claim_raw and metadata['intent_hash']==self.intent.sha256
                   and metadata['target_identity_sha256']==self.intent.value['target_identity_sha256']
                   and metadata['principal']==self.intent.value['worker_login']
                   and all(p.pin(metadata[k]) for k in self.retention.META-{'principal'}),
                   'TERMINAL_BIND_METADATA')
            with prepared.handoff.open_connection() as connection:
                assert_enrolled_assignment_on_connection(connection,assignment)
            prepared.check()
            self.metadata=p.parse(p.canonical(metadata))
            ports={k:v for k,v in self.ports.items() if k in ('access_token','metadata_reader','downloader')}
            reader=prepared.handoff.post_finish_reader(
                connection_validator=prepared.adapter.connection_validator,**ports)
            p.need(reader.handoff is prepared.handoff and prepared.handoff.terminal_recovery is None,
                   'TERMINAL_READER_HANDOFF')
            self._publish('BOUND',assignment_sha256=assignment.sha256,selector=assignment.selector_sha256,
                enrollment_sha256=metadata['enrollment_sha256'],
                admission_record_sha256=metadata['admission_record_sha256'],
                admission_anchor_sha256=metadata['admission_anchor_sha256'])
            self.prepared_job,self.reader=prepared,reader
            prepared.handoff.terminal_recovery=self
            return reader

    def bind_committed(self, claim):
        from .receipt_encoding import canonical_bytes
        with self._operation():
            self._phase('CLAIM_COMMITTED')
            p.need(canonical_bytes(dict(claim),max_bytes=16384)==self._claim_raw,
                   'TERMINAL_COMMITTED_CLAIM')
            self._guard()
            bound=self.owner.bind_committed(self._claim_raw,self.journal.committed_evidence(claim))
            self._guard()
            p.need(type(bound) is tuple and len(bound)==2,'TERMINAL_BIND_PORTS')
            return self.bind(bound[0],metadata=bound[1])

    def assert_bound(self, claim, reader):
        from .receipt_encoding import canonical_bytes
        with self._operation():
            self._phase('BOUND')
            p.need(self.reader is reader and reader.handoff is self.prepared_job.handoff
                   and canonical_bytes(dict(claim),max_bytes=16384)==self._claim_raw,'TERMINAL_QUEUE_READER_CHANGED')
            self.prepared_job.check()

    def prepared(self, handoff):
        with self._operation():
            self._phase('BOUND')
            self._check_handoff(handoff)
            wm=handoff.retain_prepared_terminal(self.retention,metadata=self.metadata)
            self._publish('PREPARED',watermark=wm.decode())

    def before_finish(self, handoff):
        with self._operation():
            self._phase('PREPARED')
            self._check_handoff(handoff)
            wm=self.retention.advance(p.parse(self.head_raw)['watermark'].encode(),'FINISH_ATTEMPTED')
            self._publish('FINISH_ATTEMPTED',watermark=wm.decode())

    def _check_handoff(self,handoff):
        from .receipt_encoding import canonical_bytes
        self.prepared_job.check()
        head=p.parse(self.head_raw)
        p.need(handoff is self.prepared_job.handoff and handoff.assignment is not None
               and handoff.assignment.sha256==head['assignment_sha256']
               and handoff.assignment.selector_sha256==head['selector']
               and p.digest(self._claim_raw)==head['claim_sha256'],'TERMINAL_HANDOFF_CHANGED')

    def before_stage(self,handoff):
        with self._operation():
            self._phase('BOUND')
            self._check_handoff(handoff)

    def _query(self, head):
        fields=('work_key','intent_sha256','claim_sha256','selector','assignment_sha256',
                'receipt_sha256','receipt_size','ack_attempt_id','enrollment_sha256',
                'admission_record_sha256','admission_anchor_sha256','target_identity_sha256','principal',
                'transport_job_sha256','stream_sha256','result_sha256')
        p.need(all(head[k] is not None for k in fields),'TERMINAL_ACK_BINDING_REQUIRED')
        return p.canonical(dict(schema='terminal-ack-query/v1',**{k:head[k] for k in fields}))

    @staticmethod
    def _acked(query, raw):
        p.need(type(raw) is bytes and len(raw)<=65536 and raw==p.canonical(dict(
            schema='terminal-owner-acked/v1',state='ACKED',binding=p.parse(query))),
            'TERMINAL_OWNER_ACK_BINDING')
        return raw

    def _complete_native(self, raw, reader):
        if self.intent.value['worker_login'] != 'autopilot_light_worker_login':
            p.need(self.native_completion is None, 'TERMINAL_UNEXPECTED_NATIVE_COMPLETION')
            return
        from .native_video_terminal import NativeVideoTerminalCompletion
        from database.video_post_finish_receipt import StagedPostFinishReceiptReader
        completion = self.native_completion
        p.need(completion is self._completion_binding
               and type(completion) is NativeVideoTerminalCompletion
               and type(reader) is StagedPostFinishReceiptReader
               and reader.handoff is self.prepared_job.handoff,
               'TERMINAL_NATIVE_COMPLETION_BINDING')
        original = reader._get()
        p.need(original._verified == (raw, p.digest(raw))
               and original.assignment is self.prepared_job.handoff.assignment,
               'TERMINAL_NATIVE_VERIFIED_RECEIPT_REQUIRED')
        self._guard()
        completion.complete(dict(status='COMMITTED_TERMINAL_RESULT',
            receipt_text=raw.decode('utf8'), receipt_sha256=p.digest(raw)),
            assignment=original.assignment, selected=original.selected, bundle=original.bundle)
        self._guard()

    def ack(self, claim, terminal_result, queue_result, reader, transport):
        with self._operation():
            self._phase('FINISH_ATTEMPTED')
            self._check_handoff(self.prepared_job.handoff)
            p.need(reader is self.reader and reader.handoff is self.prepared_job.handoff,
                   'TERMINAL_READER_HANDOFF')
            p.need(type(transport) is ApplicationTransport and transport.completed
                   and not transport.terminal_ack_attempted and not transport.terminal_acknowledged
                   and type(transport.sealed_stream) is bytes
                   and transport.result_raw==p.canonical(dict(terminal_result))
                   and transport.job.claim_raw==p.canonical({k:claim[k] for k in p.CLAIM}),
                   'TERMINAL_TRANSPORT_BINDING')
            self._transport=transport
            a=self.prepared_job.handoff.assignment.value
            p.need(all(getattr(transport.job,attr)==a[key] for attr,key in (
                ('source_sha256','source_sha256'),('context_sha256','context_sha256'),
                ('request_sha256','request_sha256'),('generation_sha256','generation_sha256'),
                ('manifest_sha256','manifest_sha256'),('policy_id','policy_id'),('policy_epoch','policy_epoch'))),
                'TERMINAL_TRANSPORT_ASSIGNMENT')
            transport._guard(claim)
            raw=reader(claim,terminal_result,queue_result)
            p.need(type(raw) is bytes and 0<len(raw)<=65536,'TERMINAL_READBACK_BOUND')
            wm=self.retention.advance(p.parse(self.head_raw)['watermark'].encode(),
                                      'READ_VERIFIED',evidence_raw=raw)
            self._publish('READ_VERIFIED',watermark=wm.decode(),receipt_sha256=p.digest(raw),receipt_size=len(raw),
                transport_job_sha256=transport.job.sha256,stream_sha256=p.digest(transport.sealed_stream),
                result_sha256=p.digest(transport.result_raw))
            # Processing DONE and delivery are separate facts. An uncertain SQL
            # completion stops here; recovery never repeats this mutation/finish.
            self._complete_native(raw, reader)
            self._guard()
            attempt=self.owner.new_ack_id(self.scope)
            p.need(p.pin(attempt),'TERMINAL_ACK_ID')
            wm=self.retention.advance(wm,'ACK_ATTEMPTED',ack_attempt_id=attempt)
            self._publish('ACK_ATTEMPTED',watermark=wm.decode(),ack_attempt_id=attempt)
            query=self._query(p.parse(self.head_raw))
            delivered=False
            def deliver_once():
                nonlocal delivered
                p.need(not delivered,'TERMINAL_ACK_BRIDGE_REPLAY')
                delivered=True  # Sticky before the original wire writer.
                transport.terminal_persistence_ack(claim,raw)
                expected=dict(job_sha256=transport.job.sha256,stream_sha256=p.digest(transport.sealed_stream),
                    result_sha256=p.digest(transport.result_raw),ack_sha256=p.digest(raw),ack_size=len(raw))
                p.need(transport.terminal_acknowledged and transport.terminal_packet==expected,
                       'TERMINAL_ACK_BRIDGE_UNKNOWN')
            with self.owner.ack_window(query):
                self._guard()
                with reader.ack_scope(raw):
                    # The existing source-bound owner bridge must invoke this
                    # exact one-shot writer; returning JSON cannot set flags.
                    acked=self._acked(query,self.owner.send_ack(query,deliver_once))
                    p.need(delivered and transport.terminal_acknowledged,'TERMINAL_ACK_BRIDGE_REQUIRED')
                    self._guard()
            # A close/window failure after delivery leaves ACK_ATTEMPTED.
            wm=self.retention.advance(wm,'ACKED',evidence_raw=acked)
            self._publish('ACKED',watermark=wm.decode())

    def recover(self):
        """Explicit restart reconciliation ONLY, even if no effect is visible."""
        from .private_result_handoff import recover_retained_terminal
        with self._operation():
            self._restart=True
            raw=self._read_head()
            if raw is None:
                return dict(state='STOP_NO_EXTERNAL_HEAD',journal_state=self.journal.inspect_state())
            head=p.parse(raw)
            if head['phase'] in ('ARMED','CLAIM_COMMITTED','BOUND'):
                return dict(state='STOP_CLAIM_OR_BIND_RECONCILIATION',phase=head['phase'],
                            journal_state=self.journal.inspect_state())
            if head['phase'] in ('ACK_ATTEMPTED','ACKED'):
                query=self._query(head)
                with self.owner.read_window(query):
                    self._guard()
                    receipt=self.owner.read_ack(query)  # NEVER send_ack.
                    self._guard()
                if receipt is None:
                    return dict(state='UNKNOWN_ACK_NO_RESEND', owner_accepted=False,
                                delivery_confirmed=False)
                self._acked(query,receipt)
                # The owner stores acceptance BEFORE the one-shot wire effect.
                # Acceptance alone therefore cannot prove delivery after a crash.
                # Only the final external head was published after the original
                # transport confirmed its exact terminal packet and windows closed.
                confirmed = head['phase'] == 'ACKED'
                return dict(state=('DELIVERY_CONFIRMED_READBACK' if confirmed else
                                   'OWNER_ACCEPTED_DELIVERY_UNKNOWN'),
                            owner_accepted=True, delivery_confirmed=confirmed,
                            ack_sha256=p.digest(receipt))
            p.need(head['watermark'] is not None,'TERMINAL_RECOVERY_INPUTS_MISSING')
            with self.owner.read_window(raw):
                self._guard()
                expected={k:head[k] for k in ('claim_sha256','assignment_sha256','selector',
                    'intent_sha256','enrollment_sha256','admission_record_sha256',
                    'admission_anchor_sha256','target_identity_sha256','principal')}
                receipt,queue=recover_retained_terminal(self.retention,head['watermark'].encode(),
                                                        expected_binding=expected,**self.ports)
                self._guard()
            return dict(state='READ_VERIFIED_NO_ACK',receipt_sha256=p.digest(receipt),queue_result=queue)
