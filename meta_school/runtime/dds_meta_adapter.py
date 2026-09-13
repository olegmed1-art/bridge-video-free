from dataclasses import dataclass, asdict
from typing import Optional

UNKNOWN='UNKNOWN'

@dataclass(frozen=True)
class DDSMetaEvent:
    event_id:str
    run_id:str
    component_id:str
    stable_sha:str
    algorithm_version:str
    solver_identity:str
    deal_id:str
    root_deal_id:str
    task_id:str
    split:str
    fold:str
    input_hash:str
    result_type:str
    dds_value_before:Optional[int]=None
    dds_value_after:Optional[int]=None
    chosen_card:Optional[str]=None
    legal_moves:tuple=()
    optimal_moves:tuple=()
    regret:Optional[int]=None
    first_swing:Optional[int]=None
    gross_loss_or_gift:Optional[int]=None
    recovered_amount:Optional[int]=None
    unrecovered_damage:Optional[int]=None
    line_hash:Optional[str]=None
    qc_status:str=UNKNOWN
    evidence_ids:tuple=()
    elapsed_ms:Optional[int]=None
    cost_class:str='FREE_LOCAL_DDS'

    def validate(self):
        required=(self.event_id,self.run_id,self.component_id,self.stable_sha,self.algorithm_version,self.solver_identity,self.deal_id,self.root_deal_id,self.task_id,self.input_hash,self.result_type)
        if not all(required): raise ValueError('missing required provenance')
        if self.regret is not None and self.regret < 0: raise ValueError('negative regret')
        if self.chosen_card and self.legal_moves and self.chosen_card not in self.legal_moves: raise ValueError('chosen card not legal')
        if self.chosen_card and self.optimal_moves and self.chosen_card in self.optimal_moves and self.regret not in (None,0): raise ValueError('optimal move must have regret 0')
        if self.regret==0 and self.chosen_card and self.optimal_moves and self.chosen_card not in self.optimal_moves: raise ValueError('zero-regret move missing from optimal set')
        if self.unrecovered_damage is not None and self.unrecovered_damage < 0: raise ValueError('negative unrecovered damage')
        return True

    def to_evidence(self):
        self.validate()
        return asdict(self)

def classify_dds_meta_case(*, intentional_legacy_block=False, canonical_change=False, stale=False, insufficient=False, dependency_ok=True, solver_available=True):
    if canonical_change: return 'OWNER_REVIEW'
    if stale: return 'REBASE_REQUIRED'
    if intentional_legacy_block: return 'NO_CHANGE'
    if not solver_available: return 'BLOCKED_UNKNOWN'
    if insufficient: return 'RETEST'
    if not dependency_ok: return 'REJECT'
    return 'SHADOW_CANDIDATE_ELIGIBLE'

A1_STABLE_WRITE_ALLOWED=False
A1_MASS_TRAINING_ALLOWED=False
