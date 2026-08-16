# META School

Status: DESIGNED

META School is the governing self-improvement layer for the Sports Bridge School. It extends existing production processes; it does not replace them.

## Whole-school architecture

The complete platform has four cooperating layers:

1. **Online School** — Student Portal and Teacher Portal; courses, lessons, tests, homework, AI Bridge Coach, progress, tournament reviews and delivery of learning materials.
2. **Learning Engine** — converts verified learning events into a model of what each student studied, demonstrated, misunderstood, improved and should practice next. It recommends; protected teaching decisions remain with the teacher.
3. **META School** — Governor, quality/evidence, failure intelligence, experiments, metrics, evolution and system self-improvement.
4. **School Core** — Neon, identity, knowledge, DDS, tournament/deal analysis, video, PBN, Drive/artifacts and GitHub/versioned code.

Flow:
Online School -> Learning Engine -> META School -> School Core, with verified results flowing back to Learning Engine and Online School.

META School treats Online School and Learning Engine as first-class clients. Core META interfaces must remain general enough that adding a second production algorithm or a new learning surface does not require redesigning the governing core.

## Constitution

1. Teacher-approved bidding and teaching methodology are protected and may not be changed autonomously.
2. Hypotheses are never promoted to teacher rules automatically.
3. UNKNOWN is preferable to invented certainty.
4. Material knowledge must preserve provenance.
5. A new version never destroys the last Stable version.
6. A material corrected failure creates or updates a regression test.
7. Failed experiments are retained as negative knowledge.
8. No change is called an improvement without independent evidence.
9. Changes to shared components require dependent regression checks.
10. At equal quality, prefer the simpler and cheaper solution.
11. Repetition of a known error is a META failure and increases priority.
12. Protected methodology changes require teacher approval.
13. Student-facing AI must answer from approved School Knowledge plus verified student context; absence of an approved rule produces UNKNOWN/teacher escalation rather than invented bidding or methodology.
14. Learning recommendations are not teacher rules. Promotion of a pedagogical hypothesis requires teacher approval.
15. Student data from different sources may be joined only after identity resolution.

## Status model

DESIGNED -> IMPLEMENTED -> TESTED -> OPERATIONAL

Promotion is evidence-gated. Existence of code or a local successful run is not enough for OPERATIONAL.

Minimum evidence record:
- EvidenceID
- component_id and version
- source identity
- RunID and/or immutable Artifact/FileID
- test/regression result
- timestamp
- provenance

OPERATIONAL requires reproducible end-to-end evidence and a repeated run/regression check appropriate to the component.

## Core layers

1. Constitution + Human Authority
2. Governor
3. Identity & Provenance
4. Registry / Knowledge / Artifact Manifest
5. Orchestrator over existing production algorithms
6. Adaptive Quality Engine (L0 code checks -> L1 semantic review -> L2 independent critic -> L3 candidates -> L4 red-team/shadow -> L5 teacher review)
7. Evidence Gate
8. Failure Intelligence + Root Cause
9. Experiment Lab: Stable / Lab / Candidate / Promote / Rollback
10. Historian + Metrics
11. Proactive Improvement
12. Discovery / Watcher
13. Architect + Complexity Auditor / SIMPLIFY

## Online School interfaces

### Student Portal
Consumes only student-authorized, evidence-backed data. Planned surfaces include course/lesson state, homework, tests, learning materials, progress, tournament reviews and AI Bridge Coach.

### Teacher Portal
Provides oversight of student state, unresolved identity, low-confidence learning conclusions, protected methodology proposals, candidate learning interventions and META escalations.

### AI Bridge Coach
Input: StudentID + approved School Knowledge + verified student context + current question/task.
Output passes through the appropriate Quality/Evidence route before becoming durable learning evidence. Coach answers do not become School Knowledge merely because they were generated or accepted conversationally.

## Learning Engine

Canonical learning loop:
Topic -> Lesson -> Exercise -> Homework/Test -> Real play/Tournament -> DDS/analysis where applicable -> Error/Success -> Student Learning Event -> Skill evidence -> recommendation -> teacher/next lesson.

