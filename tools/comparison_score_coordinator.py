"""Default-off coordinator bridge. No resident fallback or evaluator launcher."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

INTEGRATION_BUNDLE = "6732ee1671c233a85d6c81dc3ae9040313ba576e04763093125cfce205a71806"
VIDEO_BUNDLE = "08aeba3587d4cf7ac231f121065cafad954b94cd7f0abad304dd9e3be6a118b3"
SCORER_PIN = "2343d67f7f5b2e2631f4c6bf8d6845187113b68b9da54147eb6ffa204fb9db4b"
API_PIN = "3335e0e87814b1f646ece5bc99ee669ec76c755f"
LIMITS = {"cpu_seconds": 120, "wall_seconds": 180, "memory_mib": 512,
          "processes": 64, "file_mib": 16, "workspace_mib": 128}
SIDECAR_FIELDS = {"schema", "config_sha256", "job_id", "job_hash", "source_file_id",
                  "source_version", "source_sha256", "sealed_manifest_sha256",
                  "clip_binding_sha256", "integration_bundle_sha256",
                  "video_bundle_sha256", "scorer_sha256", "api_checkout_sha",
                  "scorer_path", "api_checkout", "gold", "pts_index", "gold_freeze",
                  "capture_review_path", "control_root", "evaluation_workspace"}

class CoordinatorBlocked(RuntimeError):
    pass

def require(ok, message):
    if not ok:
        raise CoordinatorBlocked(message)

def components():
    # Source/loaded-code inventories must admit these exact reviewed components.
    # Imports are fixed; no caller-supplied executor, command or callback is accepted.
    from universal_video import comparison_sandbox as video
    from tools import comparison_score_handoff as handoff
    return video, handoff

def sidecar(path, pin, video):
    raw = video.protected_json(path, with_bytes=True)[1]
    require(hashlib.sha256(raw).hexdigest() == pin, "score sidecar digest changed")
    value = json.loads(raw)
    require(set(value) == SIDECAR_FIELDS and
            value["schema"] == "comparison-score-coordinator-bindings/v1",
            "exact score sidecar fields required")
    return value

def no_overlap(paths, roots):
    for path in paths:
        path = Path(path).resolve()
        for root in roots:
            root = Path(root).resolve()
            require(path != root and not path.is_relative_to(root)
                    and not root.is_relative_to(path), "scoring control overlaps recognition role")

def admit(config, *, sidecar_path, sidecar_sha256, qualification_file, video, handoff):
    # Same exact v7 entry, default-off qualified API and preflight; no schema widening.
    config = video.authorized_entry(config)
    q, observation = video.qualify(config, qualification_file)
    qualification_pin = video.qualification_pin(q, observation)
    bindings = sidecar(sidecar_path, sidecar_sha256, video)
    expected = {"config_sha256": video.digest(config),
                **{k: config[k] for k in ("job_id", "job_hash", "source_file_id",
                   "source_version", "source_sha256", "sealed_manifest_sha256",
                   "clip_binding_sha256")},
                "integration_bundle_sha256": INTEGRATION_BUNDLE,
                "video_bundle_sha256": VIDEO_BUNDLE, "scorer_sha256": SCORER_PIN,
                "api_checkout_sha": API_PIN}
    require(all(bindings[k] == v for k, v in expected.items()), "score job/source/code binding mismatch")
    sealed = video._preflight(config)
    score = handoff.scorer_at(video.absolute(bindings["scorer_path"]))
    inputs, pins = {}, {}
    for name in ("gold", "pts_index", "gold_freeze"):
        item = bindings[name]
        require(isinstance(item, dict) and set(item) == {"path", "sha256"},
                "exact independent input pin required")
        path = video.absolute(item["path"])
        inputs[name] = score.sealed_json(path, item["sha256"])
        pins[name] = item["sha256"]
    gold, pts, freeze = (inputs[k] for k in ("gold", "pts_index", "gold_freeze"))
    require(score.frozen(pins["gold"], pins["pts_index"], freeze),
            "independent pre-output gold/PTS freeze required")
    frozen_time = datetime.fromisoformat(freeze["frozen_utc"].replace("Z", "+00:00"))
    require(frozen_time.utcoffset() is not None and
            frozen_time <= datetime.now(timezone.utc), "pre-output freeze time invalid")
    require(pins["gold"] == sealed["inputs"]["gold"]["sha256"] and
            Path(bindings["gold"]["path"]).resolve() ==
            Path(sealed["inputs"]["gold"]["path"]).resolve(), "sealed gold differs from independent gold")
    require(gold.get("schema") == score.TAXONOMY and
            gold.get("source_sha256") == pts.get("source_sha256") == config["source_sha256"]
            and gold.get("clip_sha256") == pts.get("clip_sha256") ==
            sealed["inputs"]["video"]["sha256"], "original source/clip/PTS mismatch")
    score.validate_gold(gold)
    score.Alignment(pts, gold)
    # More controls than v7 knows about: they must not exist under any recognition role.
    controls = [sidecar_path, qualification_file, bindings["scorer_path"],
                bindings["api_checkout"], bindings["capture_review_path"],
                bindings["control_root"], bindings["evaluation_workspace"],
                *[bindings[n]["path"] for n in ("gold", "pts_index", "gold_freeze")]]
    controls = [video.absolute(str(p)) for p in controls]
    roots = [q["workspace_root"],
             *[r["root"] for r in q["runtime_trees"]],
             *[r["root"] for r in q["source_trees"].values()],
             *[sealed["inputs"][n]["path"] for n in ("video", "reference", "profile", "sprite")]]
    no_overlap(controls, roots)
    control = Path(bindings["control_root"])
    evaluation = Path(bindings["evaluation_workspace"])
    no_overlap([control], [evaluation])
    require(not evaluation.exists(), "new separate evaluator workspace required")
    require(control.parent.resolve() == Path(q["ledger_root"]).resolve(),
            "controls must use the qualified persistent ledger")
    return config, bindings, score, handoff, qualification_pin

def capture_once(*, enabled=False, config, sidecar_path, sidecar_sha256, qualification_file=None):
    require(enabled is True, "coordinator disabled")
    video, handoff = components()
    config, b, _, _, admitted_pin = admit(config, sidecar_path=sidecar_path,
                           sidecar_sha256=sidecar_sha256,
                           qualification_file=qualification_file, video=video, handoff=handoff)
    control = Path(b["control_root"])
    require(not control.exists() and control.parent.is_dir(), "new durable control reservation required")
    control.mkdir(mode=0o700)
    fd = os.open(control.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    # Durable reservation before capture. Failure or unknown state never permits rerun.
    started = {"schema": "comparison-score-coordinator-stage/v1", "state": "STARTED",
               "sidecar_sha256": sidecar_sha256, "config_sha256": video.digest(config),
               "qualification_pin": admitted_pin}
    handoff.write_new(control / "started.json", handoff.encoded(started))
    raw = video.run_qualified_comparison(config, qualification_file=qualification_file)
    expected_raw = Path(video.protected_json(qualification_file)["workspace_root"]) / (
        "comparison-" + config["job_hash"]) / "raw"
    require(Path(raw).resolve() == expected_raw.resolve(), "unexpected qualified raw return")
    # Recheck pins and role boundaries after capture before issuing continuation.
    final = admit(config, sidecar_path=sidecar_path, sidecar_sha256=sidecar_sha256,
                  qualification_file=qualification_file, video=video, handoff=handoff)
    require(final[4] == admitted_pin, "capture qualification changed; no renewal")
    rows = handoff.inventory(raw)  # Existing strict 4096-file / 256-MiB limits.
    stage = {**started, "state": "CAPTURED_UNSCORED", "raw": str(Path(raw).resolve()),
             "inventory_sha256": handoff.inventory_sha256(rows),
             "accuracy_evaluated": False, "promotion_allowed": False}
    encoded = handoff.encoded(stage)
    path = control / "captured.json"
    handoff.write_new(path, encoded)
    return {"stage": str(path), "stage_sha256": handoff.digest(encoded),
            "inventory_sha256": stage["inventory_sha256"]}

def freeze_after_review(*, enabled=False, config, sidecar_path, sidecar_sha256,
                        qualification_file, stage_path, stage_sha256, review_sha256):
    require(enabled is True, "coordinator disabled")
    video, handoff = components()
    config, b, score, _, admitted_pin = admit(config, sidecar_path=sidecar_path,
                              sidecar_sha256=sidecar_sha256,
                              qualification_file=qualification_file, video=video, handoff=handoff)
    control = Path(b["control_root"]).resolve()
    require(Path(stage_path).resolve() == control / "captured.json", "stage outside coordinator controls")
    stage = score.sealed_json(stage_path, stage_sha256)
    require(stage.get("schema") == "comparison-score-coordinator-stage/v1"
            and stage.get("state") == "CAPTURED_UNSCORED"
            and stage.get("sidecar_sha256") == sidecar_sha256
            and stage.get("config_sha256") == video.digest(config)
            and stage.get("qualification_pin") == admitted_pin, "sealed captured stage required")
    expected_raw = Path(video.protected_json(qualification_file)["workspace_root"]) / (
        "comparison-" + config["job_hash"]) / "raw"
    require(Path(stage["raw"]).resolve() == expected_raw.resolve(), "stage raw binding mismatch")
    rows = handoff.inventory(expected_raw)
    require(handoff.inventory_sha256(rows) == stage["inventory_sha256"],
            "capture changed before independent review")
    review = score.sealed_json(b["capture_review_path"], review_sha256)
    require(review.get("comparison_inventory_sha256") == stage["inventory_sha256"],
            "review did not bind admitted capture")
    frozen = handoff.freeze_capture(
        enabled=True, scorer=score, comparison=expected_raw, state_dir=control / "freeze",
        gold=b["gold"]["path"], gold_sha256=b["gold"]["sha256"],
        index=b["pts_index"]["path"], index_sha256=b["pts_index"]["sha256"],
        freeze=b["gold_freeze"]["path"], freeze_sha256=b["gold_freeze"]["sha256"],
        review=b["capture_review_path"], review_sha256=review_sha256)
    # Returned pins must be persisted OUT OF BAND before the evaluator request is used.
    return {**frozen, "sidecar_sha256": sidecar_sha256, "stage_sha256": stage_sha256,
            "promotion_allowed": False}

def evaluator_request(*, enabled=False, config, sidecar_path, sidecar_sha256,
                      qualification_file, handoff_path, handoff_sha256, stage_sha256):
    require(enabled is True, "coordinator disabled")
    video, handoff = components()
    config, b, score, _, admitted_pin = admit(config, sidecar_path=sidecar_path,
                              sidecar_sha256=sidecar_sha256,
                              qualification_file=qualification_file, video=video, handoff=handoff)
    control = Path(b["control_root"]).resolve()
    require(Path(handoff_path).resolve() == control / "freeze" / "handoff.json",
            "handoff outside coordinator controls")
    stage = score.sealed_json(control / "captured.json", stage_sha256)
    require(stage.get("schema") == "comparison-score-coordinator-stage/v1"
            and stage.get("state") == "CAPTURED_UNSCORED"
            and stage.get("sidecar_sha256") == sidecar_sha256
            and stage.get("config_sha256") == video.digest(config)
            and stage.get("qualification_pin") == admitted_pin, "sealed captured stage required")
    frozen = score.sealed_json(handoff_path, handoff_sha256)
    expected_raw = Path(video.protected_json(qualification_file)["workspace_root"]) / (
        "comparison-" + config["job_hash"]) / "raw"
    require(Path(stage["raw"]).resolve() == expected_raw.resolve(), "stage raw binding mismatch")
    rows = handoff.inventory(expected_raw)
    require(handoff.inventory_sha256(rows) == stage["inventory_sha256"],
            "capture inventory differs from captured stage")
    require(Path(frozen["comparison"]).resolve() == expected_raw.resolve(),
            "evaluator capture binding mismatch")
    for name, target in (("gold", "gold"), ("index", "pts_index"), ("freeze", "gold_freeze")):
        require(frozen[name + "_sha256"] == b[target]["sha256"]
                and Path(frozen[name]).resolve() == Path(b[target]["path"]).resolve(),
                "evaluator independent input binding mismatch")
    require(Path(frozen["capture"]).resolve() == control / "freeze" / "capture.json",
            "evaluator capture manifest binding mismatch")
    # Authenticate complete capture/evidence again; future executor must repeat before exec.
    gold_freeze = score.sealed_json(b["gold_freeze"]["path"], b["gold_freeze"]["sha256"])
    gold = score.sealed_json(b["gold"]["path"], b["gold"]["sha256"])
    score.load_comparison(expected_raw, b["gold"]["sha256"], gold["clip_sha256"], gold_freeze,
                          capture_path=frozen["capture"], capture_hash=frozen["capture_sha256"],
                          index_hash=b["pts_index"]["sha256"])
    output = Path(b["evaluation_workspace"]) / "report.json"
    argv = handoff.scoring_argv(handoff_path=handoff_path, handoff_sha256=handoff_sha256,
                               scorer_path=b["scorer_path"], api_checkout=b["api_checkout"],
                               output=output)
    request = {"schema": "comparison-bounded-evaluator-request/v1",
               "state": "REQUEST_ONLY_NOT_RUN", "config_sha256": video.digest(config),
               "sidecar_sha256": sidecar_sha256, "handoff_sha256": handoff_sha256,
               "stage_sha256": stage_sha256, "recognition_qualification_pin": admitted_pin,
               "scorer_sha256": SCORER_PIN, "api_checkout_sha": API_PIN,
               "argv": argv, "limits": dict(LIMITS),
               "read_only": [str(expected_raw), b["scorer_path"], b["api_checkout"],
                             *[str(Path(frozen[n]).resolve()) for n in ("gold", "index", "freeze", "capture")]],
               "write_only_workspace": b["evaluation_workspace"], "output": str(output),
               "runtime_requires": ["separate credential-free qualified scoring identity/view",
                   "initial host UID mapping, namespaces, no_new_privs, zero capabilities",
                   "OS network denial, finite exclusive cgroup and aggregate tmpfs quotas",
                   "immutable admitted scorer/helper/API/Git bytes and safe Git config",
                   "read-only unchanged raw/independent inputs, no recognition descendants",
                   "fixed-argv execution only, deadlines, subtree termination and receipt"],
               "accuracy_evaluated": False, "promotion_allowed": False}
    raw = handoff.encoded(request)
    path = control / "evaluator-request.json"
    handoff.write_new(path, raw)
    return {"request": str(path), "request_sha256": handoff.digest(raw)}

def execute_evaluator(*, enabled=False, request_path=None, request_sha256=None):
    require(enabled is True, "coordinator disabled")
    # No arbitrary command/callback, subprocess or recognition-launcher reuse.
    # A separate source-reviewed, OS-qualified evaluator implementation is still required.
    raise CoordinatorBlocked("bounded evaluator executor unavailable; request is not runtime qualification")
