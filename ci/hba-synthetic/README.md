# Private revision5 capability-classifier proposal — NOT PUBLISHED, NOT RUN

Baseline revision3 publication commit 52e63d9d36b13947586ddb15d20d383cda41b7e5 and trigger cdcb12dc250b33060ae8bad5206d4564c26c3413 ran once: ordinary 54 PASS, namespaces 4 FAIL because uid_map EPERM was not recognized as unavailable. These are actual failures, not retrospective SKIPs. Native job 112139542829 ran 5 seconds; no rerun was made.

The proposed fix recognizes only exact one-line LANG=C unshare capability refusal diagnostics with returncode 1, including observed /proc/self/uid_map EPERM. Unknown diagnostics, extra lines, success and killed children are not converted into SKIP. Six ordinary classifier regressions were added. Candidate inventory: 60 ordinary + 4 optional namespace = 64 methods. The added integration regression calls all four scenario entrypoints with a known uid_map refusal, an unknown diagnostic containing the old substring, and a multiline diagnostic; only the known refusal may skip. Namespace SKIP would mean NOT_RUN for bind/namespace scenarios and would never establish live readiness.

Workflow, helper, collector and supervisor bytes are preserved from reviewed revision3. Exact-directory/class/method validation remains before imports. No root, sudo, installs, security changes, production access, owner contract or secrets. The candidate has NOT_RUN AST/native status and requires bounded independent review. No GitHub publication or second CI is authorized/executed under the exhausted one-run budget.

The observed ordinary descendant-drain test passed, including an escaped process-group grandchild and ECHILD assertions. Namespace-dependent drain/bind proof remains unavailable on that runner. Directory coherence and continuous coordination remain live gates.

Baseline external Vercel previews for both pushes allocated/cloned then CANCELED after ignoreCommand exit 0; no app build was observed in the collected terminal logs. Cost was not established as zero.

Before any future publication/run, obtain explicit scoped owner authorization and fresh coordinator/recognizer clearance. Do not rerun the old job: its run_attempt guard forbids reruns, and its four failures remain the recorded result.

Revision5 adds explicit native_coverage fields distinguishing actual bind/drain PASS from NOT_RUN or FAILED_OR_UNKNOWN. Entry into a case does not prove namespace scenario execution; failed cases never establish coverage. All classifier fixes and six new regressions remain NOT_RUN pending independent review. The red revision3 run is unchanged.

Independent review found a stale substring classifier in revision4's namespace descendant-drain receipt scenario. Revision5 fixes that final call. Revision4 remains an unexecuted proposal with a known review defect; none of its results or the original failed run are relabeled. All four entrypoints are covered by the new mocked regression, which is still NOT_RUN.

Revision5 also removes the unconditional mount-error-to-exit77 conversion. Only mount returncode32, empty stdout and an exact target-bound LANG=C permission-denied diagnostic (with only the optional standard dmesg hint) produce an exact denial receipt. The parent accepts exit77 only with that exact stdout receipt and empty stderr. All other mount failures exit78 and fail the scenario. Two regressions exercise the actual embedded child without executing mount (16 subcases) and both real bind entrypoints (10 subcases). The four-entrypoint unshare regression has 12 subcases. All remain NOT_RUN; no native capability claim is made.

Diagnostic form reference: https://github.com/util-linux/util-linux/blob/master/libmount/src/context_mount.c (EPERM) and https://github.com/util-linux/util-linux/blob/master/sys-utils/mount.c (format/hint). This is a conservative source allowlist, not a confirmation of the CI runner's installed mount version; additional formats fail closed.
