# DDS3 screenshot regression corpus

This directory defines the corpus contract; images may be stored externally when privacy/licensing requires it.

Each case must contain canonical truth independent of DDS3 output:
- image SHA-256
- source/layout family
- board number, dealer, vulnerability
- exact N/E/S/W hands
- visibility/quality tags
- expected accept/reject status
- ambiguous fields if rejection is expected

Metrics:
- exact-deal accuracy (all 52 cards correct)
- board/dealer/vulnerability accuracy
- false-accept rate on ambiguous images
- correct-rejection rate

Required coverage before declaring production vision complete: >=50 distinct real screenshots, >=5 layout families, and negative cases for blur, crop, duplicate-looking rank, unreadable card, incomplete diagram and conflicting metadata.

Vision quality is measured separately from DDS3 correctness. No failed recognition case may be repaired by bridge inference before the 52-card validation gate.
