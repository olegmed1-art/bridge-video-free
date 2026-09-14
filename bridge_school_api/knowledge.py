from __future__ import annotations

from enum import StrEnum

from fastapi import APIRouter, HTTPException, Query

from .db import connect
from .l1_canonical_registry import (
    ACTIVE_DOMAIN_RULE_IDS,
    RULE_ID_FINGERPRINT,
    SNAPSHOT_DATE,
    SNAPSHOT_SOURCE,
    SYSTEM_VERSION,
)
from .l1_canonical_runtime_v4 import ENGINE_VERSION, evaluate

router = APIRouter(prefix="/v1/knowledge", tags=["knowledge-read"])

CONTRACT_VERSION = "CANON_KB_READ_V1"


class AuthorityLane(StrEnum):
    SOURCE = "SOURCE"
    WORLD_EXTERNAL = "WORLD_EXTERNAL"
    SCHOOL_CANON_CANDIDATE = "SCHOOL_CANON_CANDIDATE"
    ACTIVE_SCHOOL_CANON = "ACTIVE_SCHOOL_CANON"


_SOURCE_FACTS_SQL = """
    SELECT
        f.fact_id AS item_id,
        f.stable_key,
        f.fact_type AS item_type,
        f.original_statement,
        f.normalized_statement,
        f.provenance_class,
        f.review_status,
        COALESCE(f.system_dependency, 'SYSTEM_NEUTRAL') AS system_dependency,
        f.source_locator,
        src.source_id,
        src.source_type,
        src.title AS source_title,
        src.canonical_locator,
        COALESCE(
            jsonb_agg(
                jsonb_build_object(
                    'rule_id', r.rule_id,
                    'stable_key', r.stable_key,
                    'system_version', r.system_version,
                    'status', r.status
                ) ORDER BY r.stable_key
            ) FILTER (WHERE r.rule_id IS NOT NULL),
            '[]'::jsonb
        ) AS related_rules,
        COALESCE(
            array_agg(DISTINCT r.system_version)
                FILTER (WHERE r.system_version IS NOT NULL),
            ARRAY[]::text[]
        ) AS related_system_profiles
    FROM ai.knowledge_fact f
    JOIN public.school school ON school.school_id = f.school_id
    LEFT JOIN public.source src ON src.source_id = f.source_id
    LEFT JOIN ai.rule_fact rf ON rf.fact_id = f.fact_id
    LEFT JOIN ai.system_rule r ON r.rule_id = rf.rule_id
    WHERE school.stable_name = %s
      AND (
          COALESCE(f.system_dependency, 'SYSTEM_NEUTRAL') = %s
          OR EXISTS (
              SELECT 1
              FROM ai.rule_fact profile_link
              JOIN ai.system_rule profile_rule
                ON profile_rule.rule_id = profile_link.rule_id
              WHERE profile_link.fact_id = f.fact_id
                AND profile_rule.system_version = %s
          )
      )
      AND f.review_status = 'APPROVED_SOURCE'
      AND (%s IS NULL OR f.stable_key = %s)
    GROUP BY f.fact_id, src.source_id
    ORDER BY f.stable_key
    LIMIT %s OFFSET %s
"""

_KNOWLEDGE_VERSIONS_SQL = """
    SELECT
        ki.knowledge_item_id,
        kv.knowledge_version_id AS item_id,
        ki.stable_key,
        ki.knowledge_type AS item_type,
        ki.title,
        kv.version_no,
        kv.content,
        kv.authority_class,
        kv.review_status,
        kv.status AS version_status,
        COALESCE(kv.bidding_system_key, 'SYSTEM_NEUTRAL') AS system_profile,
        kv.agreement_scope,
        kv.level_scope,
        kv.effective_from,
        kv.effective_to,
        kv.method_version,
        kv.provenance,
        {activation_fields}
        COALESCE(
            (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'source_id', src.source_id,
                        'source_type', src.source_type,
                        'title', src.title,
                        'canonical_locator', src.canonical_locator,
                        'relation_type', kvs.relation_type,
                        'source_locator', kvs.source_locator
                    ) ORDER BY src.source_id
                )
                FROM public.knowledge_version_source kvs
                JOIN public.source src ON src.source_id = kvs.source_id
                WHERE kvs.knowledge_version_id = kv.knowledge_version_id
            ),
            '[]'::jsonb
        ) AS sources
    FROM public.knowledge_item ki
    JOIN public.knowledge_version kv ON kv.knowledge_item_id = ki.knowledge_item_id
    JOIN public.school school ON school.school_id = ki.school_id
    {activation_join}
    WHERE school.stable_name = %s
      AND kv.authority_class = %s
      AND COALESCE(kv.bidding_system_key, 'SYSTEM_NEUTRAL') = %s
      AND (%s IS NULL OR ki.stable_key = %s)
      {lifecycle_filter}
    ORDER BY ki.stable_key, kv.version_no DESC
    LIMIT %s OFFSET %s
"""

