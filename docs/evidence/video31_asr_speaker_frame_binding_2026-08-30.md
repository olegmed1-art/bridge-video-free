# Video 3.1 FREE — source-bound ASR/speaker frame binding

Date: 2026-08-30

Change ID: `video31-source-bound-speech-frame-v1`

Governance mode: `ASSURED`

Dependency: hidden-card prohibition from PR #950.

## Finding

The profiled SHADOW adapter previously evaluated each frame independently. A
speech declaration without an explicit frame hash was therefore applied to
every frame whose timestamp fell inside the segment interval. A `frame_file`
value was also accepted without SHA-256 or source identity. That behavior could
spread one phrase across multiple visual states and could not prove that a
phrase and frame came from the same source.

The machine-readable 3.1-test definition also retained two superseded claims:
teacher speech could create a card after identity/confidence gates, and the
fourth hand could be derived from 39 visible cards.

## Corrected contract

Each declaration now has exactly one of two outcomes:

1. `BOUND` to one manifest frame, with source fingerprint, frame SHA-256,
   filename, frame timestamp, transcript locator, speech interval, binding
   method, and distance to the interval midpoint; or
2. `REVIEW` with a bounded reason code.

Explicit binding requires a manifest SHA-256 and a matching speech interval.
Filename-only binding is rejected. Temporal binding considers only frames
inside the speech interval, selects exactly one nearest frame, and rejects an
equal-distance tie as ambiguous. A declaration is never bound to more than one
frame.

Speaker identity and role remain separate evidence dimensions:

- anonymous diarization labels do not prove a real-person name;
- teacher/student role remains suggestion-only unless its independent role
  proof gate passes;
- a named identity remains `UNKNOWN` without separate private source-bound
  evidence;
- even a verified teacher phrase cannot create a card; it can only corroborate
  a visual observation or expose a conflict.

## Review reasons

- `SOURCE_IDENTITY_MISSING`
- `SOURCE_IDENTITY_MISMATCH`
- `INVALID_SPEECH_INTERVAL`
- `FRAME_SHA256_NOT_IN_SOURCE_MANIFEST`
- `FRAME_SHA256_AMBIGUOUS`
- `FRAME_FILE_SHA256_MISMATCH`
- `FRAME_OUTSIDE_SPEECH_INTERVAL`
- `FRAME_FILE_WITHOUT_SHA256`
- `NO_FRAME_INSIDE_SPEECH_INTERVAL`
- `AMBIGUOUS_NEAREST_FRAME`

## Safety and evidence status

This is a SHADOW evidence contract. It does not run media, alter ASR text,
activate production recognition, change a route, name a person, or write to
SCHOOL CANON, Student Profile, or the approved Evolutionary Course. Unit and
CI checks prove contract behavior only; real-video timing and speaker accuracy
remain unproved until independent canary evidence exists.
