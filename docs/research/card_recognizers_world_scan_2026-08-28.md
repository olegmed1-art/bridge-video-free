# Ready-made playing-card recognizers: adoption scan

Date: 2026-08-28

Scope: ready models and hosted APIs that recognize rank plus suit and could feed Universal Video 3.1.

Decision: benchmark two challengers, but do not make either canonical without a bridge-video gold-set gate and a license/privacy gate.

## What “usable” means here

A generic card detector returns a card code and bounding box. Universal Video additionally needs to assign the box to a bridge hand, combine evidence across frames, accept a played card that becomes visible, fuse attributable teacher speech, and derive a hidden hand only from a proven deck complement. Those bridge rules remain in the school-owned deterministic layer; no external model is allowed to infer them.

## Shortlist

| Candidate | Delivery | Cost | License / data boundary | Decision |
|---|---|---|---|---|
| `sroot/lgd-cards-gen3` | Ready `model.onnx`, 52 rank+suit classes, ONNX Runtime | No inference fee on Oracle | Weights are AGPL-3.0 because they derive from Ultralytics YOLO11. The publisher reports recall 0.847 and precision proxy 0.771 on a private 225-frame real-video holdout; these are not independently validated bridge metrics. | Best technical local challenger, but **disabled** until an AGPL/Enterprise-license decision. |
| `playing-cards-pzvb1/1` on Roboflow | Ready hosted HTTP API, 52 classes | Public plan: free, 15 credits/month. Core: listed at $99 monthly or $79/month billed annually; usage consumes credits. | Dataset is labeled CC BY 4.0; hosted-service and model-use terms remain separate. Frames leave Oracle. Public-plan work is public; private data requires a paid workspace. | Best zero-install benchmark on a small, approved frame set; **not** the default production path. |
| Roboflow `playing-cards-ow27d` family | Ready public pretrained model/API and synthetic 52-card dataset | Same Roboflow credit model | Strong synthetic baseline, but published evidence warns of a synthetic-to-real gap. | Useful secondary baseline and training seed, not sufficient alone. |
| `sroot/lgd-cards-gen4` | Ready ONNX corner-pip detector | No inference fee on Oracle | AGPL-3.0. Publisher says it was reverted after gameplay regressions and is only a deck-spread specialist. | Reject as primary; optional stress-test challenger only. |
| Hamza-Asif `Playing-Card-Detection-YOLOv8` | Ready `best.onnx`, 52 classes | No inference fee | Ultralytics-derived and repository has no clear reusable license in the inspected project page. Dataset-distribution metrics do not establish bridge-video performance. | Do not import. |
| EdjeElectronics OpenCV detector | Ready source/templates | No inference fee | No clear license in the inspected repository; requires isolated cards on a dark background and old dependencies. | Not suitable for lesson video. |
| LandingLens poker-card tutorial | Hosted example | Commercial cloud service | Demonstration recognizes suits, not a complete 52-card rank+suit deal. Frames leave Oracle. | Not suitable as the main recognizer. |

General Google Cloud Vision, AWS Rekognition and Azure AI Vision APIs were not shortlisted: they are ready commercial vision/OCR APIs, but their public offerings are not specialized 52-class playing-card recognizers. Paying per sampled frame would add cost and data transfer without removing the bridge-specific validation work.

## Adoption decision

1. Keep the production interface detector-neutral and keep all deal logic outside the detector.
2. First benchmark the Roboflow model on a small owner-approved set because it needs no model installation.
3. In parallel, benchmark LGD gen3 locally only after license approval. ONNX Runtime itself is MIT; that does not remove the AGPL obligation attached to the weights.
4. Do not download, vendor, or deploy AGPL or unlicensed weights in the current repository by default.
5. Select a recognizer only if it beats the existing baseline on a frozen bridge-video gold set and does not increase false exact-card facts.

## Required bridge-video gate

The frozen evaluation set must include:

- one, two and three visible hands;
- 38, 39 and 40 visible-card states;
- a card newly exposed by play;
- overlapped cards and tiny corner indices;
- all four missing-seat positions;
- visual/speech conflicts and incomplete spoken rank/suit constraints;
- duplicate detections of the two corners of the same physical card;
- multiple screen layouts and compression levels.

Primary metrics are exact-card precision, exact-card recall, cross-seat error rate, duplicate-card rate and false-complete-deal rate. A candidate may improve recall only if it does not invent an exact card. The canonical 39-card complement, played-card preservation and speech-constraint resolution are evaluated separately from detector accuracy.

## Sources

- LGD gen3 model card and downloadable ONNX: https://huggingface.co/sroot/lgd-cards-gen3
- LGD gen4 model card and documented rollback: https://huggingface.co/sroot/lgd-cards-gen4
- Roboflow 52-class API model: https://universe.roboflow.com/playing-card-recognition/playing-cards-pzvb1
- Roboflow playing-card baseline: https://universe.roboflow.com/augmented-startups/playing-cards-ow27d
- Roboflow pricing: https://roboflow.com/pricing
- Ultralytics license terms: https://www.ultralytics.com/license
- ONNX Runtime license: https://github.com/microsoft/onnxruntime/blob/main/LICENSE
- Hamza-Asif ready ONNX project: https://github.com/Hamza-Asif-ai/Playing-Card-Detection-YOLOv8
- EdjeElectronics template detector: https://github.com/EdjeElectronics/OpenCV-Playing-Card-Detector
- LandingLens card-suit tutorial: https://landinglens.docs.landing.ai/python-tutorial
