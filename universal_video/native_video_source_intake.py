"""Local content identity from retained receipts, NEVER task/lease authority.

New revision semantics: sha256:<original video bytes SHA-256>. This does not
change old receipts or the closed Assignment ABI. Data validation cannot prove
current bytes, Drive immutability, accepted intake, native ownership or a lease.
The original provider boundary has no successful path until its real owning
callsite supplies authenticated source/task/claim/native binding.
"""
from dataclasses import dataclass
import re

from . import diana20_transport_protocol as p
from .receipt_encoding import canonical_bytes, strict_loads, sha256
from .terminal_finish_contract import Assignment


class SourceIntakeRefused(RuntimeError):
    pass


def need(ok, code):
    if not ok:
        raise SourceIntakeRefused(code) from None


FIELDS = {'schema','source_file_id','source_sha256','size_bytes',
          'intake_source_revision','evidence'}
EVIDENCE = {'kind','source_read_receipt_sha256','frame_manifest_sha256'}


@dataclass(frozen=True)
class RetainedVideoSource:
    """Canonical SOURCE DATA; neither accepted intake nor an issuance ticket."""
    raw: bytes

    def __post_init__(self):
        try:
            value=strict_loads(self.raw,max_bytes=4096)
            need(type(value) is dict and set(value)==FIELDS
                 and value['schema']=='native-video-content-source/v1'
                 and type(value['source_file_id']) is str
                 and re.fullmatch('[A-Za-z0-9_-]{10,200}',value['source_file_id'])
                 and p.pin(value['source_sha256'])
                 and type(value['size_bytes']) is int and 0<value['size_bytes']<=2**53
                 and value['intake_source_revision']=='sha256:'+value['source_sha256'],
                 'VIDEO_SOURCE_CONTENT_CONTRACT')
            evidence=value['evidence']
            need(type(evidence) is dict and set(evidence)==EVIDENCE
                 and evidence['kind']=='RETAINED_RECEIPTS_ONLY'
                 and all(p.pin(evidence[k]) for k in EVIDENCE-{'kind'})
                 and canonical_bytes(value,max_bytes=4096)==self.raw,
                 'VIDEO_SOURCE_RECEIPT_BINDING')
        except Exception:
            raise SourceIntakeRefused('VIDEO_SOURCE_CONTRACT_REFUSED') from None

    @property
    def value(self):
        return strict_loads(self.raw,max_bytes=4096)

    @property
    def sha256(self):
        return sha256(self.raw)

    @classmethod
    def from_diana20_receipts(cls, source_read_raw, frame_manifest_raw, *,
                              expected_read_sha256, expected_frame_sha256):
        """Check exact retained bytes/pins; no IO and no fresh source assertion.

        Expected hashes are explicitly supplied integrity inputs, not enrolled
        authentication. An APPROVED read receipt authorizes no task or execution.
        """
        try:
            need(p.pin(expected_read_sha256) and p.pin(expected_frame_sha256)
                 and type(source_read_raw) is bytes and 0<len(source_read_raw)<=16384
                 and type(frame_manifest_raw) is bytes and 0<len(frame_manifest_raw)<=131072
                 and sha256(source_read_raw)==expected_read_sha256
                 and sha256(frame_manifest_raw)==expected_frame_sha256,
                 'VIDEO_SOURCE_EXACT_RECEIPT_BYTES')
            read=strict_loads(source_read_raw,max_bytes=16384)
            frames=strict_loads(frame_manifest_raw,max_bytes=131072)
            need(type(read) is dict and type(frames) is dict
                 and read.get('schema')=='diana20-approved-source-read/v1'
                 and read.get('status')=='APPROVED'
                 and read.get('scope')=='NORMAL_READ_RETAINED_DIANA20_SOURCE_TO_PRIVATE_WORKCOPY'
                 and read.get('verification_status')=='BYTE_IDENTITY_AND_METADATA_VERIFIED_ONLY'
                 and frames.get('schema')=='diana20-card-evidence-export-v1',
                 'VIDEO_SOURCE_RETAINED_RECEIPT_REQUIRED')
            source=frames['source']
            need(type(source) is dict and source.get('mimeType')=='video/mp4'
                 and source.get('sha256')==read.get('source_sha256')
                 and type(source.get('sizeBytes')) is int
                 and type(read.get('size_bytes')) is int
                 and source['sizeBytes']==read['size_bytes'], 'VIDEO_SOURCE_RECEIPTS_DISAGREE')
            return cls(canonical_bytes(dict(schema='native-video-content-source/v1',
                source_file_id=source['driveId'],source_sha256=source['sha256'],size_bytes=source['sizeBytes'],
                intake_source_revision='sha256:'+source['sha256'],evidence=dict(
                    kind='RETAINED_RECEIPTS_ONLY',source_read_receipt_sha256=expected_read_sha256,
                    frame_manifest_sha256=expected_frame_sha256)),max_bytes=4096))
        except Exception:
            raise SourceIntakeRefused('VIDEO_SOURCE_RETAINED_RECEIPTS_REFUSED') from None

    def check_assignment_data(self, assignment, claim_data):
        """Compatibility check only; successful None is NOT an authenticated ABI.

        Reuse the original Assignment validator. No provider objects, lease
        freshness, COMMIT proof, worker identity, native handles, DB authorization
        or accepted task are established.
        """
        try:
            need(type(assignment) is Assignment and type(claim_data) is dict,
                 'VIDEO_SOURCE_ASSIGNMENT_DATA_REQUIRED')
            a=assignment.value
            source=self.value
            assignment.check_claim(claim_data)
            need(a['video']['source_file_id']==source['source_file_id']
                 and a['source_sha256']==source['source_sha256']
                 and a['autopilot']['intake_source_revision']==source['intake_source_revision'],
                 'VIDEO_SOURCE_ASSIGNMENT_CONTENT_CHANGED')
        except Exception:
            raise SourceIntakeRefused('VIDEO_SOURCE_ASSIGNMENT_DATA_REFUSED') from None
