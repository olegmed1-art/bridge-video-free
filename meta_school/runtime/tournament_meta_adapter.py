from dataclasses import dataclass
from typing import Optional
UNKNOWN='UNKNOWN'

@dataclass(frozen=True)
class TournamentMetaEvent:
    event_id:str; run_id:str; stable_id:str; tournament_id:str; session_id:str; board_id:str
    source_ids:tuple; pair_identity_basis:str; board_status:str; deal_hash:Optional[str]=None
    dealer:Optional[str]=None; vulnerability:Optional[str]=None; contract:Optional[str]=None
    declarer:Optional[str]=None; opening_lead:Optional[str]=None; result:Optional[str]=None
    score:Optional[float]=None; percentage_or_imps:Optional[float]=None
    trade_evidence_status:str=UNKNOWN; play_evidence_status:str=UNKNOWN
    dds_evidence_ids:tuple=(); interpretation_status:str=UNKNOWN; layout_version:str=UNKNOWN
    qc_status:str=UNKNOWN; evidence_ids:tuple=()

    def validate(self):
        if not all((self.event_id,self.run_id,self.stable_id,self.tournament_id,self.session_id,self.board_id,self.source_ids)):
            raise ValueError('missing provenance')
        if self.board_status not in ('PLAYED','AVERAGE','NOT_PLAYED','MISSING_DATA'):
            raise ValueError('invalid board status')
        if not self.pair_identity_basis:
            raise ValueError('missing identity basis')
        return True

def claim_allowed(*, board_status, claim_type, trade_evidence=False, play_evidence=False, identity_basis=True):
    if not identity_basis and claim_type in ('PERSONAL_ERROR','STUDENT_ATTRIBUTION'): return False
    if board_status in ('AVERAGE','NOT_PLAYED') and claim_type=='PERSONAL_ERROR': return False
    if claim_type=='SPECIFIC_BIDDING_ERROR' and not trade_evidence: return False
    if claim_type=='SPECIFIC_MIDPLAY_ERROR' and not play_evidence: return False
    return True

def classify_tournament_meta_case(*, canonical_change=False, source_conflict=False, no_change=False, dependency_ok=True, direct_tournament_command=False, actual_analysis=False):
    if actual_analysis and not direct_tournament_command: return 'DENY_START'
    if canonical_change: return 'OWNER_REVIEW'
    if source_conflict: return 'RETEST_BLOCK_FIELD'
    if no_change: return 'NO_CHANGE'
    if not dependency_ok: return 'REJECT'
    return 'SHADOW_CANDIDATE_ELIGIBLE'

A1_STABLE_WRITE_ALLOWED=False
A1_ORIGINAL_SOURCE_MUTATION_ALLOWED=False
