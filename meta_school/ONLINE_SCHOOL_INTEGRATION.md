# Online School + META School integration

Status: DESIGNED

## Purpose

Online School is the human-facing education layer. Learning Engine manages verified student-learning state. META School governs quality, evidence and improvement. School Core supplies data, computation and durable storage.

## Boundary contracts

### Online School -> Learning Engine
Student actions are submitted as candidate learning events. Portal completion alone is EXPOSURE, not mastery.

### Production -> Learning Engine
Video, tournament, deal, test and homework pipelines may emit candidate events only with SourceID and provenance. Automated claims require EvidenceID when they are used to update durable skill state.

### Learning Engine -> Online School
Returns evidence-backed progress, unresolved/low-confidence state, and recommendations. Recommendations must expose confidence and must not masquerade as teacher-approved methodology.

### AI Bridge Coach
Must use approved School Knowledge and verified Student Context. If approved knowledge does not support a bidding/methodology answer, return UNKNOWN or escalate to teacher. Coach-generated text is not automatically School Knowledge.

### META School -> all layers
May run quality checks, detect failures, create Lab candidates and promote safe technical changes after evidence. Protected pedagogical/methodology changes require teacher approval.

## Minimal canonical IDs

- PersonID
- StudentID
- SourceIdentity / MappingID
- TopicID
- SkillID
- LessonID
- ExerciseID
- TournamentID
- DealID
- RecordingID
- SourceID
- RunID
- EvidenceID
- Artifact/FileID
- LearningEventID
- InterventionID

## Minimal student learning state

Do not store a single unexplained mastery percentage as truth. Derive current state from evidence history and retain:
- last verified evidence;
- evidence count/type;
- successes and errors;
- intervention history;
- later outcomes;
- confidence/uncertainty;
- unresolved identity/evidence flags.

## Initial end-to-end pilot

Use one known student only after SourceIdentity is verified.

1. Create/verify StudentID.
2. Map one source identity (e.g. tournament identity or recording identity).
3. Map one TopicID/SkillID to approved course material.
4. Ingest one real learning source.
5. Produce candidate Learning Events.
6. Pass automated claims through Evidence Gate.
7. Build a minimal Skill evidence view.
8. Generate one recommendation for teacher review.
9. Record teacher decision separately from the recommendation.
10. Observe a later real outcome and close the feedback loop.

Pilot success is evidence that the identity and learning loop is reproducible, not that the recommendation happened to sound plausible.

## Safety against false learning

- Folder/name matching cannot merge students.
- Course completion cannot equal mastery.
- DDS can establish double-dummy consequences but does not by itself establish the pedagogical cause of a student's mistake.
- Correlation between an intervention and later improvement is not automatically causation.
- One local correction is not automatically a global teaching rule.
- Low-confidence and conflicting evidence remains visible.

## Next implementation artifacts

1. Controlled Neon migrations for canonical identity, learning events, topic/skill mapping and evidence links.
2. Validation code for identity and learning-event schemas.
3. Minimal APIs/services for event ingestion and evidence-backed student state.
4. Regression fixtures for wrong-person joins, duplicate events, unsupported mastery claims and Coach UNKNOWN behavior.
5. Teacher-review queue for protected or low-confidence decisions.
