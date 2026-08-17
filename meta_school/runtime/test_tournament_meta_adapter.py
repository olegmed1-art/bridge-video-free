from tournament_meta_adapter import *

def run():
 e=TournamentMetaEvent('e','r','canon-v1','30041','2','1',('official-card','pbn'),'explicit_pair_card','PLAYED')
 assert e.validate()
 assert not claim_allowed(board_status='AVERAGE',claim_type='PERSONAL_ERROR')
 assert not claim_allowed(board_status='NOT_PLAYED',claim_type='PERSONAL_ERROR')
 assert not claim_allowed(board_status='PLAYED',claim_type='SPECIFIC_BIDDING_ERROR',auction_evidence=False)
 assert claim_allowed(board_status='PLAYED',claim_type='SPECIFIC_BIDDING_ERROR',auction_evidence=True)
 assert not claim_allowed(board_status='PLAYED',claim_type='SPECIFIC_MIDPLAY_ERROR',play_evidence=False)
 assert claim_allowed(board_status='PLAYED',claim_type='SPECIFIC_MIDPLAY_ERROR',play_evidence=True)
 assert not claim_allowed(board_status='PLAYED',claim_type='STUDENT_ATTRIBUTION',identity_basis=False)
 assert classify_tournament_meta_case(actual_analysis=True,direct_tournament_command=False)=='DENY_START'
 assert classify_tournament_meta_case(canonical_change=True)=='OWNER_REVIEW'
 assert classify_tournament_meta_case(source_conflict=True)=='RETEST_BLOCK_FIELD'
 assert classify_tournament_meta_case(no_change=True)=='NO_CHANGE'
 assert classify_tournament_meta_case(dependency_ok=False)=='REJECT'
 assert A1_STABLE_WRITE_ALLOWED is False
 assert A1_ORIGINAL_SOURCE_MUTATION_ALLOWED is False
 print('TOURNAMENT_META_A1_REGRESSION_PASS')
if __name__=='__main__': run()
