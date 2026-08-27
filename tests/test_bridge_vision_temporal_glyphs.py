from bridge_vision.temporal_glyphs import mask_iou,stable_consensus
def test_temporal_consensus_excludes_one_unstable_frame():
 a=[[1,0,1],[1,1,1]];b=[[1,0,1],[1,1,1]];noise=[[0,1,0],[0,1,0]];r=stable_consensus([a,noise,b],min_pair_iou=.9);assert r["status"]=="STABLE";assert r["stable_indices"]==[0,2];assert r["template"]==[[True,False,True],[True,True,True]]
def test_temporal_consensus_fails_closed_without_stable_pair():
 a=[[1,0],[0,0]];b=[[0,1],[0,0]];c=[[0,0],[1,0]];r=stable_consensus([a,b,c],min_pair_iou=.9);assert r["status"]=="UNSTABLE";assert r["template"] is None
def test_temporal_consensus_requires_two_observations():
 r=stable_consensus([[[1]]]);assert r["status"]=="INSUFFICIENT_SUPPORT";assert r["template"] is None
def test_mask_iou_is_exact_for_identical_masks():
 m=[[1,0],[1,1]];assert mask_iou(m,m)==1.0
