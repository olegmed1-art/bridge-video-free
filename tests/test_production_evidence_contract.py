#!/usr/bin/env python3
"""Permanent production guard for the evidence-preserving 3.1 FREE r25.15 route.

r25.15 is accepted as production only when it remains a thin descendant of
r25.14 -> r25.7 -> r25.6 and changes only the local anonymous speaker-separation
layer. Identity, privacy, methodology and zero-paid-AI guards remain intact.
"""
from pathlib import Path
from types import SimpleNamespace
import json
import os

import bridge_runtime_hardening_r25_15 as runtime
from bridge_output_scoped_idempotency import existing_same_revision_done
import check_completed_job as preflight
import run_master_3_1_free as base


def test_production_route_is_confirmed_r25_15_with_inheritance_chain():
    adapter = Path("run_drive_3_1_free_generic.py").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/bridge-video-3.1-free.yml").read_text(encoding="utf-8")
    runtime_source = Path("bridge_runtime_hardening_r25_15.py").read_text(encoding="utf-8")
    r25_14_source = Path("bridge_runtime_hardening_r25_14.py").read_text(encoding="utf-8")
    r25_7_source = Path("bridge_runtime_hardening_r25_7.py").read_text(encoding="utf-8")
    semantic_v2 = Path("run_master_3_1_free_semantic_v2.py").read_text(encoding="utf-8")
    diarization_v3 = Path("bridge_speaker_diarization_v3.py").read_text(encoding="utf-8")
    diarization_core = Path("bridge_speaker_diarization_v3_core.py").read_text(encoding="utf-8")
    diarization_repair = Path("bridge_speaker_diarization_v3_repair.py").read_text(encoding="utf-8")

    assert "bridge_runtime_hardening_r25_15" in adapter
    assert 'BRIDGE_REQUESTED_ALGORITHM_REVISION: "3.1-free-r25.15"' in workflow
    assert 'BRIDGE_DIARIZATION_ENABLED: "true"' in workflow
    assert "WHISPER_MODEL: small" in workflow
    assert "BRIDGE_REQUESTED_WHISPER_MODEL: medium" not in workflow
    assert 'BRIDGE_PAID_CLOUD: "false"' in workflow
    assert 'BRIDGE_BILLING_FALLBACK: "false"' in workflow
    assert 'git diff-tree --no-commit-id --name-only -r -m "$GITHUB_SHA"' in workflow
    assert "Expected exactly one run request in triggering commit" in workflow

    assert "import bridge_runtime_hardening_r25_14 as previous" in runtime_source
    assert "previous.install(token_func)" in runtime_source
    assert "bridge_speaker_diarization_v3" in runtime_source
    assert "bridge_output_scoped_idempotency" in runtime_source
    assert "semantic_v2._existing_same_revision_done" in runtime_source
    assert "semantic_v2.previous._existing_same_revision_done" in runtime_source
    assert "import bridge_runtime_hardening_r25_7 as previous" in r25_14_source
    assert "previous.install(token_func)" in r25_14_source
    assert "import bridge_runtime_hardening_r25_6 as previous" in r25_7_source
    assert "previous.install(token_func)" in r25_7_source
    assert "import run_master_3_1_free_semantic as previous" in semantic_v2
    assert "METHODOLOGY_PARTIAL" in semantic_v2
    assert "technical_ready_does_not_imply_methodology_ready" in semantic_v2

    combined = "\n".join((diarization_v3, diarization_core, diarization_repair))
    assert "cluster_collapse" in combined
    assert "recluster" in combined.lower()
    assert "3dspeaker" in combined.lower()
    assert "real_person_identity_claimed" in combined
    assert "voice_embedding_persisted" in combined
    assert "cross_lesson_voice_profile_persisted" in combined
    assert '"paid_api": 0' in combined
    assert '"paid_cloud": 0' in combined


def test_runtime_does_not_filter_master_canon_evidence():
    raw_candidate_builder = base.course_link_candidates
    previous = os.environ.get("BRIDGE_REQUESTED_ALGORITHM_REVISION")
    os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = runtime.REVISION
    try:
        runtime.install(lambda: "test-token")
    finally:
        if previous is None:
            os.environ.pop("BRIDGE_REQUESTED_ALGORITHM_REVISION", None)
        else:
            os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = previous
    assert base.course_link_candidates is raw_candidate_builder, (
        "production runtime replaced the master canon-link producer; "
        "retrieval classification must not erase the original evidence set"
    )


def test_terminal_preflight_matches_revision_receipt_contract():
    source = Path("check_completed_job.py").read_text(encoding="utf-8")
    assert "receipt_matches_revision" in source
    assert "knowledge_status_matches_revision" not in source
    assert "CLEANUP_ACK" in source


def test_terminal_preflight_scopes_same_revision_to_output_generation():
    job = "41daa4ca6e09d13e366c578b7c53ae31"
    global_query = preflight.receipt_search_query(job)
    scoped_query = preflight.receipt_search_query(job, "1ProdRepeatOutputFolder")
    assert " in parents" not in global_query
    assert "'1ProdRepeatOutputFolder' in parents" in scoped_query
    assert f"CLEANUP_ACK_{job}.json" in scoped_query
    try:
        preflight.receipt_search_query(job, "bad folder/id")
    except RuntimeError as exc:
        assert "INVALID_OUTPUT_FOLDER_ID" in str(exc)
    else:
        raise AssertionError("invalid Drive id must fail closed")