The engine must distinguish:
- exposure: student was taught/shown a topic;
- assessment: student was tested;
- demonstrated performance: student made a decision in a real or controlled deal;
- error/success classification;
- intervention: explanation/exercise/homework was provided;
- later outcome: whether the behavior repeated or improved.

A learning event should preserve StudentID, Topic/SkillID, SourceID, RunID when automated, evidence/provenance, confidence, timestamp and links to relevant artifacts/deals.

Student mastery is never inferred from course completion alone. A Skill Model must preserve uncertainty and evidence history.

## Identity rules

Names and folder names are not identity. Use stable PersonID/StudentID plus SourceIdentity and external identity mappings. Student-learning joins are blocked until identity is resolved.

Zoom recordings, Drive folders/files, tournament identities and portal accounts require explicit SourceIdentity mappings before cross-source aggregation.

## Data rules

Neon remains the system of record. META must extend the existing schema through controlled migrations; it must not create a competing source of truth. New migrations are never treated as production merely because they exist in the repository.

The future Online School and Learning Engine must use the same canonical IDs and system of record rather than maintain parallel student profiles.

## Artifact rules

Every material output should be traceable to source data, algorithm/version, RunID, Artifact/FileID and checksum where practical. Access-control changes are protected operations and are not autonomous self-improvement actions.

Student-facing delivery must verify StudentID/authorization and the intended artifact/version before delivery.

## Production integrations

### Video / online lessons
Zoom/recording -> RecordingID/SourceIdentity -> transcript/media pipeline -> lesson analysis -> Quality/Evidence -> verified Learning Events. Statements extracted from speech do not automatically become global Teacher Rules.

### Tournament/deal analysis
Tournament identity -> boards/results -> Deal/Tournament Analyzer -> DDS where applicable -> critical decisions -> Quality/Evidence -> Student Learning Events -> Skill Model. The system should link repeated errors and later successes to prior learning interventions without treating correlation as proven pedagogical causation.

### Course and approved materials
Approved course materials are School Knowledge with provenance/versioning. The Learning Engine maps lessons and exercises to Topic/Skill IDs but does not silently change course sequence or teaching method.

## Improvement loop

Production -> Quality -> Evidence Gate -> Failure/Root Cause when needed -> Lab candidates -> Regression/Golden/Red-Team according to risk -> Promote or Rollback -> Historian/Metrics -> Proactive Improvement.

Online-school feedback adds a second loop:
Verified Learning Events -> Skill Model -> intervention recommendation -> teacher/student learning activity -> later verified outcome -> effectiveness evidence -> META analysis.

## First-stage KPI

The first objective is not maximizing the number of experiments. It is increasing the number of existing school components with reproducible evidence of their actual status and operation.

Primary META metrics:
- Repeat Error Rate
- First Pass Acceptance
- Teacher Intervention Rate
- Regression Rate
- Detection Before Delivery
- Learning Velocity
- Improvement per cost

Future learning metrics must distinguish content-production quality from educational outcome. A beautiful report is not evidence that learning occurred.

## Build order

1. Truth Layer: Registry + Identity + Provenance + RunID + ArtifactManifest
2. Evidence Layer: common status/evidence gates
3. Quality Layer: regression + Golden Set + Failure Base + dependency graph
4. Governor: adaptive checks + retry/checkpoint/rollback
5. Online School identity contracts: StudentID + portal/source mappings
6. Learning Event schema + Topic/Skill identity
7. Learning Engine minimal loop using verified events
8. Learning: failures -> hypotheses -> candidates -> experiments -> evidence
9. Evolution: proactive weakness discovery
10. Student Skill Model and personalized recommendations after reliable identity/evidence
11. AI Bridge Coach grounded in approved School Knowledge + verified Student Context
12. Discovery/Watcher/Red Team and META self-audit

## Budget policy

Prefer deterministic code over AI whenever code can verify the requirement reliably. Escalate AI depth by uncertainty, impact and risk. Budget is a ceiling, not a spending target.

Student-facing routine operations should use the cheapest reliable path; expensive critics/experiments belong primarily to uncertainty, high-impact decisions and META improvement rather than every ordinary student interaction.