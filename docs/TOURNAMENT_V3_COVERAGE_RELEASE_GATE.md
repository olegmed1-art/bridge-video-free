# Tournament Analyzer v3 — coverage/release gate

This module implements the release-boundary parts of the approved **Tournament analysis algorithm v1.4** without adding bridge methodology.

## Normative behavior

- Every **played** board receives at least one planned board slide.
- An explicitly scored episode totals three 0–2 criteria: result impact, educational transferability, and evidence reliability.
- Total **4–6** requires a separate adjacent deep slide; **2–3** stays in the ordinary board analysis; **0–1** is a brief review.
- Episode scores are never invented in this layer. Each episode must arrive pre-scored with provenance.
- Average and unplayed boards remain administrative/non-personal for decision statistics.
- Before export, rendered slide identity and order must match the coverage manifest exactly.
- Final release also requires the existing pre-analysis gate and a passed visual QA result.

## Evidence limitations are not silently upgraded to hard facts

The algorithm v1.4 explicitly permits a report to retain an official MP percentage when a full traveller is unavailable, provided that the percentage is marked as not independently recalculated. Likewise, missing actual auction/full play limits causal attribution but does not erase verified contract/result facts.

Therefore this gate distinguishes:

- **technical analysis ready** — source/structure/score gates passed;
- **full causal replay ready** — actual auction and full play are available where required;
- **full traveller available** — independent MP reconstruction is possible;
- **final report release ready** — coverage inventory is complete, rendered slide plan matches, no hard stop remains, and visual QA passed.

For the current audited 30041 evidence, the real-source contract test confirms 21 played, 1 average, and 2 unplayed boards. The minimum deck coverage plan is 24 slides (title + overview + 21 played-board slides + final). It intentionally remains blocked for final release until the episode inventory and rendered/visual QA gates are actually completed.

The module never enables automatic student-error attribution or methodology invention.