_ACTIVE_CONFLICT_SQL = """
    SELECT
        left_item.stable_key AS left_key,
        right_item.stable_key AS right_key
    FROM public.knowledge_relation relation
    JOIN public.knowledge_version left_version
      ON left_version.knowledge_version_id = relation.from_version_id
    JOIN public.knowledge_item left_item
      ON left_item.knowledge_item_id = left_version.knowledge_item_id
    JOIN public.canon_activation left_activation
      ON left_activation.knowledge_version_id = left_version.knowledge_version_id
    JOIN public.knowledge_version right_version
      ON right_version.knowledge_version_id = relation.to_version_id
    JOIN public.knowledge_item right_item
      ON right_item.knowledge_item_id = right_version.knowledge_item_id
    JOIN public.canon_activation right_activation
      ON right_activation.knowledge_version_id = right_version.knowledge_version_id
    JOIN public.school school ON school.school_id = left_item.school_id
    WHERE school.stable_name = %s
      AND right_item.school_id = left_item.school_id
      AND relation.relation_type = 'contradicts'
      AND left_version.authority_class = 'school_canon'
      AND right_version.authority_class = 'school_canon'
      AND COALESCE(left_version.bidding_system_key, 'SYSTEM_NEUTRAL') = %s
      AND COALESCE(right_version.bidding_system_key, 'SYSTEM_NEUTRAL') = %s
      AND left_activation.scope_key = %s
      AND right_activation.scope_key = %s
      AND left_activation.status = 'active'
      AND right_activation.status = 'active'
      AND left_activation.valid_from <= now()
      AND right_activation.valid_from <= now()
      AND (left_activation.valid_to IS NULL OR now() < left_activation.valid_to)
      AND (right_activation.valid_to IS NULL OR now() < right_activation.valid_to)
    LIMIT 1
"""

_ACTIVE_SNAPSHOT_SQL = (
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
)

_SYNC_STATE_SQL = """
    SELECT
        sync_key,
        source_locator,
        source_revision,
        last_seen_at,
        last_success_at,
        status
    FROM ai.sync_state
    WHERE sync_key = 'google-sheet:catalog-ai'
"""


