"""Fail-closed multi-lesson longitudinal pilot aggregator."""
from __future__ import annotations

from typing import Any, Mapping

from .contract import EpisodeContractError, build_skill_trajectory, validate_episode

LONGITUDINAL_PILOT_SCHEMA = "evolutionary-course-longitudinal-pilot-v1"


def run_multi_lesson_pilot(adapter_reports: list[Mapping[str, Any]]) -> dict[str, Any]:
    blockers: list[str] = []
    if not isinstance(adapter_reports, list):
        adapter_reports = []
        blockers.append("ADAPTER_REPORTS_NOT_LIST")
    jobs: set[str] = set()
    videos: set[str] = set()
    dates: set[str] = set()
    episodes: list[dict[str, Any]] = []
    for report in adapter_reports:
        if not isinstance(report, Mapping):
            blockers.append("ADAPTER_REPORT_NOT_OBJECT")
            continue
        authority = report.get("authority")
        if not isinstance(authority, Mapping) or any(
            authority.get(field) is not False
            for field in (
                "canonical_promotion_allowed", "curriculum_activation_allowed",
                "student_profile_write_allowed", "publication_allowed",
            )
        ):
            blockers.append("AUTHORITY_BOUNDARY_MISMATCH")
        if report.get("skill_binding_mode") != "REVIEWED_CATALOG":
            blockers.append("REVIEWED_CATALOG_BINDING_REQUIRED")
        job_id = str(report.get("source_job_id") or "").strip()
        lesson_date = str(report.get("lesson_date") or "").strip()
        if not job_id:
            blockers.append("SOURCE_JOB_ID_MISSING")
        elif job_id in jobs:
            blockers.append("DUPLICATE_SOURCE_JOB")
        else:
            jobs.add(job_id)
        if not lesson_date:
            blockers.append("CONFIRMED_LESSON_DATE_MISSING")
        elif lesson_date in dates:
            blockers.append("DUPLICATE_LESSON_DATE")
        else:
            dates.add(lesson_date)
        report_episodes = report.get("episodes")
        if not isinstance(report_episodes, list) or not report_episodes:
            blockers.append("ACCEPTED_EPISODE_MISSING")
            continue
        report_videos: set[str] = set()
        for episode in report_episodes:
            try:
                normalized = validate_episode(episode)
            except EpisodeContractError:
                blockers.append("INVALID_EPISODE")
                continue
            if lesson_date and not normalized["occurred_at"].startswith(lesson_date):
                blockers.append("EPISODE_DATE_MISMATCH")
            report_videos.add(normalized["source"]["video_file_id"])
            episodes.append(normalized)
        if len(report_videos) != 1:
            blockers.append("EXACT_SINGLE_VIDEO_PER_LESSON_REQUIRED")
        else:
            video_id = next(iter(report_videos))
            if video_id in videos:
                blockers.append("DUPLICATE_SOURCE_VIDEO")
            videos.add(video_id)
    if len(jobs) < 3:
        blockers.append("MINIMUM_THREE_DISTINCT_LESSONS_REQUIRED")
    base = {
        "schema": LONGITUDINAL_PILOT_SCHEMA,
        "lesson_count": len(jobs),
        "episode_count": len(episodes),
        "source_job_ids": sorted(jobs),
        "source_video_file_ids": sorted(videos),
        "lesson_dates": sorted(dates),
        "media_reprocessed": False,
        "authority": {
            "authority_class": "CANDIDATE_RESEARCH",
            "canonical_promotion_allowed": False,
            "curriculum_activation_allowed": False,
            "student_profile_write_allowed": False,
            "publication_allowed": False,
        },
    }
    blockers = list(dict.fromkeys(blockers))
    if blockers:
        return {**base, "status": "BLOCKED", "blockers": blockers, "trajectory": None}
    try:
        trajectory = build_skill_trajectory(episodes)
    except EpisodeContractError as exc:
        return {
            **base, "status": "BLOCKED",
            "blockers": [f"TRAJECTORY_REJECTED: {exc}"], "trajectory": None,
        }
    return {
        **base,
        "status": "READY_FOR_PRIVATE_LONGITUDINAL_REVIEW",
        "blockers": [],
        "trajectory": trajectory,
    }


__all__ = ["LONGITUDINAL_PILOT_SCHEMA", "run_multi_lesson_pilot"]
