# Диана 1 — завершение полного алгоритма обработки

Дата фиксации: 2026-08-18
Job ID: `e853e695e34e01e941a481cd513530ce`
Video algorithm: `3.1-free-r25.6`

## Source integrity

- MASTER Drive ID: `1_fqEYx5lx47qC6ISIyNkW06NJ4gSdx1B`.
- MASTER parent remains `Полный видеокурс Дианы` (`1aQRFIlKrGjePmrDUaohppmygXp4MwAMo`).
- Source size: 1,103,541,313 bytes.
- Source hash recorded by worker: `345c7935d5c078764b99bf22397435d0fc42f19a9748b9e3b136d0f50dcb0e1d`.
- Source was not renamed, moved, modified, deleted or placed in result/work folders.
- Derived outputs were routed to dedicated folders.

## Execution

Heavy video analysis run: `32076196790` — SUCCESS.
Finalizer run: `32077650827` — SUCCESS.
Longitudinal regression run: `32078798086` — SUCCESS.

The heavy run used standard public GitHub Actions infrastructure, FFmpeg and local open-source faster-whisper. Paid AI endpoints, paid cloud fallback and billing fallback were disabled.

## Output files

Result folder: `1YIctvI0Nq42n8PzU5FbQL9DEiXlkU9C7`.

- master PDF: `1aql_IlkXzXEGMHMEuyR_vMvzgk7MTukm`;
- AI_DONE: `1C22f2w6Tnt85sgSKPMbq1eC2VyrwMrF4`;
- METHODOLOGY_READY: `1dPuXSzfk6Taq3NM7rPtHxYsYYEr4tYne`;
- CLEANUP_ACK: `1972saT9ItFmztMKVAaRmeZCy0U9ssq7k`;
- longitudinal JSON: `1IhpSislmUezkz2wFBSILFYSYE4jGInY0`;
- longitudinal Markdown: `1ty8XwwmPv3dwx70EYsNj7bLtLlz-mVIw`.

Work/receipt folder: `1Ti5JaNXfLnu4Rw8fVia6teBzdAcz_JQ-`.

## Extraction facts

- transcript segments: 819;
- semantic episodes: 279;
- visual Evidence items: 249;
- selected report visuals: 30;
- observed decision candidates: 115;
- deal candidates: 136;
- learning interaction cycle candidates: 53;
- Canon match candidates: 251;
- Knowledge candidates after longitudinal consolidation: 82;
- Reusable Asset candidates: 17;
- Knowledge/Curriculum Gap candidates: 3.

The first lesson is principally an introduction to card play in no-trump contracts, moving from simple positions toward core planning ideas. Historical Curriculum candidates also include first lead, finesse, pass, bid, game, response, trick, trump, dummy/table, opening, contract, deal, expasse and transfer. These remain extracted candidates, not activated School canon.

## Lesson date

`lesson_number = 1`.
`lesson_date = 2021-02-22` with status `CANDIDATE_MEDIUM`.
Basis: the original source Drive createdTime and modifiedTime both fall on 2021-02-22. The date was not explicitly recovered from the transcript, so independent confirmation remains open. Drive dates and lesson date are stored separately.

## Quality and limitations

- semantic QC: PASS;
- visual pass 1: COMPLETE;
- visual pass 2: COMPLETE;
- actor attribution: unavailable because speaker labels are absent;
- actor-specific claims withheld rather than inferred;
- unreliable transcript segments excluded from semantic derivation;
- no automatic claim of retention/generalization/transfer from one lesson;
- no automatic inference that NOT_OBSERVED means failure.

## Database persistence

Neon persistence completed idempotently on the current School database branch:
- one media asset and corrected transcript;
- 819 transcript segments;
- 279 semantic episodes;
- 53 learning-cycle candidates;
- 115 decisions;
- 357 Evidence records;
- 715 Evidence links;
- analysis/report artifact and QC records.

Post-run verification found:
- Canon activations: 0;
- Student profile snapshots/inputs/inferences: 0;
- verified KnowledgeItem/KnowledgeVersion writes: 0;
- normative CourseVersion writes: 0.

Therefore all extracted Canon, Knowledge, Student and Curriculum objects remain A1 Candidate Evidence.

## Cost

- paid AI API cost: 0;
- paid cloud fallback: not used;
- permanent heavy working copy: not created;
- duplicate heavy processing was skipped on finalizer runs through terminal receipt/idempotency checks.

## Final verdict

`DIANA_1_FULL_ALGORITHM = PASS`

The source is intact; master analysis, Evidence, longitudinal extraction, Knowledge/Curriculum candidates, result routing, database persistence and regression validation are complete.