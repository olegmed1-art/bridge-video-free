"""Opt-in real post-finish SQL reader; no issuer, DSN discovery or work retry.

The proposed 0063 API independently authenticates session_user against an
owner-enrolled assignment and a fresh READ_RESULT grant. Empty/unbound owner
tables deny. A Python owner callback cannot replace that DB authorization.
"""
import copy
import time
from contextlib import contextmanager
from collections.abc import Mapping

from universal_video.receipt_encoding import canonical_bytes,sha256
from universal_video.terminal_finish_contract import (
    Assignment,require,verify_terminal_response,checked_queue_result)

SQL_READ = 'SELECT video_queue.read_terminal_receipt_v1(%s,%s) AS terminal_receipt'
# PL/pgSQL void success is a non-NULL datum whose text output is ''. Return a
# typed boolean instead of assuming a driver maps void to Python None. An
# authority exception still aborts this expression; no empty-value coercion.
SQL_AUTH = 'SELECT (video_queue.assert_terminal_read_authority_v1(%s,%s) IS NOT NULL) AS authority'
SQL_ASSIGNMENT = 'SELECT video_queue.check_terminal_assignment_v1(%s,%s) AS assignment'
SQL_STAGE_ASSIGNMENT = 'SELECT video_queue.check_terminal_stage_assignment_v1(%s,%s) AS assignment'


def assert_enrolled_assignment_on_connection(connection,assignment,*,for_stage=False):
    """DB enrollment check, also used inside the original stage transaction."""
    require(type(assignment) is Assignment,'READ_ASSIGNMENT_UNBOUND')
    with connection.cursor() as cursor:
        cursor.execute(SQL_STAGE_ASSIGNMENT if for_stage else SQL_ASSIGNMENT,
                       (assignment.selector_sha256,assignment.raw.decode('utf-8')))
        row=cursor.fetchone()
        if isinstance(row,Mapping):
            require(set(row)=={'assignment'},'ASSIGNMENT_RESPONSE_COLUMNS')
            value=row['assignment']
        else:
            require(type(row) in (tuple,list) and len(row)==1,'ASSIGNMENT_RESPONSE_COLUMNS')
            value=row[0]
        require(canonical_bytes(value,max_bytes=16384)==assignment.raw,'ASSIGNMENT_DB_BINDING')