def test_runtime_same_revision_done_is_scoped_to_requested_output_folder():
    job = "41daa4ca6e09d13e366c578b7c53ae31"
    revision = runtime.REVISION
    done_item = {"id": "done", "modifiedTime": "2026-08-19T13:00:00Z"}
    receipt_item = {"id": "method", "modifiedTime": "2026-08-19T13:00:01Z"}
    payloads = {
        "done": {
            "status": "AI_DONE",
            "job_id": job,
            "algorithmRevision": revision,
            "masterPdf": {"driveId": "pdf-new"},
        },
        "method": {
            "status": "METHODOLOGY_READY",
            "job_id": job,
            "algorithmRevision": revision,
            "masterPdfDriveId": "pdf-new",
        },
    }
    queries = []

    def search(_token, query):
        queries.append(query)
        if "'targetOutput' in parents" not in query:
            return []
        if f"AI_DONE_{job}.json" in query:
            return [done_item]
        if f"METHODOLOGY_READY_{job}.json" in query:
            return [receipt_item]
        return []

    fake = SimpleNamespace(
        base=SimpleNamespace(io=SimpleNamespace(search=search)),
        _read_json=lambda _token, item: payloads.get(item.get("id")),
    )
    previous_output = os.environ.get("BRIDGE_OUTPUT_FOLDER_ID")
    os.environ["BRIDGE_OUTPUT_FOLDER_ID"] = "targetOutput"
    try:
        found = existing_same_revision_done(fake, "token", job, revision)
    finally:
        if previous_output is None:
            os.environ.pop("BRIDGE_OUTPUT_FOLDER_ID", None)
        else:
            os.environ["BRIDGE_OUTPUT_FOLDER_ID"] = previous_output

    assert found == payloads["done"]
    assert queries
    assert all("'targetOutput' in parents" in query for query in queries)

    foreign_queries = []

    def foreign_only_search(_token, query):
        foreign_queries.append(query)
        if " in parents" not in query and f"AI_DONE_{job}.json" in query:
            return [done_item]
        if " in parents" not in query and f"METHODOLOGY_READY_{job}.json" in query:
            return [receipt_item]
        return []

    fake.base.io.search = foreign_only_search
    os.environ["BRIDGE_OUTPUT_FOLDER_ID"] = "freshOutput"
    try:
        found_foreign = existing_same_revision_done(fake, "token", job, revision)
    finally:
        if previous_output is None:
            os.environ.pop("BRIDGE_OUTPUT_FOLDER_ID", None)
        else:
            os.environ["BRIDGE_OUTPUT_FOLDER_ID"] = previous_output

    assert found_foreign is None
    assert foreign_queries
    assert all("'freshOutput' in parents" in query for query in foreign_queries)

    # The inherited r25 semantic adapter has no _read_json helper. Its scoped
    # gate must therefore use base._read_text without falling back to global search.
    text_queries = []

    def text_search(_token, query):
        text_queries.append(query)
        if "'nestedOutput' in parents" not in query:
            return []
        if f"AI_DONE_{job}.json" in query:
            return [done_item]
        if f"METHODOLOGY_READY_{job}.json" in query:
            return [receipt_item]
        return []

    nested = SimpleNamespace(
        base=SimpleNamespace(
            io=SimpleNamespace(search=text_search),
            _read_text=lambda _token, item: json.dumps(payloads[item["id"]]),
        )
    )
    os.environ["BRIDGE_OUTPUT_FOLDER_ID"] = "nestedOutput"
    try:
        nested_found = existing_same_revision_done(nested, "token", job, revision)
    finally:
        if previous_output is None:
            os.environ.pop("BRIDGE_OUTPUT_FOLDER_ID", None)
        else:
            os.environ["BRIDGE_OUTPUT_FOLDER_ID"] = previous_output
    assert nested_found == payloads["done"]
    assert text_queries
    assert all("'nestedOutput' in parents" in query for query in text_queries)


def test_periodic_auto_discovery_remains_disabled():
    source = Path(".github/workflows/bridge-video-auto-discovery.yml").read_text(
        encoding="utf-8"
    )
    assert "schedule:" not in source
    assert "actions: write" not in source
    assert "GOOGLE_DRIVE_OAUTH_JSON" not in source
    assert "discover_next_drive_job.py" not in source
    assert "workflow_dispatch:" in source
    assert "AUTO_DISCOVERY_DISABLED" in source


if __name__ == "__main__":
    test_production_route_is_confirmed_r25_15_with_inheritance_chain()
    test_runtime_does_not_filter_master_canon_evidence()
    test_terminal_preflight_matches_revision_receipt_contract()
    test_terminal_preflight_scopes_same_revision_to_output_generation()
    test_runtime_same_revision_done_is_scoped_to_requested_output_folder()
    test_periodic_auto_discovery_remains_disabled()
    print("PRODUCTION_R25_15_EVIDENCE_CONTRACT: PASS")
