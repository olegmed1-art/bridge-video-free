"""Crash-conservative protocol. Backends implement only the fixed four-service scope."""
import hashlib
import json

TARGETS = ('synthetic-lab.service', 'synthetic-lab-control-bridge.service',
           'synthetic-lab-control.service', 'synthetic-lab-observer.service')
TERMINAL = {'COMPLETED', 'FAILED', 'CANCELLED'}

class Refused(Exception):
    pass

def require(value, code):
    if not value:
        raise Refused(code)

def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()

def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()

def queue_gate(receipt, baseline_sha, now):
    require(set(receipt) == {'project','branch','database','observed_at','counts','baseline_sha'}, 'QUEUE_RECEIPT_SHAPE')
    require(receipt['project'] == 'synthetic-project' and
            receipt['branch'] == 'synthetic-branch' and receipt['database'] == 'syntheticdb', 'QUEUE_SCOPE')
    require(receipt['baseline_sha'] == baseline_sha, 'QUEUE_BASELINE')
    require(type(receipt['observed_at']) in (float,int) and 0 <= now-receipt['observed_at'] <= 10,
            'QUEUE_RECEIPT_STALE')
    require(set(receipt['counts']) == {'lab','control'}, 'QUEUE_TABLES')
    for counts in receipt['counts'].values():
        require(type(counts) is dict and all(type(n) is int and n >= 0 for n in counts.values()), 'QUEUE_COUNTS')
        require(all(status in TERMINAL or n == 0 for status,n in counts.items()), 'QUEUE_WORK_PRESENT')

def copy_gate(receipt, manifest):
    require(set(receipt) == {'version','baseline_sha','operation','guest_identity','backup_identity',
                           'location','verified_sha','durable'}, 'COPY_RECEIPT_SHAPE')
    require(receipt['version'] == 2 and receipt['durable'] is True, 'COPY_NOT_DURABLE')
    require(receipt['baseline_sha'] == digest(manifest) == receipt['verified_sha'], 'COPY_HASH')
    require(receipt['operation'] == manifest['operation'] and
            receipt['guest_identity'] == manifest['identity'], 'COPY_SCOPE')
    require(isinstance(receipt['backup_identity'],str) and len(receipt['backup_identity']) == 64
            and receipt['backup_identity'] != manifest['identity'], 'COPY_NOT_OFFHOST')
    require(isinstance(receipt['location'],str) and receipt['location'].startswith('/'), 'COPY_LOCATION')

def classify(manifest, current, attempted):
    """Pure observation. No command or automatic retry, even after a crash."""
    if current['identity'] != manifest['identity'] or current['config'] != manifest['config']:
        return 'DRIFT_UNKNOWN'
    if current['jobs']:
        return 'JOBS_PENDING_UNKNOWN'
    original = manifest['links']
    links = current['links']
    if any(path not in original or target != original[path] for path,target in links.items()):
        return 'LINK_CONFLICT_UNKNOWN'
    stopped = all(current['states'].get(u) in ('inactive','failed') for u in TARGETS)
    if not stopped:
        return 'ACTIVE_OR_RESTARTED_UNKNOWN' if attempted else 'PREPARED_ONLY'
    if links == original:
        return 'STOPPED_ORIGINAL_LINKS'
    if not links:
        return 'STOPPED_LINKS_REMOVED'
    return 'PARTIAL_DISABLE'

