# Two visible-suit exercises: local staging integration

The optional server service adds `diana19-visible-exercise-001` and
`diana19-visible-exercise-002` to the existing Diana19 picker. It reads the
accepted split delivery format, not the older `student/exercises.json` format.
The author's files are never edited or executed.

Accepted source ZIP SHA256:
`68de356c6fc48a95e2d03baa117704657c9083a12062b2e294a3773f4fb170b5`.
Library source: `libfile_a73d9fbcf6508191bddb5f61e6d96c0f`, version 0.
Learner-only ZIP SHA256:
`2f99e179f9d29b2ed478134015af297c1424d07ff7aa0c30de2a27d5438bd010`.
Library learner delivery: `libfile_ed260f3c40008191bb610c4b491247a8`, version 0.

The install command accepts the exact reviewed ZIP or its containing directory:

```text
python -B -m site_integration.yan_spot_cycle.install_visible --private-dir <existing-local-root>/private --source <reviewed-split.zip>
```

It verifies the complete ZIP SHA, member count, CRC, manifest and learner/key
binding before creating its own private projection and an empty answer journal.
No archive extraction or author scripts run. Existing answers are not copied or
rewritten. A new root can instead use `create_local --visible-exercises <zip>`,
optionally together with the existing `--lesson-archive` for a five-item picker.
Restart the explicit local launcher normally to load the new optional binding.

The initial response contains the learner-only title, three questions, open
holdings and editorial conditions. The private key, solutions and next prompt
are excluded from the catalog, initial API response, page and static JavaScript.
There is no endpoint for obtaining a later prompt directly.

The diamond exercise has two immutable responses in a separate SQLite journal.
The first independent response must commit with `synchronous=FULL` before the
guided prompt can be returned. The guided response is marked `GUIDED_PRACTICE`;
it is not an independent assessment. The final explanation follows its save.
The spade exercise has one response. Exact retries remain idempotent after a
lost reply; reload reads the persisted stage. Different rewrites are rejected.
No automatic score, mastery, canon change or historical video play is inferred.
Source video links are not invented when the accepted package has no URL.

Focused checks:

```text
python -B -m pytest tests/test_diana19_visible_stages.py -p no:cacheprovider --basetemp <new-existing-parent>/pytest
node site_integration/yan_spot_cycle/tests/verify_visible_stages_ui.cjs <fresh-private-root>
```

The second command uses the actual JavaScript controller with a test DOM,
separate Python CLI processes and the real private SQLite journal. It creates
only new automated verification records in that root. It covers the initial
payload, lost save reply, retry, stage refresh, final reveal and the independent
spade response. This proof does not claim browser HTTP or actual CMS integration.
The existing teacher-session engine and three-lesson source pins are unchanged.

Production remains disabled. The anonymous application adapter still requires
an explicitly verified native session/CSRF resolver and CMS renderer. The
original site archive has not been materialized here: this environment does
not expose a `download_file` tool for the supplied Sediment reference. No Drive
download workaround, broad Oracle search or private configuration read was made.
Rollback is a code revert while retaining all private journals for review.
