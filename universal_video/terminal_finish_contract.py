"""Exact post-finish identities and evidence checks; hashes confer no authority."""
from dataclasses import dataclass
import re
from uuid import UUID

from .receipt_encoding import PROFILE, canonical_bytes, strict_loads, sha256, json_sha256
from .private_result_handoff import PrivateOneJobConfigurationError

UNIT = 'school-autopilot-production-light.service'
ASSIGNMENT_SCHEMA = 'diana20-owner-assignment/v1'
SELECTOR_SCHEMA = 'diana20-terminal-selector/v1'
RECEIPT_SCHEMA = 'diana20-terminal-finish/v1'
PINS = ('source_sha256','context_sha256','request_sha256','generation_sha256',
        'manifest_sha256','source_fingerprint','production_source_sha256',
        'unit_sha256','binding_sha256')
VIDEO_KEYS = ('job_id','batch_id','source_file_id','lease_token','attempt_count',
              'worker_key','stable_job_key','algorithm_revision','output_folder_id')
AUTOPILOT_KEYS = ('task_id','worker_id','lease_epoch','intake_source_revision')
BUNDLE_KEYS = ('status','receipt_id','bundle_sha256','master_json_sha256','ai_done_sha256')
ARTIFACT_KEYS = ('ai_done_drive_id','ai_done_sha256','master_pdf_drive_id',
                 'master_pdf_sha256','artifact_manifest_sha256','terminal_evidence_sha256')


class TerminalFinishRefused(PrivateOneJobConfigurationError):
    error_code = 'POST_FINISH_UNKNOWN_NO_RETRY'


def require(condition,code):
    if not condition:raise TerminalFinishRefused(code)


def _uuid(value):
    try:return type(value) is str and str(UUID(value))==value
    except (ValueError,TypeError,AttributeError):return False


def _pin(value):return type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None


@dataclass(frozen=True)
class Assignment:
    """Detached pinned INPUT, not proof of enrollment or an issued grant."""
    raw: bytes

    def __post_init__(self):
        value = strict_loads(self.raw,max_bytes=16384)
        require(type(value) is dict and set(value)==set(PINS)|{
            'schema','unit','autopilot','video','admission_id','policy_id','policy_epoch'},'ASSIGNMENT_FIELDS')
        require(value['schema']==ASSIGNMENT_SCHEMA and value['unit']==UNIT,'ASSIGNMENT_UNIT')
        require(all(_pin(value[k]) for k in PINS),'ASSIGNMENT_PIN')
        require(_uuid(value['admission_id']) and type(value['policy_id']) is str
                and 0<len(value['policy_id'])<=200 and type(value['policy_epoch']) is int
                and value['policy_epoch']>0,'ASSIGNMENT_POLICY')
        a,v = value['autopilot'],value['video']
        require(type(a) is dict and set(a)==set(AUTOPILOT_KEYS) and _uuid(a['task_id'])
                and type(a['lease_epoch']) is int and a['lease_epoch']>0
                and all(type(a[k]) is str and 0<len(a[k])<=200
                        for k in ('worker_id','intake_source_revision')),'AUTOPILOT_IDENTITY')
        require(type(v) is dict and set(v)==set(VIDEO_KEYS)
                and all(_uuid(v[k]) for k in ('job_id','batch_id','lease_token'))
                and type(v['attempt_count']) is int and 1<=v['attempt_count']<=3
                and all(type(v[k]) is str and 0<len(v[k])<=200 for k in
                        ('worker_key','algorithm_revision'))
                and type(v['stable_job_key']) is str and re.fullmatch('[0-9a-f]{32}',v['stable_job_key']) is not None
                and all(type(v[k]) is str and re.fullmatch('[A-Za-z0-9_-]{10,200}',v[k])
                        for k in ('source_file_id','output_folder_id')),'VIDEO_IDENTITY')
        require(canonical_bytes(value,max_bytes=16384)==self.raw,'ASSIGNMENT_NONCANONICAL')

    @classmethod
    def from_mapping(cls,value):return cls(canonical_bytes(dict(value),max_bytes=16384))

    @property
    def value(self):return strict_loads(self.raw,max_bytes=16384)

    @property
    def sha256(self):return sha256(self.raw)

    @property
    def selector_sha256(self):
        v=self.value['video']
        return json_sha256(dict(schema=SELECTOR_SCHEMA,assignment_sha256=self.sha256,
            job_id=v['job_id'],attempt_count=v['attempt_count'],
            former_video_lease_token=v['lease_token'],generation_sha256=self.value['generation_sha256']))

    def check_claim(self,claim,worker_key=None):
        v=self.value['video']
        require(all(type(claim.get(k)) is type(v[k]) and claim[k]==v[k]
                    for k in VIDEO_KEYS if k!='worker_key'),'ASSIGNMENT_CLAIM_CHANGED')
        if worker_key is not None:require(worker_key==v['worker_key'],'ASSIGNMENT_WORKER_CHANGED')


