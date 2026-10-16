# Two authored spot situations: local learner screen

The screen is wired into the FastAPI lesson assets at
`bridge_school_api/static/spot_cycle_session/`. Its exact local preview URL is
`http://127.0.0.1:<port>/local/spot-cycle/session/` when the loopback-only
`BRIDGE_LOCAL_SPOT_PREVIEW=1` gate and an existing local TeacherSessionStore
journal are configured. The corresponding school API path is
`/school/lessons/spot-cycle/`, with `./transport` and `./saved-answer` under
that path. The school path remains closed with HTTP 503 because its browser
session verifier and learner-to-journal lookup are not connected. This is not
an asserted URL on bridgecourse.co.il.

The pinned `learner-presentation.json` contains only `spot.a7c2` and
`spot.f3b8`. The browser verifies its SHA-256 before rendering, shows the
authored task text, and accepts exactly `first_play`, `count` (0 or 1), and
`explanation`. The client binds the v2 response to the server prompt hash. The
server validates that a submitted first-play card is in the visible hand;
the UI offers only the two authored choices. After an acknowledged terminal
`ADVANCE`, the screen reads back the
last saved learner answer and says that teacher review is pending. A failed
read shows no stale answer and offers retry. It does not assign another task,
grade mastery, display hidden hands, or read the complete private journal.

The actual website source supplied by Yan is not in this checkout. The prior
Library copy returned `403 scope_violation`; this work did not retry that
restricted source. The repository contains no non-admin PHP site source or
learner cabinet. Therefore the exact integration file and public site route
cannot be established here. The next site step is a permitted, non-secret
copy of the site's page/router source and deployment mapping; inspect those
before placing a lesson link or selecting a browser-session mechanism.

The workflow `.github/workflows/two-spot-lesson-ci.yml` runs the learner's
JavaScript contracts and HTTP/ASGI tests on standard Ubuntu 24.04 with Python
3.12. The HTTP cycle covers both authored spots, checks that saved v2 answers
remain unavailable before terminal ACK, and reads back the saved answer
without assigning another task. It uses synthetic journals, read-only
repository access, no production secrets, and no artifact or cache uploads.
The test command has a two-minute deadline. CI is triggered by a push to
`codex/two-spot-learner-ui-20261015` or a matching pull request.
CI success is separate from school-host integration and does not authorize
merging or publishing the lesson to the site.
