from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from .tournament_portfolio_release_gate_v3 import build_portfolio_aware_release_gate


class TournamentRenderEvidenceError(ValueError):
    pass


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sha256(value: Any, field: str) -> str:
    text = str(value or "").strip().lower()
    if not _SHA256_RE.fullmatch(text):
        raise TournamentRenderEvidenceError(f"{field} must be a lowercase SHA-256 hex digest")
    return text


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise TournamentRenderEvidenceError(f"{field} must be a positive integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise TournamentRenderEvidenceError(f"{field} must be a positive integer") from exc
    if number <= 0:
        raise TournamentRenderEvidenceError(f"{field} must be a positive integer")
    return number


def _string_sequence(value: Any, field: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TournamentRenderEvidenceError(f"{field} must be a sequence")
    out = [str(item).strip() for item in value]
    if not out or any(not item for item in out):
        raise TournamentRenderEvidenceError(f"{field} must contain non-empty values")
    if len(out) != len(set(out)):
        raise TournamentRenderEvidenceError(f"{field} must contain unique values")
    return out


def validate_render_release_evidence(
    *,
    coverage_manifest: Mapping[str, Any],
    render_evidence: Mapping[str, Any],
    visual_qa_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate rendered-report and visual-QA evidence against the exact coverage plan.

    This closes the fail-open path where a caller could previously supply only an
    expected slide-key list plus ``visual_qa_pass=True``. The stricter release path
    requires immutable artifact identity, exact coverage-manifest binding and a QA
    receipt bound to the same rendered artifact.
    """
    if coverage_manifest.get("schema") != "tournament-coverage-manifest-v1":
        raise TournamentRenderEvidenceError("unsupported coverage manifest schema")
    provider_native_key = str(coverage_manifest.get("provider_native_key") or "").strip()
    if not provider_native_key:
        raise TournamentRenderEvidenceError("coverage manifest provider_native_key is required")
    expected_slide_keys = _string_sequence(coverage_manifest.get("expected_slide_keys"), "expected_slide_keys")
    coverage_sha256 = _canonical_sha256(coverage_manifest)

    if render_evidence.get("schema") != "tournament-render-evidence-v1":
        raise TournamentRenderEvidenceError("unsupported render evidence schema")
    if render_evidence.get("normative_algorithm_version") != "1.4":
        raise TournamentRenderEvidenceError("render evidence normative version mismatch")
    if str(render_evidence.get("provider_native_key") or "").strip() != provider_native_key:
        raise TournamentRenderEvidenceError("render evidence provider identity mismatch")
    if _sha256(render_evidence.get("coverage_manifest_sha256"), "coverage_manifest_sha256") != coverage_sha256:
        raise TournamentRenderEvidenceError("render evidence is not bound to the exact coverage manifest")

    artifact = render_evidence.get("artifact")
    if not isinstance(artifact, Mapping):
        raise TournamentRenderEvidenceError("render artifact metadata is required")
    render_sha256 = _sha256(artifact.get("sha256"), "artifact.sha256")
    render_size = _positive_int(artifact.get("size_bytes"), "artifact.size_bytes")
    media_type = str(artifact.get("media_type") or "").strip().lower()
    if media_type != "application/pdf":
        raise TournamentRenderEvidenceError("release visual evidence must identify a rendered PDF artifact")

    actual_slide_keys = _string_sequence(render_evidence.get("slide_keys"), "render_evidence.slide_keys")
    if actual_slide_keys != expected_slide_keys:
        raise TournamentRenderEvidenceError("rendered slide keys do not exactly match coverage order")
    rendered_page_count = _positive_int(render_evidence.get("rendered_page_count"), "rendered_page_count")
    if rendered_page_count != len(actual_slide_keys):
        raise TournamentRenderEvidenceError("rendered page count does not match slide-key count")

    renderer = render_evidence.get("renderer")
    if not isinstance(renderer, Mapping):
        raise TournamentRenderEvidenceError("renderer provenance is required")
    if not str(renderer.get("name") or "").strip() or not str(renderer.get("version") or "").strip():
        raise TournamentRenderEvidenceError("renderer name and version are required")
    render_provenance = render_evidence.get("provenance")
    if not isinstance(render_provenance, Mapping) or not render_provenance:
        raise TournamentRenderEvidenceError("render provenance is required")

    if visual_qa_evidence.get("schema") != "tournament-visual-qa-evidence-v1":
        raise TournamentRenderEvidenceError("unsupported visual QA evidence schema")
    if visual_qa_evidence.get("normative_algorithm_version") != "1.4":
        raise TournamentRenderEvidenceError("visual QA normative version mismatch")
    if str(visual_qa_evidence.get("provider_native_key") or "").strip() != provider_native_key:
        raise TournamentRenderEvidenceError("visual QA provider identity mismatch")
    if _sha256(visual_qa_evidence.get("coverage_manifest_sha256"), "visual_qa.coverage_manifest_sha256") != coverage_sha256:
        raise TournamentRenderEvidenceError("visual QA is not bound to the exact coverage manifest")
    if _sha256(visual_qa_evidence.get("render_artifact_sha256"), "visual_qa.render_artifact_sha256") != render_sha256:
        raise TournamentRenderEvidenceError("visual QA is not bound to the rendered artifact")
    checked_slide_count = _positive_int(visual_qa_evidence.get("checked_slide_count"), "checked_slide_count")
    if checked_slide_count != rendered_page_count:
        raise TournamentRenderEvidenceError("visual QA did not check every rendered page")
    if str(visual_qa_evidence.get("status") or "").strip().upper() != "PASS":
        raise TournamentRenderEvidenceError("visual QA status is not PASS")
    if visual_qa_evidence.get("pass") is not True:
        raise TournamentRenderEvidenceError("visual QA pass flag is not true")
    hard_failures = visual_qa_evidence.get("hard_failures")
    if isinstance(hard_failures, (str, bytes)) or not isinstance(hard_failures, Sequence):
        raise TournamentRenderEvidenceError("visual QA hard_failures must be a sequence")
    if list(hard_failures):
        raise TournamentRenderEvidenceError("visual QA contains hard failures")
    qa_engine = visual_qa_evidence.get("qa_engine")
    if not isinstance(qa_engine, Mapping):
        raise TournamentRenderEvidenceError("visual QA engine provenance is required")
    if not str(qa_engine.get("name") or "").strip() or not str(qa_engine.get("version") or "").strip():
        raise TournamentRenderEvidenceError("visual QA engine name and version are required")
    qa_provenance = visual_qa_evidence.get("provenance")
    if not isinstance(qa_provenance, Mapping) or not qa_provenance:
        raise TournamentRenderEvidenceError("visual QA provenance is required")

    return {
        "schema": "tournament-render-release-evidence-validation-v1",
        "normative_algorithm_version": "1.4",
        "provider_native_key": provider_native_key,
        "coverage_manifest_sha256": coverage_sha256,
        "render_artifact_sha256": render_sha256,
        "render_artifact_size_bytes": render_size,
        "rendered_slide_keys": actual_slide_keys,
        "rendered_page_count": rendered_page_count,
        "visual_qa_checked_slide_count": checked_slide_count,
        "visual_qa_pass": True,
        "render_evidence_gate_pass": True,
        "visual_qa_evidence_gate_pass": True,
    }


def build_evidence_bound_portfolio_release_gate(
    *,
    preanalysis_gate: Mapping[str, Any],
    coverage_manifest: Mapping[str, Any],
    mp_availability: Mapping[str, Any],
    event_teacher_review_gate: Mapping[str, Any],
    portfolio_episode_coverage_handoff: Mapping[str, Any] | None,
    render_evidence: Mapping[str, Any],
    visual_qa_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    validated = validate_render_release_evidence(
        coverage_manifest=coverage_manifest,
        render_evidence=render_evidence,
        visual_qa_evidence=visual_qa_evidence,
    )
    base = build_portfolio_aware_release_gate(
        preanalysis_gate=preanalysis_gate,
        coverage_manifest=coverage_manifest,
        mp_availability=mp_availability,
        event_teacher_review_gate=event_teacher_review_gate,
        portfolio_episode_coverage_handoff=portfolio_episode_coverage_handoff,
        rendered_slide_keys=validated["rendered_slide_keys"],
        visual_qa_pass=True,
    )
    return {
        **base,
        "schema": "tournament-v1.4-evidence-bound-portfolio-release-gate-v1",
        "render_release_evidence": validated,
        "render_evidence_gate_enforced": True,
        "visual_qa_evidence_gate_enforced": True,
        "bare_visual_qa_boolean_accepted": False,
        "bare_rendered_slide_key_list_accepted": False,
    }