def checked_bundle(bundle):
    require(type(bundle) is dict and set(bundle)==set(BUNDLE_KEYS)
            and bundle['status']=='PRIVATE_RESULT_STAGED' and _uuid(bundle['receipt_id'])
            and all(_pin(bundle[k]) for k in BUNDLE_KEYS if k not in ('status','receipt_id')),
            'PRIVATE_BUNDLE_BINDING')
    return strict_loads(canonical_bytes(bundle,max_bytes=4096),max_bytes=4096)


def project_private_output(selected,bundle):
    """The actual 0062 stage response has five fields, all retained in overlay."""
    require(type(selected) is dict and selected.get('result_mode')=='SHADOW_REVIEW_ONLY'
            and selected.get('database_persistence_allowed') is False
            and selected.get('canonical_promotion_allowed') is False
            and 'private_staging' not in selected,'SELECTED_PRE_OVERLAY_REQUIRED')
    result = strict_loads(canonical_bytes(selected))
    result.update(result_mode='PRIVATE_RESULT_STAGING_ONLY',database_persistence_allowed=True,
                  private_staging=checked_bundle(bundle))
    canonical_bytes(result)
    return result


def checked_queue_result(value):
    require(type(value) is dict and set(value)=={'job_status','batch_status','released_jobs'}
            and value['job_status']=='REVIEW_READY' and value['batch_status'] in ('RUNNING','REVIEW')
            and type(value['released_jobs']) is int and value['released_jobs']==0,'QUEUE_RESULT_BINDING')
    return value


def verify_terminal_response(response,*,assignment,selected,bundle):
    """Verify DB result bytes. The SQL principal/grant check supplies authority."""
    require(type(assignment) is Assignment,'ASSIGNMENT_REQUIRED')
    require(type(response) is dict and set(response)=={'status','receipt_text','receipt_sha256'}
            and response['status']=='COMMITTED_TERMINAL_RESULT'
            and type(response['receipt_text']) is str,'TERMINAL_RESPONSE')
    canonical_bytes(response,max_bytes=65536)
    raw=response['receipt_text'].encode('utf-8')
    require(2<=len(raw)<=65536 and sha256(raw)==response['receipt_sha256'],'TERMINAL_RECEIPT_HASH')
    body=strict_loads(raw,max_bytes=65536)
    required={'schema','encoding','selector_sha256','assignment_sha256','job_id','attempt_count',
              'former_video_lease_token','source_file_id','generation_sha256','terminal_event_id',
              'selected_terminal_sha256','persisted_output_sha256','queue_result','queue_result_sha256',
              'artifact','private_bundle'}
    v=assignment.value['video']
    require(type(body) is dict and set(body)==required
            and canonical_bytes(body,max_bytes=65536)==raw,'TERMINAL_RECEIPT_FIELDS')
    require(body['schema']==RECEIPT_SCHEMA and body['encoding']==PROFILE
            and body['selector_sha256']==assignment.selector_sha256
            and body['assignment_sha256']==assignment.sha256
            and all(body[k]==v[k] for k in ('job_id','attempt_count','source_file_id'))
            and type(body['attempt_count']) is int
            and body['former_video_lease_token']==v['lease_token']
            and body['generation_sha256']==assignment.value['generation_sha256']
            and type(body['terminal_event_id']) is int and body['terminal_event_id']>0,'TERMINAL_ASSIGNMENT')
    persisted=project_private_output(selected,bundle)
    require(body['selected_terminal_sha256']==json_sha256(selected)
            and body['persisted_output_sha256']==json_sha256(persisted)
            and body['private_bundle']==checked_bundle(bundle)
            and body['artifact']=={k:selected[k] for k in ARTIFACT_KEYS},'TERMINAL_OUTPUT_BINDING')
    checked_queue_result(body['queue_result'])
    require(body['queue_result_sha256']==json_sha256(body['queue_result']),'TERMINAL_QUEUE_HASH')
    return raw,body['queue_result']
