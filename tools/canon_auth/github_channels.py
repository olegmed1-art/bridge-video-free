"""Dormant fixed-repository authenticated observation and create-only claim backend.

No workflow imports this module. Existing GH_TOKEN is consumed in place only.
The current installed read-only token cannot create claims. Do not install with
write permissions without explicit security review and namespace qualification.
"""
import base64
import json
import re
from hashlib import sha1, sha256
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler
from .launch_contract import digest, require
from .resident_preflight import Refused
from .owner_probe import NoRedirect

REPO = "olegmed1-art/bridge-video-free"
PREFIX = "/repos/" + REPO
WORKFLOW = ".github/workflows/native-maintenance-owner-attest.yml"
NAMESPACE = "refs/canon-claims/"
SHA = r"[a-f0-9]{40}"
READ = re.compile(
    r"(?:" + re.escape(PREFIX) + r"/branches/main|" +
    re.escape(PREFIX) + r"/actions/runs/[1-9][0-9]*(?:/jobs\?per_page=100)?|" +
    re.escape(PREFIX) + r"/git/(?:blobs|commits)/" + SHA + r"|" +
    re.escape(PREFIX) + r"/git/matching-refs/canon-claims/(?:intents/[a-f0-9-]{36}|"
    r"builds/dpl_[A-Za-z0-9]{8,100}|stages/[a-f0-9-]{36}/(?:baseline|initial|revoke|reactivate)))"
)
WRITE = {PREFIX + "/git/blobs", PREFIX + "/git/trees",
         PREFIX + "/git/commits", PREFIX + "/git/refs"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


class GitHubREST:
    """Fixed TLS origin, no proxy/redirect/retry; bounded responses; no secret output."""
    evidence_class = "authenticated_github"
    def __init__(self, token):
        require(isinstance(token, str) and bool(token) and "\n" not in token and "\r" not in token,
                "existing_github_capability_required")
        self._token = token
        self._opener = build_opener(NoRedirect(), ProxyHandler({}))
        self.calls = 0
        self._originals = []
        self._original_bytes = 0

    @classmethod
    def resident(cls, env):
        # Named resident lookup only; never enumerate, serialize or export env.
        return cls(env.get("GH_TOKEN", ""))

    def __repr__(self):
        return "GitHubREST(fixed_repository, credential_redacted)"

    def _validate_create(self, path, body):
        require(isinstance(body, dict), "claim_payload_refused")
        kind = path.rsplit("/", 1)[-1]
        if kind == "refs":
            require(set(body) == {"ref", "sha"}
                and isinstance(body["ref"], str)
                and re.fullmatch(r"refs/canon-claims/(?:intents/[a-f0-9-]{36}|"
                    r"builds/dpl_[A-Za-z0-9]{8,100}|stages/[a-f0-9-]{36}/"
                    r"(?:baseline|initial|revoke|reactivate))", body["ref"])
                and isinstance(body["sha"], str) and re.fullmatch(SHA, body["sha"]),
                "claim_namespace_required")
        elif kind == "commits":
            require(set(body) == {"message", "tree", "parents"} and body["parents"] == []
                and isinstance(body["tree"], str) and re.fullmatch(SHA, body["tree"])
                and isinstance(body["message"], str)
                and re.fullmatch(r"canon-claim [a-f0-9]{64}", body["message"]),
                "claim_root_commit_required")
        elif kind == "trees":
            require(set(body) == {"tree"} and isinstance(body["tree"], list)
                and len(body["tree"]) == 1, "claim_tree_required")
            entry = body["tree"][0]
            require(isinstance(entry, dict) and set(entry) == {"path", "mode", "type", "sha"}
                and (entry["path"], entry["mode"], entry["type"]) == ("claim.json", "100644", "blob")
                and isinstance(entry["sha"], str) and re.fullmatch(SHA, entry["sha"]),
                "claim_tree_required")
        elif kind == "blobs":
            require(set(body) == {"content", "encoding"} and body["encoding"] == "base64"
                and isinstance(body["content"], str) and len(body["content"]) <= 8192,
                "claim_public_blob_required")
            try:
                raw = base64.b64decode(body["content"], validate=True)
                value = json.loads(raw)
                from .launch_contract import Launch
                require(set(value) == {"version", "contract", "stage", "contract_hash", "attempt"}
                    and type(value["version"]) is int and value["version"] == 1
                    and type(value["attempt"]) is int and value["attempt"] == 1
                    and value["stage"] in ("reservation", "baseline", "initial", "revoke", "reactivate"),
                    "claim_public_blob_required")
                launch = Launch.parse(value["contract"])
                require(value["contract_hash"] == launch.fingerprint and raw == canonical(value),
                        "claim_public_blob_required")
            except Exception:
                raise Refused("claim_public_blob_required") from None
        else:
            raise Refused("claim_payload_refused")

    def _send(self, method, path, body=None):
        require((method == "GET" and READ.fullmatch(path))
                or (method == "POST" and path in WRITE), "github_path_or_method_refused")
        if method == "POST":
            self._validate_create(path, body)
        require(self.calls < 80, "github_channel_budget")
        self.calls += 1
        request = Request("https://api.github.com" + path,
            data=canonical(body) if body is not None else None, method=method,
            headers={"Authorization": "Bearer " + self._token,
                     "Accept": "application/vnd.github+json",
                     "Content-Type": "application/json",
                     "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with self._opener.open(request, timeout=10) as response:
                require(response.geturl() == request.full_url, "github_redirect_refused")
                status = response.status
                raw = response.read(4_000_001)
                request_id = response.headers.get("X-GitHub-Request-Id", "")
        except HTTPError as error:
            # Do not read/log an arbitrary error body containing private data.
            status, raw, request_id = error.code, b"", ""
        except Exception:
            raise Refused("github_transport_unproven") from None
        require(len(raw) <= 4_000_000, "github_response_budget")
        if status in (200, 201):
            require(bool(re.fullmatch(r"[A-Za-z0-9:-]{4,100}", request_id)),
                    "github_original_receipt_missing")
            try:
                data = json.loads(raw)
                require(self._original_bytes + len(raw) <= 8_000_000, "github_retention_budget")
                self._originals.append((path, request_id, raw))
                self._original_bytes += len(raw)
            except (ValueError, UnicodeError):
                raise Refused("github_response_refused") from None
        else:
            data = None
        return {"status": status, "data": data,
                "original_record_sha256": sha256(raw).hexdigest(),
                "request_id": request_id}

    def get(self, path):
        return self._send("GET", path)

    def _create(self, path, body):
        # Private fixed backend entry. No PATCH/DELETE/ref update exists.
        return self._send("POST", path, body)


class OwnerObservation:
    """Authenticated GitHub run provenance; inventory is never recovery readiness."""
    def __init__(self, channel):
        self.channel = channel

    def inventory(self, runtime_sha, run_id):
        require(bool(re.fullmatch(SHA, runtime_sha)) and type(run_id) is int and run_id > 0,
                "observation_identity_required")
        before = self.channel.get(PREFIX + "/branches/main")
        run = self.channel.get(PREFIX + "/actions/runs/" + str(run_id))
        jobs = self.channel.get(PREFIX + "/actions/runs/" + str(run_id) + "/jobs?per_page=100")
        after = self.channel.get(PREFIX + "/branches/main")
        require(all(r["status"] == 200 for r in (before, run, jobs, after)),
                "owner_observation_unavailable")
        require(before["data"]["commit"]["sha"] == after["data"]["commit"]["sha"] == runtime_sha,
                "owner_observation_main_changed")
        data = run["data"]
        require(data.get("id") == run_id and data.get("head_sha") == runtime_sha
            and data.get("head_branch") == "main" and data.get("event") == "workflow_dispatch"
            and data.get("run_attempt") == 1 and data.get("status") == "completed"
            and data.get("conclusion") == "success" and data.get("path") == WORKFLOW
            and data.get("actor", {}).get("login") == "olegmed1-art"
            and data.get("triggering_actor", {}).get("login") == "olegmed1-art"
            and data.get("repository", {}).get("full_name") == REPO,
            "owner_run_binding_refused")
        jobdata = jobs["data"]
        require(type(jobdata.get("total_count")) is int
            and jobdata["total_count"] == len(jobdata.get("jobs", [])) <= 100,
            "owner_jobs_incomplete")
        owners = [j for j in jobdata["jobs"] if j.get("name") == "canon-owner-probe"]
        require(len(owners) == 1 and owners[0].get("run_id") == run_id
            and owners[0].get("run_attempt") == 1
            and owners[0].get("status") == "completed" and owners[0].get("conclusion") == "success",
            "owner_job_binding_refused")
        steps = [s for s in owners[0].get("steps", [])
                 if s.get("name") == "Inventory existing owner connection without writes"]
        require(len(steps) == 1 and steps[0].get("conclusion") == "success",
                "owner_inventory_step_required")
        return {"status": "OWNER_INVENTORY_PROVENANCE",
                "source_transport": self.channel.evidence_class, "runtime_sha": runtime_sha,
                "run_id": run_id, "originals": [r["original_record_sha256"]
                    for r in (before, run, jobs, after)],
                "write_admission": False, "recovery_ready": False,
                "watcher_24h_installed": False}


class ClaimUncertain(Refused):
    pass


class CreateOnlyClaims:
    """Create-only immutable-public tuple claims, no repair/replay/update/delete.

    Keys are permanently consumed even on partial reservation. A transport loss
    does not authorize retry. Inspection can reconcile, but cannot admit execution.
    Current installed contents:read rejects the first POST: no live fallback.
    """
    def __init__(self, channel):
        self.channel = channel

    def _object(self, path, body):
        result = self.channel._create(PREFIX + "/git/" + path, body)
        require(result["status"] == 201 and isinstance(result["data"], dict),
                "claim_create_not_proven")
        require(bool(re.fullmatch(SHA, result["data"].get("sha", ""))),
                "claim_object_identity_refused")
        return result["data"]["sha"]

    def _commit(self, launch, stage):
        require(stage in ("reservation", "baseline", "initial", "revoke", "reactivate"),
                "claim_stage_refused")
        # Only nonpersonal public launch binding. No credential/SQL/catalog/hand.
        value = {"version": 1, "contract": launch.public(), "stage": stage,
                 "contract_hash": launch.fingerprint, "attempt": 1}
        raw = canonical(value)
        blob = self._object("blobs", {"content": base64.b64encode(raw).decode(), "encoding": "base64"})
        expected_blob = sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        require(blob == expected_blob, "claim_blob_binding_refused")
        tree = self._object("trees", {"tree": [{"path": "claim.json", "mode": "100644",
                                               "type": "blob", "sha": blob}]})
        tree_raw = b"100644 claim.json\0" + bytes.fromhex(blob)
        require(tree == sha1(b"tree " + str(len(tree_raw)).encode() + b"\0" + tree_raw).hexdigest(),
                "claim_tree_binding_refused")
        commit = self._object("commits", {"message": "canon-claim " + digest(value),
                                         "tree": tree, "parents": []})
        observed = self.channel.get(PREFIX + "/git/commits/" + commit)
        require(observed["status"] == 200 and observed["data"].get("sha") == commit
            and observed["data"].get("tree", {}).get("sha") == tree
            and observed["data"].get("parents") == []
            and observed["data"].get("message") == "canon-claim " + digest(value),
            "claim_commit_binding_refused")
        return commit

    def _key(self, key, commit):
        ref = NAMESPACE + key
        result = self.channel._create(PREFIX + "/git/refs", {"ref": ref, "sha": commit})
        require(result["status"] == 201 and result["data"].get("ref") == ref
            and result["data"].get("object", {}) == {
                "type": "commit", "sha": commit, "url": "https://api.github.com" +
                PREFIX + "/git/commits/" + commit}, "claim_key_consumed_or_unproven")
        # Authenticated exact readback, not a receipt hash supplied by caller.
        observed = self.channel.get(PREFIX + "/git/matching-refs/" + ref[5:])
        require(observed["status"] == 200 and isinstance(observed["data"], list),
                "claim_readback_unproven")
        exact = [r for r in observed["data"] if r.get("ref") == ref]
        require(len(exact) == 1 and exact[0].get("object", {}).get("type") == "commit"
                and exact[0]["object"].get("sha") == commit, "claim_readback_unproven")

    def _main(self, launch):
        observed = self.channel.get(PREFIX + "/branches/main")
        require(observed["status"] == 200
            and observed["data"].get("commit", {}).get("sha") == launch.runtime_sha,
            "claim_main_changed")

    def reserve(self, launch):
        try:
            self._main(launch)
            commit = self._commit(launch, "reservation")
            self._key("intents/" + launch.intent, commit)
            self._key("builds/" + launch.validation_build_id, commit)
            self._main(launch)
            return {"status": "DURABLE_CLAIM", "contract_hash": launch.fingerprint,
                    "intent": launch.intent, "validation_build_id": launch.validation_build_id,
                    "controller_run_id": launch.controller_run_id, "attempt": 1}
        except BaseException:
            raise ClaimUncertain("claim_reservation_consumed_or_unproven_no_replay") from None

    def inspect_reservation(self, launch):
        value = {"version": 1, "contract": launch.public(), "stage": "reservation",
                 "contract_hash": launch.fingerprint, "attempt": 1}
        raw = canonical(value)
        blob = sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        tree_raw = b"100644 claim.json\0" + bytes.fromhex(blob)
        tree = sha1(b"tree " + str(len(tree_raw)).encode() + b"\0" + tree_raw).hexdigest()
        commits = set()
        for key in ("intents/" + launch.intent, "builds/" + launch.validation_build_id):
            ref = NAMESPACE + key
            result = self.channel.get(PREFIX + "/git/matching-refs/" + ref[5:])
            require(result["status"] == 200 and isinstance(result["data"], list),
                    "reservation_readback_required")
            exact = [r for r in result["data"] if r.get("ref") == ref]
            require(len(exact) == 1 and exact[0].get("object", {}).get("type") == "commit"
                and bool(re.fullmatch(SHA, exact[0]["object"].get("sha", ""))),
                "reservation_readback_required")
            commits.add(exact[0]["object"]["sha"])
        require(len(commits) == 1, "reservation_tuple_mismatch")
        commit = commits.pop()
        observed = self.channel.get(PREFIX + "/git/commits/" + commit)
        require(observed["status"] == 200 and observed["data"].get("sha") == commit
            and observed["data"].get("tree", {}).get("sha") == tree
            and observed["data"].get("parents") == []
            and observed["data"].get("message") == "canon-claim " + digest(value),
            "reservation_content_refused")
        return {"status": "RESERVATION_READBACK", "contract_hash": launch.fingerprint,
                "commit": commit, "execution_admission": False}

    def stage(self, launch, stage):
        require(stage in ("baseline", "initial", "revoke", "reactivate"), "claim_stage_refused")
        try:
            self._main(launch)
            self.inspect_reservation(launch)
            commit = self._commit(launch, stage)
            self._key("stages/" + launch.intent + "/" + stage, commit)
            self._main(launch)
            return {"status": "STAGE_CLAIM", "contract_hash": launch.fingerprint, "stage": stage}
        except BaseException:
            raise ClaimUncertain("stage_claim_consumed_or_unproven_no_replay") from None
