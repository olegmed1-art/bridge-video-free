"""Native receipt QueuePort over parameterized PostgreSQL RPCs.

Pass the existing worker's single-RPC connection function. Migration 0339 is
owner-only until native runtime activation is separately reviewed; there is no
credential fallback, broad table query, or legacy GitHub proof conversion here.
"""
from .codex_cli_bridge import canonical, validate_request, SHA


class NativeQueue:
    def __init__(self, rpc):
        self.rpc = rpc

    def _one(self, statement, parameters):
        row = self.rpc(statement, parameters)
        if not isinstance(row, dict) or 'payload' not in row:
            raise ValueError('NATIVE_QUEUE_RPC_RESPONSE_INVALID')
        return row['payload']

    def reserve(self, dispatch_id, assignment, branch):
        return self._one('SELECT autopilot.native_cli_reserve(%s::uuid,%s::jsonb,%s) AS payload',
                         (dispatch_id, canonical(assignment), branch))

    def snapshot(self, dispatch_id):
        return self._one('SELECT autopilot.native_cli_snapshot(%s::uuid) AS payload', (dispatch_id,))

    def acknowledge(self, request, task_id, prompt_sha256):
        validate_request(request)
        self._one('SELECT autopilot.native_cli_ack(%s::jsonb,%s,%s) AS payload',
                  (canonical(request), task_id, prompt_sha256))

    def begin_submission(self, request):
        result = self._one('SELECT autopilot.native_cli_begin(%s::jsonb) AS payload',
                           (canonical(request),))
        if type(result) is not bool:
            raise ValueError('NATIVE_QUEUE_BEGIN_INVALID')
        return result

    def finish(self, request, task_id, receipt):
        validate_request(request)
        self._one('SELECT autopilot.native_cli_finish(%s::jsonb,%s,%s::jsonb) AS payload',
                  (canonical(request), task_id, canonical(receipt)))

    def current(self, request):
        result = self._one('SELECT autopilot.native_cli_current(%s::jsonb) AS payload',
                           (canonical(request),))
        if type(result) is not bool:
            raise ValueError('NATIVE_QUEUE_AUTHORITY_INVALID')
        return result


class NativeAuthority:
    def __init__(self, queue, read_pr):
        self.queue, self.read_pr = queue, read_pr

    def inspect(self, request):
        validate_request(request)
        # Reader must use the fixed repository and return the primary GitHub
        # PR object. No model report can supply this independent authority.
        pr = self.read_pr(request['target_pr'])
        head = pr['head']
        if not isinstance(head.get('sha'), str) or not SHA.fullmatch(head['sha']):
            raise ValueError('NATIVE_PR_HEAD_INVALID')
        current = (pr['number'] == request['target_pr']
                   and head['ref'] == request['branch']
                   and head['repo']['full_name'] == 'olegmed1-art/bridge-video-free'
                   and pr['base']['repo']['full_name'] == 'olegmed1-art/bridge-video-free'
                   and self.queue.current(request))
        return dict(current=bool(current), open=pr['state']=='open', head_sha=head['sha'])
