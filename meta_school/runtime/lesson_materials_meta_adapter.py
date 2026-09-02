from dataclasses import dataclass

@dataclass(frozen=True)
class MaterialClaim:
    text:str
    claim_class:str  # CANON | OWNER_INSTRUCTION | PROPOSAL
    source_id:str|None=None

    def validate(self):
        if self.claim_class not in ('CANON','OWNER_INSTRUCTION','PROPOSAL'):
            raise ValueError('invalid claim class')
        if self.claim_class in ('CANON','OWNER_INSTRUCTION') and not self.source_id:
            raise ValueError('authoritative claim requires source')
        return True

def terminology_ok(text:str)->bool:
    # School Russian terminology: use «торговля», not «аукцион».
    return 'аукцион' not in text.lower()

def material_change_decision(*, semantic_change=False, bidding_system_change=False, methodology_change=False, source_missing=False, source_conflict=False, shared_template_change=False, technical_only=False):
    if bidding_system_change or methodology_change or semantic_change:
        return 'OWNER_REVIEW_R4'
    if source_missing:
        return 'ASK_OWNER_OR_MARK_UNKNOWN'
    if source_conflict:
        return 'CONFLICTED_RETEST'
    if shared_template_change:
        return 'ESCALATE_R2'
    if technical_only:
        return 'R1_CANDIDATE_ELIGIBLE'
    return 'NO_CHANGE'

A1_CANON_WRITE_ALLOWED=False
A1_SOURCE_DELETE_ALLOWED=False
A1_TEMPLATE_WRITE_ALLOWED=False