def _profile(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(status_code=422, detail="system_profile is required")
    return normalized


def _version_query(lane: AuthorityLane) -> str:
    if lane is AuthorityLane.ACTIVE_SCHOOL_CANON:
        return _KNOWLEDGE_VERSIONS_SQL.format(
            activation_fields=(
                "activation.canon_activation_id, activation.scope_key, "
                "activation.valid_from AS activation_valid_from, "
                "activation.valid_to AS activation_valid_to, "
                "activation.status AS activation_status,"
            ),
            activation_join=(
                "JOIN public.canon_activation activation "
                "ON activation.knowledge_version_id = kv.knowledge_version_id"
            ),
            lifecycle_filter="""
              AND activation.scope_key = %s
              AND activation.status = 'active'
              AND activation.valid_from <= now()
              AND (activation.valid_to IS NULL OR now() < activation.valid_to)
            """,
        )
    return _KNOWLEDGE_VERSIONS_SQL.format(
        activation_fields=(
            "NULL::uuid AS canon_activation_id, NULL::text AS scope_key, "
            "NULL::timestamptz AS activation_valid_from, "
            "NULL::timestamptz AS activation_valid_to, "
            "NULL::text AS activation_status,"
        ),
        activation_join="",
        lifecycle_filter=(
            "AND kv.review_status = 'reviewed' "
            "AND kv.status IN ('candidate', 'active')"
            if lane is AuthorityLane.WORLD_EXTERNAL
            else """
              AND kv.status = 'candidate'
              AND NOT EXISTS (
                  SELECT 1
                  FROM public.canon_activation activation
                  WHERE activation.knowledge_version_id = kv.knowledge_version_id
                    AND activation.status = 'active'
                    AND activation.valid_from <= now()
                    AND (activation.valid_to IS NULL OR now() < activation.valid_to)
              )
            """
        ),
    )


def _retrieval_status(lane: AuthorityLane, count: int) -> str:
    if lane is AuthorityLane.ACTIVE_SCHOOL_CANON:
        return "CANON_MATCH" if count else "CANON_GAP"
    if lane is AuthorityLane.SCHOOL_CANON_CANDIDATE:
        return "CANDIDATES_FOUND" if count else "CANDIDATE_GAP"
    if lane is AuthorityLane.WORLD_EXTERNAL:
        return "WORLD_MATCH" if count else "WORLD_GAP"
    return "SOURCE_MATCH" if count else "SOURCE_GAP"


@router.get("/query")
def query_knowledge(
    lane: AuthorityLane = Query(),
    system_profile: str = Query(min_length=1, max_length=120),
    stable_key: str | None = Query(default=None, min_length=1, max_length=200),
    scope_key: str = Query(default="default", min_length=1, max_length=120),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Read exactly one authority lane; never fall back or promote implicitly."""
    lane = AuthorityLane(lane)
    system_profile = _profile(system_profile)
    sync_state = None

    with connect() as conn, conn.cursor() as cur:
        if lane is AuthorityLane.SOURCE:
            cur.execute(
                _SOURCE_FACTS_SQL,
                (
                    "Школа спортивного бриджа",
                    system_profile,
                    system_profile,
                    stable_key,
                    stable_key,
                    limit,
                    offset,
                ),
            )
        else:
            authority_class = (
                "external"
                if lane is AuthorityLane.WORLD_EXTERNAL
                else "school_canon"
            )
            if lane is AuthorityLane.ACTIVE_SCHOOL_CANON:
                # The conflict guard and retrieval must observe one MVCC
                # snapshot. READ COMMITTED would allow a concurrent activation
                # or contradiction to appear between the two SELECTs.
                cur.execute(_ACTIVE_SNAPSHOT_SQL, ())
                cur.execute(
                    _ACTIVE_CONFLICT_SQL,
                    (
                        "Школа спортивного бриджа",
                        system_profile,
                        system_profile,
                        scope_key,
                        scope_key,
                    ),
                )
                conflict = cur.fetchone()
                if conflict:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "CANON_CONFLICT",
                            "left_key": conflict["left_key"],
                            "right_key": conflict["right_key"],
                        },
                    )
            params: tuple[object, ...] = (
                "Школа спортивного бриджа",
                authority_class,
                system_profile,
                stable_key,
                stable_key,
            )
            if lane is AuthorityLane.ACTIVE_SCHOOL_CANON:
                params += (scope_key,)
            params += (limit, offset)
            cur.execute(_version_query(lane), params)

        items = cur.fetchall()
        if lane is AuthorityLane.SOURCE:
            cur.execute(_SYNC_STATE_SQL, ())
            sync_state = cur.fetchone()

    return {
        "contract_version": CONTRACT_VERSION,
        "authority_lane": lane.value,
        "system_profile": system_profile,
        "scope_key": scope_key if lane is AuthorityLane.ACTIVE_SCHOOL_CANON else None,
        "retrieval_status": _retrieval_status(lane, len(items)),
        "fallback_performed": False,
        "candidate_activation_performed": False,
        "sync_state": sync_state,
        "count": len(items),
        "items": items,
    }


@router.get("/runtime/l1")
def l1_runtime_catalog(
    system_profile: str = Query(min_length=1, max_length=120),
    rule_id: str | None = Query(default=None, min_length=1, max_length=200),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Expose the pinned executable snapshot without asserting DB activation."""
    system_profile = _profile(system_profile)
    if system_profile != SYSTEM_VERSION:
        raise HTTPException(
            status_code=404,
            detail={"code": "RUNTIME_PROFILE_GAP", "system_profile": system_profile},
        )

    selected = [
        candidate
        for candidate in ACTIVE_DOMAIN_RULE_IDS
        if rule_id is None or candidate == rule_id
    ]
    selected = selected[offset : offset + limit]
    rules = []
    for candidate in selected:
        result = evaluate(candidate, {})
        executable = not (
            result.status == "BLOCK"
            and result.action == "KNOWN_RULE_NOT_EXECUTABLE"
        )
        rules.append({"stable_key": candidate, "executable": executable})

    return {
        "contract_version": CONTRACT_VERSION,
        "authority_lane": "SCHOOL_CANON",
        "lifecycle": "RUNTIME_SNAPSHOT",
        "formal_db_activation_asserted": False,
        "system_profile": SYSTEM_VERSION,
        "engine_version": ENGINE_VERSION,
        "snapshot_source": SNAPSHOT_SOURCE,
        "snapshot_date": SNAPSHOT_DATE,
        "rule_id_fingerprint": RULE_ID_FINGERPRINT,
        "retrieval_status": "RUNTIME_MATCH" if rules else "RUNTIME_GAP",
        "fallback_performed": False,
        "count": len(rules),
        "rules": rules,
    }
