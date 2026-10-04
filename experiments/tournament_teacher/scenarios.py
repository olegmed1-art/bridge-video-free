"""Hand-written synthetic cases, independent of executable rule JSON.

Shape order: spades, hearts, diamonds, clubs. Numeric premises are school test
points, never hand valuations. Each case is evaluated through the public consumer.
"""
from .consumer import MODE, PROFILE

# suffix, decision, partnership auction, call, positive shape, points, forcing,
# negative shape (None for a numeric-only rule), lower and upper point boundaries.
CASES = [
    ("1D-1NT-REBID-2H","02-001","1D 1NT","2H",(2,4,5,2),18,"FG",(2,4,2,5),18,None),
    ("1D-1NT-REBID-2S","02-001","1D 1NT","2S",(4,2,5,2),18,"FG",(4,2,2,5),18,None),
    ("1D-1NT-REBID-PASS","02-002","1D 1NT","PASS",(3,3,5,2),12,"NF",None,12,14),
    ("1S-RSP-2C","02-003","1S","2C",(2,3,4,4),11,"F1",(2,3,5,3),11,None),
    ("1S-RSP-2D","02-003","1S","2D",(2,3,4,4),11,"F1",(2,3,3,5),11,None),
    ("1S-RSP-2H","02-003","1S","2H",(2,5,3,3),11,"F1",(2,4,3,4),11,None),
    ("1H-1NT-REBID-2S","02-004","1H 1NT","2S",(4,5,2,2),18,"FG",(3,5,3,2),18,None),
    ("1S-1NT-REBID-2S","02-005","1S 1NT","2S",(6,3,2,2),15,"NF",(5,3,3,2),12,15),
    ("1S-1NT-REBID-3S","02-005","1S 1NT","3S",(6,3,2,2),16,"INV",(5,3,3,2),16,17),
    ("1C-1H-REBID-2C","02-006","1C 1H","2C",(2,2,4,5),12,"NF",(2,3,3,5),12,15),
    ("1C-1H-REBID-3C","02-006","1C 1H","3C",(2,2,3,6),16,"NF",(2,2,4,5),16,17),
    ("1C-1H-REBID-1S","02-007","1C 1H","1S",(4,2,2,5),12,"F1",(4,4,0,5),12,22),
    ("OPEN-1NT","02-008","","1NT",(3,3,2,5),15,None,(5,3,3,2),15,17),
    ("OPEN-1H","02-008","","1H",(3,5,3,2),16,None,(3,4,3,3),12,22),
    ("OPEN-1S","02-008","","1S",(5,3,3,2),16,None,(4,3,3,3),12,22),
    ("1H-1NT-REBID-2NT","02-008","1H 1NT","2NT",(3,5,3,2),16,None,(4,5,2,2),15,17),
    ("1S-1NT-REBID-2NT","02-008","1S 1NT","2NT",(5,3,3,2),16,None,(5,4,2,2),15,17),
    ("OPEN-2NT","02-009","","2NT",(3,3,2,5),20,None,(5,3,3,2),20,22),
    ("1D-1H-1S-RSP-3C","02-010","1D 1H 1S","3C",(2,4,2,5),13,"FG",(2,5,1,5),13,None),
    ("1D-1H-1NT-RSP-3C","02-010","1D 1H 1NT","3C",(2,4,2,5),13,"FG",(2,5,1,5),13,None),
    ("2S-RSP-3C","03-001","2S","3C",(2,3,3,5),17,"FG",(2,3,4,4),17,None),
    ("2S-RSP-3D","03-001","2S","3D",(2,3,5,3),17,"FG",(2,3,4,4),17,None),
    ("1NT-RSP-3H","03-002","1NT","3H",(3,1,4,5),None,"FG",(2,2,4,5),None,None),
    ("1NT-RSP-3S","03-002","1NT","3S",(1,3,4,5),None,"FG",(2,2,4,5),None,None),
    ("1NT-RSP-2C","03-003","1NT","2C",(4,3,3,3),8,None,(3,3,4,3),8,None),
    ("1NT-RSP-2NT","03-003","1NT","2NT",(4,3,3,3),8,"INV",None,8,9),
]


def make_request(case, *, shape=None, points="DEFAULT"):
    suffix, decision, partnership, call, original_shape, p, forcing, negative, low, high = case
    auction = [item for call in partnership.split() for item in (call,"PASS")]
    shape = original_shape if shape is None else shape
    points = p if points == "DEFAULT" else points
    result = dict(mode=MODE, system_profile=PROFILE, task="assess_call", dealer="N",
                  actor="NESW"[len(auction)%4], auction=auction, proposed_call=call,
                  hand=[s+r for s,n in zip("SHDC",shape) for r in "23456789TJQKA"[:n]])
    if points is not None:
        result["school_points"] = {"value":points,"basis":"SYNTHETIC_SCHOOL_POINTS"}
    if partnership == "2S":
        result["opening_meaning"] = "WEAK_2S"
    return result


def scenarios(case):
    yield "positive", make_request(case), "SUPPORTED"
    if case[7] is not None:
        yield "negative_shape", make_request(case,shape=case[7]), "CONTRADICTED"
    if case[8] is not None:
        yield "negative_points", make_request(case,points=case[8]-1), "CONTRADICTED"
        yield "boundary_lower", make_request(case,points=case[8]), "SUPPORTED"
        yield "missing_points", make_request(case,points=None), "ABSTAIN"
    else:
        # Exact singleton boundary; no numeric threshold exists.
        yield "boundary_singleton", make_request(case), "SUPPORTED"
    if case[9] is not None:
        yield "boundary_upper", make_request(case,points=case[9]), "SUPPORTED"
        yield "outside_upper", make_request(case,points=case[9]+1), "CONTRADICTED"
    yield "hidden_information", {**make_request(case),"partner_hand":["SA"]}, "ABSTAIN"
    yield "wrong_turn", {**make_request(case),"actor":"E"}, "ABSTAIN"
