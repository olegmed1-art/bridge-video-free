# Definition source overlay (uninstalled)

Base: public `main` commit `d33edc382e324246946d95862f4fe4c63cd435b3`,
tree `81df1478d401e8fc8bbe4e214e8044d59e1497a0`. This changed only three
Books workflow/ops paths relative to the prior `cef161c6` review base. The two edited Video
readers use exact main preimage blobs `51a6532d78e32447531d73c62af60c6ec70092d5`
and `0c58bfb5b53020ef25581c49c13ffbf795aee578`. The original reviewed
Definition and Video packages remain unchanged.

`0375_workflow_definition_contract_store.sql` has exactly the reviewed 0331
source bytes; only its release filename changes. `0376_workflow_definition_bundle_store_successor.sql`
replaces old 0331/0332 release labels and its `schema_migration` key with
0375/0376. The source rollback filename is updated accordingly. The latest
observed main has 0374 and 0400–0402, but no 0375 or 0376. The migration
namespace guard permits `workflow_` in 03xx, and `migrate.sh` rejects duplicate
numeric prefixes. These are source assertions, not a production migration.

The typed Video staging member is a derived, append-only candidate. Its
source `RULE_CANDIDATE` was `TOPIC_MENTION`/`STAGING_ONLY`; no native UUID or
accepted skill is fabricated. The product reader keeps the existing school,
source/run, replay, revocation and disclosure fences. The Definition reader
still requires an authenticated host connection/context lease and all sixteen
current, independent Canon checks. The lesson preflight correlates exact
draft lesson/rubric/assessor hashes and returns `ABSTAIN`; EX-0002's
test-only accepted-composite consumer is deliberately excluded.

No role, grant, key, authority issuer, active route, runtime connection,
production SQL, merge or deploy is added. `0375` creates ancillary typed state
without enabling it; `0376` leaves its control disabled and seeds no
principals. Rollback before installation is removal of this source overlay.
After any separately qualified installation, do not drop retained state or
history to undo release: disable its control, stop dispatch, and restore a
verified preimage under an independently reviewed operational plan.

Offline checks cover Python imports, real draft hash binding, all 16 check
identifiers, fail-closed Teacher output, exact Video product projection against
the reviewed source, SQL parse and 0375/0376 release-key transformation.
The disposable PG18 R2 PASS belongs to the original 0331/0332 source stack;
it does not qualify these renamed bytes or any project database.