class Protocol:
    def __init__(self, manifest, store, backend, clock, fault=lambda point: None):
        self.m, self.s, self.b, self.clock, self.fault = manifest, store, backend, clock, fault

    def record(self, name, value):
        self.fault('before_record:' + name)
        self.s.once(name, value)
        self.fault('after_record:' + name)

    def apply(self, copy_receipt, queue_receipt):
        require(not self.s.attempted(), 'APPLY_ALREADY_ATTEMPTED_RECONCILE_ONLY')
        copy_gate(copy_receipt, self.m)
        current = self.b.preflight()
        require(current['identity'] == self.m['identity'] and current['boot'] == self.m['boot'], 'BOOT_OR_HOST_CHANGED')
        require(current['config'] == self.m['config'] and current['links'] == self.m['links'], 'BASELINE_DRIFT')
        require(current['bindings'] == self.m['bindings'], 'RUNTIME_IDENTITY_DRIFT')
        require(not current['jobs'], 'EXISTING_SYSTEMD_JOBS')
        # The trusted backend may obtain fresh read-only evidence AFTER preflight.
        # JSON receipts remain supported for deterministic fixtures and old callers.
        if callable(queue_receipt):
            queue_receipt = queue_receipt(digest(self.m))
        queue_gate(queue_receipt, digest(self.m), self.clock())
        self.b.reserve(45)
        # Global, durable, never removed: a different operation id cannot replay.
        self.fault('before_claim')
        self.s.claim({'operation':self.m['operation'], 'baseline_sha':digest(self.m)})
        self.fault('after_claim')
        self.record('stop-intent', {'targets':list(TARGETS), 'copy':copy_receipt,'queue':queue_receipt})
        self.b.local_idle()
        self.b.pre_stop(self.m['bindings'])
        queue_gate(queue_receipt, digest(self.m), self.clock())
        self.fault('before_stop')
        self.b.stop_once()
        self.fault('after_stop')
        self.record('stop-client-returned', {'meaning':'SUBMISSION_ONLY_NOT_COMPLETION'})
        current = self.b.wait_stopped(15)
        require(classify(self.m,current,True) == 'STOPPED_ORIGINAL_LINKS', 'STOP_OUTCOME_UNKNOWN')
        self.record('stop-observed', {'states':current['states'],'jobs':current['jobs']})
        # A queued local observer job blocks disabling; no job is deleted/cancelled.
        self.b.local_idle()
        self.b.reserve(20)
        for number,(path,target) in enumerate(sorted(self.m['links'].items())):
            self.b.assert_stopped()
            self.record('unlink-%02d-intent'%number, {'path':path,'target':target})
            self.fault('before_unlink:%d'%number)
            self.b.unlink_exact(path,target)
            self.fault('after_unlink:%d'%number)
            self.record('unlink-%02d-observed'%number, {'absent':True})
        self.record('reload-intent', {'operation':'daemon-reload'})
        self.fault('before_reload')
        self.b.reload_once()
        self.fault('after_reload')
        current = self.b.observe()
        require(classify(self.m,current,True) in ('STOPPED_LINKS_REMOVED','STOPPED_ORIGINAL_LINKS')
                and not current['links'], 'DISABLE_READBACK_UNKNOWN')
        require(all(current['enabled'].get(u) in ('disabled','static') for u in TARGETS), 'ENABLEMENT_READBACK_UNKNOWN')
        self.record('complete', {'state':'DISABLED_VERIFIED','observation':current})
        return {'state':'DISABLED_VERIFIED','baseline_sha':digest(self.m)}

    def reconcile(self):
        current = self.b.observe()
        attempted=self.s.attempted()
        if attempted and self.s.claim_value() != {'operation':self.m['operation'],'baseline_sha':digest(self.m)}:
            return {'state':'CLAIM_CONFLICT_UNKNOWN','apply_replay_allowed':False}
        return {'state':classify(self.m,current,attempted),'observation':current,
                'apply_replay_allowed':False,'baseline_sha':digest(self.m)}

    def restore(self, copy_receipt):
        copy_gate(copy_receipt,self.m)
        require(self.s.attempted(), 'NO_APPLY_ATTEMPT')
        require(self.s.claim_value()=={'operation':self.m['operation'],'baseline_sha':digest(self.m)},'CLAIM_CONFLICT')
        current = self.b.observe()
        state = classify(self.m,current,True)
        require(state in ('STOPPED_ORIGINAL_LINKS','STOPPED_LINKS_REMOVED','PARTIAL_DISABLE'),
                'RESTORE_BLOCKED_' + state)
        self.b.reserve(20)
        # Idempotent exact links: never overwrite, never stop/start/enable/replay apply.
        for number,(path,target) in enumerate(sorted(self.m['links'].items())):
            self.b.assert_stopped()
            self.s.ensure('restore-%02d-intent'%number, {'path':path,'target':target})
            self.fault('before_restore:%d'%number)
            self.b.restore_exact(path,target)
            self.fault('after_restore:%d'%number)
        self.fault('before_restore_reload')
        self.b.reload_once()
        self.fault('after_restore_reload')
        current = self.b.observe()
        require(classify(self.m,current,True) == 'STOPPED_ORIGINAL_LINKS', 'RESTORE_READBACK_UNKNOWN')
        return {'state':'CONFIG_RESTORED_CONSUMERS_NOT_STARTED','observation':current}
