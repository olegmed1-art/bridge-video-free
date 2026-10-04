"""Manual owner inventory proposal: fixed read-only checks, never pilot SQL.

Only a reviewed test-branch workflow may supply its existing resident credential.
No caller SQL, credential export, writes, role changes, or activation are accepted.
"""
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from . import resident_preflight as resident

REPOSITORY = "olegmed1-art/bridge-video-free"
BRANCH = "test/canon-acceptance-cf6091-20261004"
MAIN = "711ddd648fa74f2b903f9d7127dadc412f94b277"
WORKFLOW = ".github/workflows/native-maintenance-owner-attest.yml"
URL = "https://api.github.com/repos/" + REPOSITORY + "/git/ref/heads/main"
PHASE = "context"


def require(ok, code):
    if not ok:
        raise resident.Refused(code)


def context(env, git):
    """Check before credential access; expected probe SHA is reviewed externally."""
    sha = env.get("EXPECTED_PROBE_SHA", "")
    require(bool(re.fullmatch(r"[0-9a-f]{40}", sha))
            and env.get("GITHUB_SHA") == sha and env.get("EXPECTED_MAIN") == MAIN
            and env.get("GITHUB_REPOSITORY") == REPOSITORY
            and env.get("GITHUB_REF") == "refs/heads/" + BRANCH
            and env.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and env.get("GITHUB_ACTOR") == "olegmed1-art"
            and env.get("GITHUB_TRIGGERING_ACTOR") == "olegmed1-art"
            and env.get("GITHUB_WORKFLOW_REF") == REPOSITORY + "/" + WORKFLOW + "@refs/heads/" + BRANCH
            and env.get("OWNER_PROBE_SCOPE") == "canon-readonly", "probe_context_refused")
    require(git("rev-parse", "HEAD") == sha
            and git("merge-base", "HEAD", MAIN) == MAIN, "probe_checkout_refused")
    return sha


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "source_response_refused")
        result[key] = value
    return result


def source_check(token, opener):
    require(bool(token), "source_token_required")
    request = Request(URL, headers={"Authorization": "Bearer " + token,
                      "Accept": "application/vnd.github+json", "Cache-Control": "no-cache",
                      "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "canon-owner-readonly"})
    with opener.open(request, timeout=5) as response:
        require(response.status == 200 and response.url == URL, "source_response_refused")
        raw = response.read(16385)
    require(len(raw) <= 16384, "source_response_refused")
    value = json.loads(raw, object_pairs_hook=unique)
    require(value.get("ref") == "refs/heads/main"
            and value.get("object", {}).get("type") == "commit"
            and value["object"]["sha"] == MAIN, "live_main_changed")


def observe(connect, raw):
    """Credential remains inside the protected runner; no snapshot/rows exported."""
    global PHASE
    PHASE = "credential_policy"
    from ops.native_maintenance_owner_attest import parameters, EXPECTED_TARGET

    kwargs = parameters(raw)  # Existing strict owner parser; no role substitution.
    kwargs["application_name"] = "canon-owner-readonly-probe"
    binding = resident.Binding(**EXPECTED_TARGET["neon"])
    PHASE = "connection"
    with connect(**kwargs, autocommit=True) as conn:
        PHASE = "read_only_transaction"
        conn.read_only = True  # Before the first transaction/query.
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute("SET LOCAL statement_timeout='5s'")
            require(conn.execute("SELECT current_setting('transaction_read_only')").fetchone()
                    == ("on",), "server_read_only_required")
        PHASE = "identity_and_permissions"
        report = resident.inspect_resident(conn, binding)
    checks = ("server_binding", "plan_table_privileges", "plan_update_privileges",
              "owned_revoke_privileges", "schema_usage", "school_select", "explicit_gate_execute")
    return {"audit": "CANON_OWNER_READ_ONLY_INVENTORY",
            "status": "PASS" if all(report[k] for k in checks) else "MISSING_PRIVILEGES",
            **{key: report[key] for key in checks}, "transaction_read_only": True,
            "tls_verified": True, "owner_identity": True,
            "production_mutations": False, "write_admission": False}


def main():
    global PHASE
    PHASE = "context"
    import psycopg

    root = Path(__file__).resolve().parents[2]

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True,
                                       stderr=subprocess.DEVNULL, timeout=5).strip()

    sha = context(os.environ, git)
    opener = build_opener(NoRedirect(), ProxyHandler({}))
    PHASE = "source_before"
    source_check(os.environ.get("GH_TOKEN", ""), opener)
    PHASE = "owner_inventory"
    report = observe(psycopg.connect, os.environ.get("NATIVE_OWNER_DATABASE_URL", ""))
    PHASE = "source_after"
    source_check(os.environ.get("GH_TOKEN", ""), opener)
    print(json.dumps({**report, "probe_sha": sha, "main_sha": MAIN}, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


def entrypoint():
    try:
        result = main()
    except BaseException:
        # No raw exception/DSN/password/host/tenant/private-row serialization.
        print(json.dumps({"audit": "CANON_OWNER_READ_ONLY_REFUSED", "phase": PHASE,
                          "production_mutations": False, "write_admission": False}))
        result = 2
    raise SystemExit(result)


if __name__ == "__main__":
    entrypoint()
