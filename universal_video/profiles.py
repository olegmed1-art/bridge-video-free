"""Named processing profiles for the universal video engine.

Profiles select stages only. They do not encode teaching methodology or bridge
agreements. Domain-specific interpretation is delegated to optional plugins.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessingProfile:
    name: str
    stages: tuple[str, ...]
    domain_plugin: str | None = None
    description: str = ""


CORE = (
    "media_preflight",
    "audio_extract",
    "transcribe",
    "transcript_qc",
    "timeline",
    "package",
)

PROFILES: dict[str, ProcessingProfile] = {
    "transcript_only": ProcessingProfile(
        "transcript_only",
        CORE,
        description="Reliable timestamped transcript plus QC/provenance.",
    ),
    "educational": ProcessingProfile(
        "educational",
        CORE[:-1] + ("keyframes", "content_segments", "educational_candidates", "package"),
        description="Domain-neutral educational segmentation after transcription.",
    ),
    "bridge_lesson": ProcessingProfile(
        "bridge_lesson",
        CORE[:-1]
        + (
            "keyframes",
            "speaker_structure",
            "bridge_context",
            "bridge_positions",
            "dds3_optional",
            "educational_candidates",
            "package",
        ),
        domain_plugin="bridge",
        description="Bridge lesson or individual bridge session.",
    ),
    "bridge_lesson_3_1_test": ProcessingProfile(
        "bridge_lesson_3_1_test",
        CORE[:-1]
        + (
            "algorithm_manifest",
            "keyframes",
            "speaker_structure",
            "bridge_context",
            "bridge_positions",
            "dds3_optional",
            "educational_candidates",
            "package",
        ),
        domain_plugin="bridge",
        description="Opt-in shadow profile for the full Video Analysis 3.1-test algorithm.",
    ),
    "bridge_lecture": ProcessingProfile(
        "bridge_lecture",
        CORE[:-1]
        + ("keyframes", "bridge_context", "topic_structure", "educational_candidates", "package"),
        domain_plugin="bridge",
        description="Bridge lecture/course recording.",
    ),
    "bridge_review": ProcessingProfile(
        "bridge_review",
        CORE[:-1]
        + ("keyframes", "bridge_context", "bridge_positions", "dds3_optional", "package"),
        domain_plugin="bridge",
        description="Bridge deal/tournament review recording.",
    ),
}


def resolve_profile(name: str) -> ProcessingProfile:
    key = str(name or "").strip().lower()
    try:
        return PROFILES[key]
    except KeyError as exc:
        raise ValueError(f"unknown universal-video profile: {name!r}") from exc


__all__ = ["ProcessingProfile", "PROFILES", "resolve_profile"]