class PostFinishReceiptReader:
    """Source port for TerminalAckQueue, plus explicit read-only recover().

    The launcher must supply its existing target/principal connection validator
    and token port. Construction performs no IO and issues no grant. Recovery
    never invokes a queue, processor, uploader or finish function.
    """
    def __init__(self,*,assignment,claim,selected,bundle,open_connection,
                 connection_validator,access_token,metadata_reader=None,downloader=None):
        require(type(assignment) is Assignment,'READ_ASSIGNMENT_UNBOUND')
        assignment.check_claim(claim)
        require(all(callable(p) for p in (open_connection,connection_validator,access_token)),
                'READ_TRUSTED_PORTS_UNBOUND')
        self.assignment=assignment
        self.claim=copy.deepcopy(dict(claim))
        self.selected=copy.deepcopy(dict(selected));self.bundle=copy.deepcopy(dict(bundle))
        self.claim_raw=canonical_bytes(self.claim)
        self.selected_raw=canonical_bytes(self.selected)
        self.bundle_raw=canonical_bytes(self.bundle,max_bytes=4096)
        self.open_connection=open_connection;self.validate=connection_validator
        self.access_token=access_token
        self.metadata_reader=metadata_reader;self.downloader=downloader
        self._verified=None
        import threading
        self._ack_scope_lock = threading.Lock()
        self._ack_scope_attempted = False

    def _guard(self):
        from universal_video.owner_effects import assert_terminal_authorized
        require(canonical_bytes(self.claim)==self.claim_raw
                and canonical_bytes(self.selected)==self.selected_raw
                and canonical_bytes(self.bundle,max_bytes=4096)==self.bundle_raw,'READ_INPUT_CHANGED')
        self.assignment.check_claim(self.claim)
        assert_terminal_authorized(self.claim,required=True)

    def recover(self):
        """Post-commit read using pre-known inputs, even with no finish reply."""
        self._verified=None  # An uncertain new read cannot reuse an earlier ACK.
        self._guard()
        with self.open_connection() as connection:
            self.validate(connection)  # Actual installed target/principal port.
            self._guard()
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(SQL_READ,(self.assignment.selector_sha256,
                                             self.assignment.raw.decode('utf-8')))
                    row=cursor.fetchone()
                    if isinstance(row,Mapping):
                        require(set(row)=={'terminal_receipt'},'READ_RESPONSE_COLUMNS')
                        response=row['terminal_receipt']
                    else:
                        require(type(row) in (tuple,list) and len(row)==1,'READ_RESPONSE_COLUMNS')
                        response=row[0]
                raw,queue=verify_terminal_response(response,assignment=self.assignment,
                    selected=self.selected,bundle=self.bundle)
                self._guard()  # Authority may expire/revoke during the lookup.
        self._guard()  # COMMIT/connection close must also have succeeded.
        from universal_video import terminal_evidence_v2 as terminal
        ports={k:v for k,v in dict(metadata_reader=self.metadata_reader,
                                  downloader=self.downloader).items() if v is not None}
        terminal.reverify_terminal_output_live(self.claim,self.selected,self.access_token(),
            max_ai_done_bytes=4*1024*1024,**ports)
        self._guard()
        self._verified=(raw,sha256(raw))
        return raw,queue

    def check_before_ack(self,raw):
        """Fresh owner AND DB-grant assertion; no receipt reread or ACK send."""
        self._guard()
        require(self._verified is not None and type(raw) is bytes
                and (raw,sha256(raw))==self._verified,'READ_ACK_EVIDENCE_CHANGED')
        with self.open_connection() as connection:
            self.validate(connection)
            with connection.transaction():
                with connection.cursor() as cursor:
                    self._guard()
                    cursor.execute(SQL_AUTH,(self.assignment.selector_sha256,
                                             self.assignment.raw.decode('utf-8')))
                    row=cursor.fetchone()
                    require((isinstance(row,Mapping) and set(row)=={'authority'} and row['authority'] is True)
                            or (type(row) in (tuple,list) and len(row)==1 and row[0] is True),'READ_ACK_DB_AUTHORITY')
                self._guard()
        self._guard()

    @contextmanager
    def ack_scope(self, raw):
        """Transaction-lifetime primitive for one ACK; over-budget => UNKNOWN.

        Owner-side read-grant lock window, lifetime margin and durable ACKED
        are separate prerequisites. This context supplies no owner authority
        and is not wired into legacy control v2 as an implicit activation.
        The 2s completion check cannot preempt a blocking caller. The actual
        control RPC must enforce its own deadline. Any uncertain send or
        transaction close must remain UNKNOWN; never retry.
        """
        with self._ack_scope_lock:
            require(not self._ack_scope_attempted, 'READ_ACK_REPLAY_NO_RETRY')
            self._ack_scope_attempted = True
        self._guard()
        require(self._verified is not None and type(raw) is bytes
                and (raw,sha256(raw)) == self._verified, 'READ_ACK_EVIDENCE_CHANGED')
        with self.open_connection() as connection:
            self.validate(connection)
            with connection.transaction():
                with connection.cursor() as cursor:
                    # SQL_AUTH takes FOR SHARE locks. PostgreSQL forbids them
                    # in READ ONLY transactions; keep the existing transaction
                    # mode and perform only the existing SELECT authority call.
                    self._guard()
                    cursor.execute(SQL_AUTH, (self.assignment.selector_sha256,
                                             self.assignment.raw.decode('utf-8')))
                    row = cursor.fetchone()
                    require((isinstance(row,Mapping) and set(row)=={'authority'} and row['authority'] is True)
                            or (type(row) in (tuple,list) and len(row)==1 and row[0] is True),
                            'READ_ACK_DB_AUTHORITY')
                self._guard()
                deadline = time.monotonic() + 2
                yield
                require(time.monotonic() < deadline, 'READ_ACK_DEADLINE_UNKNOWN_NO_RETRY')
                self._guard()
        self._guard()

    def __call__(self,claim,terminal_result,queue_result):
        require(canonical_bytes(dict(claim))==self.claim_raw
                and canonical_bytes(dict(terminal_result))==self.selected_raw,'READ_SELECTED_CALLBACK_CHANGED')
        # A known queue response is an optional extra comparison, never a
        # lookup/grant prerequisite. recover() works without any queue result.
        checked_queue_result(dict(queue_result))
        raw,captured=self.recover()
        require(canonical_bytes(captured)==canonical_bytes(dict(queue_result)),'READ_FINISH_REPLY_CHANGED')
        self.check_before_ack(raw)
        return raw


class StagedPostFinishReceiptReader:
    """Lazy real-reader composition from the handoff's retained staging facts.

    May be passed to TerminalAckQueue before compute. It creates no IDs and
    never obtains a bundle/selector from the possibly lost finish response.
    """
    def __init__(self,handoff,**ports):
        from universal_video.private_result_handoff import PrivateResultStagingHandoff
        require(type(handoff) is PrivateResultStagingHandoff,'READ_HANDOFF_UNBOUND')
        self.handoff=handoff;self.ports=ports;self.reader=None

    def _get(self):
        if self.reader is None:self.reader=self.handoff.make_post_finish_reader(**self.ports)
        return self.reader

    def recover(self):return self._get().recover()

    def ack_scope(self, raw):return self._get().ack_scope(raw)

    def __call__(self,claim,terminal_result,queue_result):
        return self._get()(claim,terminal_result,queue_result)
