# Bridgit unresolved-card recovery v1

Status: **SHADOW_ONLY / successor**.

## Reference source

The successor uses the original Gambler classic client artwork as the visual
reference. The asset bytes are not committed; the caller supplies a local
`all.png`, its SHA-256, and verified registered card scale. The native variant
is selected from card scale rather than video resolution or window position.

## Trigger

Recovery does not run on every frame. It starts only when the primary stable
deal state is incomplete. Candidate retry frames come from neighbouring
**event-selected registered frames**; timer polling is not part of this path.
Duplicate decoded-pixel identities do not add support.

## Retry order

1. Determine the unresolved card identities from direct HAND/PLAYED evidence.
2. Re-search only those identities using the original Gambler card corners.
3. Primary retry requires confidence >= 0.95.
4. Occlusion-aware smaller-corner retry may enter at >= 0.92, but requires at
   least two independent decoded-pixel frames for the same card and seat.
5. A card claimed for more than one seat stays unresolved.
6. Seat binding uses normalized N/E/S/W regions inside the registered game
   window, so source resolution, scale and absolute window position are not
   recognition inputs.
7. Cursor position has no role.

## Completion

After all direct retry evidence is exhausted, exact deck complement is allowed
only when exactly one seat remains incomplete, every other seat has 13 directly
supported cards, and the number of remaining deck cards exactly equals that
seat's capacity. Those cards are labelled
`EXACT_DECK_COMPLEMENT_AFTER_RETRY` and
`MATHEMATICALLY_UNIQUE_NOT_VISUAL`.

If two or more hands remain incomplete, the successor does **not** guess their
split and returns `PARTIAL_AFTER_RETRY`.

Thus the successor's order is:

`direct recognition -> unresolved retry -> exact unique completion -> partial`

not:

`direct recognition -> blind deck fill`.

## Safety / provenance

- no SCHOOL CANON or production write;
- no cursor evidence;
- no probabilistic hidden-hand split;
- no duplicate-frame evidence multiplication;
- exact-complement cards remain derived, never relabelled as visually observed;
- historical frozen v1 recognizer remains unchanged.
