from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping


class TournamentReleaseProvenanceError(ValueError):
    pass


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise TournamentReleaseProvenanceError(f"{field} is required")
    return text


def _sha256_text(value: Any, field: str) -> str:
    text = _required_text(value, field).lower()
    if not _SHA256_RE.fullmatch(text):
        raise TournamentReleaseProvenanceError(f"{field} must be a lowercase SHA-256 digest")
    return text


def build_release_provenance_receipt(
    *,
    preanalysis_gate: Mapping[str, Any],
    coverage_manifest: Mapping[str, Any],
    mp_availability: Mapping[str, Any],
    event_teacher_review_gate: Mapping[str, Any],
    portfolio_episode_coverage_handoff: Mapping[str, Any],
    artifact_derived_release_gate: Mapping[str, Any],
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    """Create one content-addressed receipt for a fully releasable tournament report.

    The receipt is intentionally unavailable while any upstream gate is unresolved.
    It binds the final rendered artifact to the exact pre-analysis, coverage,
    teacher-review, episode-coverage and scoring-context state used for release.
    """
    if artifact_derived_release_gate.get("schema") != "tournament-v1.4-artifact-derived-portfolio-release-gate-v1":
        raise TournamentReleaseProvenanceError("artifact-derived final release gate is required")
    if artifact_derived_release_gate.get("final_report_release_ready") is not True:
        raise TournamentReleaseProvenanceError("final report is not release-ready")
    if artifact_derived_release_gate.get("artifact_derived_render_evidence_enforced") is not True:
        raise TournamentReleaseProvenanceError("artifact-derived render evidence is not enforced")
    for field in (
        "caller_supplied_render_sha_accepted",
        "caller_supplied_render_size_accepted",
        "caller_supplied_slide_order_accepted",
        "caller_supplied_visual_qa_pass_accepted",
        "automatic_episode_scoring_allowed",
        "automatic_student_error_attribution_allowed",
        "automatic_methodology_invention_allowed",
    ):
        if artifact_derived_release_gate.get(field) is not False:
            raise TournamentReleaseProvenanceError(f"release boundary weakened: {field}")

    tournament = preanalysis_gate.get("tournament")
    if not isinstance(tournament, Mapping):
        raise TournamentReleaseProvenanceError("preanalysis tournament metadata missing")
    provider_native_key = _required_text(tournament.get("provider_native_key"), "provider_native_key")
    if _required_text(coverage_manifest.get("provider_native_key"), "coverage provider") != provider_native_key:
        raise TournamentReleaseProvenanceError("coverage provider identity mismatch")

    event_id = _required_text(artifact_derived_release_gate.get("event_id"), "release event_id")
    if _required_text(event_teacher_review_gate.get("event_id"), "review event_id") != event_id:
        raise TournamentReleaseProvenanceError("teacher-review event identity mismatch")
    if _required_text(portfolio_episode_coverage_handoff.get("event_id"), "handoff event_id") != event_id:
        raise TournamentReleaseProvenanceError("coverage handoff event identity mismatch")

    portfolio_id = _required_text(
        artifact_derived_release_gate.get("teacher_review_portfolio_id"),
        "teacher_review_portfolio_id",
    )
    if _required_text(event_teacher_review_gate.get("portfolio_id"), "review portfolio_id") != portfolio_id:
        raise TournamentReleaseProvenanceError("teacher-review portfolio identity mismatch")
    if _required_text(portfolio_episode_coverage_handoff.get("portfolio_id"), "handoff portfolio_id") != portfolio_id:
        raise TournamentReleaseProvenanceError("coverage handoff portfolio identity mismatch")
    if portfolio_episode_coverage_handoff.get("coverage_manifest") != coverage_manifest:
        raise TournamentReleaseProvenanceError("coverage handoff does not contain exact release coverage manifest")

    render = artifact_derived_release_gate.get("render_release_evidence")
    if not isinstance(render, Mapping) or render.get("render_evidence_gate_pass") is not True or render.get("visual_qa_evidence_gate_pass") is not True:
        raise TournamentReleaseProvenanceError("validated render/QA evidence missing from final release gate")
    render_sha256 = _sha256_text(render.get("render_artifact_sha256"), "render_artifact_sha256")
    render_size = int(render.get("render_artifact_size_bytes") or 0)
    if render_size <= 0:
        raise TournamentReleaseProvenanceError("render artifact size missing")
    if not isinstance(provenance, Mapping) or not provenance:
        raise TournamentReleaseProvenanceError("release provenance is required")

    component_digests = {
        "preanalysis_gate_sha256": _canonical_sha256(preanalysis_gate),
        "coverage_manifest_sha256": _canonical_sha256(coverage_manifest),
        "mp_availability_sha256": _canonical_sha256(mp_availability),
        "event_teacher_review_gate_sha256": _canonical_sha256(event_teacher_review_gate),
        "portfolio_episode_coverage_handoff_sha256": _canonical_sha256(portfolio_episode_coverage_handoff),
        "artifact_derived_release_gate_sha256": _canonical_sha256(artifact_derived_release_gate),
    }
    identity = {
        "schema": "tournament-v1.4-release-provenance-receipt-v1",
        "normative_algorithm_version": "1.4",
        "provider_native_key": provider_native_key,
        "event_id": event_id,
        "portfolio_id": portfolio_id,
        "render_artifact": {
            "sha256": render_sha256,
            "size_bytes": render_size,
            "page_count": int(render.get("rendered_page_count") or 0),
        },
        "component_digests": component_digests,
        "provenance": dict(provenance),
        "final_report_release_ready": True,
        "automatic_teacher_decisions_used": False,
        "automatic_episode_scoring_used": False,
        "automatic_methodology_mapping_used": False,
        "automatic_student_error_attribution_used": False,
        "causal_error_attribution_allowed": False,
    }
    if identity["render_artifact"]["page_count"] <= 0:
        raise TournamentReleaseProvenanceError("render page count missing")
    return {
        **identity,
        "release_id": _canonical_sha256(identity),
        "content_addressed_release_receipt": True,
    }


def verify_release_provenance_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    if receipt.get("schema") != "tournament-v1.4-release-provenance-receipt-v1":
        raise TournamentReleaseProvenanceError("unsupported release provenance receipt schema")
    if receipt.get("normative_algorithm_version") != "1.4":
        raise TournamentReleaseProvenanceError("release provenance normative version mismatch")
    if receipt.get("final_report_release_ready") is not True:
        raise TournamentReleaseProvenanceError("receipt does not represent a release-ready report")
    for field in (
        "automatic_teacher_decisions_used",
        "automatic_episode_scoring_used",
        "automatic_methodology_mapping_used",
        "automatic_student_error_attribution_used",
        "causal_error_attribution_allowed",
    ):
        if receipt.get(field) is not False:
            raise TournamentReleaseProvenanceError(f"release receipt boundary weakened: {field}")

    _required_text(receipt.get("provider_native_key"), "provider_native_key")
    _required_text(receipt.get("event_id"), "event_id")
    _required_text(receipt.get("portfolio_id"), "portfolio_id")
    render = receipt.get("render_artifact")
    if not isinstance(render, Mapping):
        raise TournamentReleaseProvenanceError("render artifact metadata missing")
    _sha256_text(render.get("sha256"), "render_artifact.sha256")
    if int(render.get("size_bytes") or 0) <= 0 or int(render.get("page_count") or 0) <= 0:
        raise TournamentReleaseProvenanceError("render artifact size/page count invalid")

    component_digests = receipt.get("component_digests")
    required_components = {
        "preanalysis_gate_sha256",
        "coverage_manifest_sha256",
        "mp_availability_sha256",
        "event_teacher_review_gate_sha256",
        "portfolio_episode_coverage_handoff_sha256",
        "artifact_derived_release_gate_sha256",
    }
    if not isinstance(component_digests, Mapping) or set(component_digests) != required_components:
        raise TournamentReleaseProvenanceError("release component digest set mismatch")
    for key, value in component_digests.items():
        _sha256_text(value, f"component_digests.{key}")
    provenance = receipt.get("provenance")
    if not isinstance(provenance, Mapping) or not provenance:
        raise TournamentReleaseProvenanceError("release provenance missing")

    identity = {key: value for key, value in receipt.items() if key not in {"release_id", "content_addressed_release_receipt"}}
    expected = _canonical_sha256(identity)
    if _sha256_text(receipt.get("release_id"), "release_id") != expected:
        raise TournamentReleaseProvenanceError("release provenance receipt digest mismatch")
    if receipt.get("content_addressed_release_receipt") is not True:
        raise TournamentReleaseProvenanceError("content-addressed receipt marker missing")
    return {
        "schema": "tournament-v1.4-release-provenance-verification-v1",
        "release_id": expected,
        "status": "PASS",
        "content_addressed_release_receipt": True,
        "release_safety_boundaries_verified": True,
    }
