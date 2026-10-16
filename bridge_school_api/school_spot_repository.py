"""Read-only ownership lookup for an already verified school browser identity.

The caller must authenticate the browser session before constructing the input.
This module never accepts a request, browser header, or client-provided journal path.
It is deliberately not wired into the public route until that verifier exists.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping, Protocol

from .local_spot_session import LocalSpotSessionUnavailable, load_store_class
from .school_spot_lesson import LearnerLessonBinding, VerifiedSchoolSession


@dataclass(frozen=True)
class VerifiedProviderIdentity:
    """Provider subject and CSRF context produced by a trusted session verifier."""

    provider_key: str
    provider_subject: str
    csrf_token: str
    origin: str


@dataclass(frozen=True)
class TrustedJournalRecord:
    """Existing journal selected by server-side school records, never by the browser."""

    school_id: str
    student_id: str
    session_id: str
    journal_path: Path


class TrustedJournalSource(Protocol):
    def find_existing(self, identity: VerifiedProviderIdentity) -> TrustedJournalRecord | None: ...


class FileTrustedJournalSource:
    """Host-owned lookup, not an authenticator. No request can select a file.

    The caller supplies one public activity ID and an already trusted identity.
    Journal paths must remain beneath the private manifest directory.
    """

    def __init__(self, manifest_path: Path, exercise_id: str):
        self.path = Path(manifest_path)
        self.exercise_id = exercise_id

    def _owned_manifest(self, identity: VerifiedProviderIdentity):
        if not isinstance(identity, VerifiedProviderIdentity) or self.path.is_symlink():
            raise SchoolLessonOwnershipRejected('invalid journal source')
        raw = self.path.read_bytes()
        if len(raw) > 65536:
            raise SchoolLessonOwnershipRejected('journal manifest size')
        data = json.loads(raw)
        if (type(data) is not dict or data.get('schema') != 'school-file-journal-bindings-v1'
                or type(data.get('owner')) is not dict or type(data.get('assignments')) is not dict):
            raise SchoolLessonOwnershipRejected('journal manifest shape')
        owner = data['owner']
        if (owner.get('provider_key') != identity.provider_key or
                owner.get('provider_subject') != identity.provider_subject or
                not all(type(owner.get(k)) is str and 1 <= len(owner[k]) <= 120
                        for k in ('school_id', 'student_id'))):
            raise SchoolLessonOwnershipRejected('journal owner mismatch')
        if not set(data['assignments']) <= {'spot.a7c2', 'spot.f3b8'}:
            raise SchoolLessonOwnershipRejected('unknown assigned activity')
        return data

    def assigned_ids(self, identity: VerifiedProviderIdentity) -> tuple[str, ...]:
        return tuple(self._owned_manifest(identity)['assignments'])

    def find_existing(self, identity: VerifiedProviderIdentity) -> TrustedJournalRecord | None:
        data = self._owned_manifest(identity)
        record = data['assignments'].get(self.exercise_id)
        if record is None:
            return None
        if (type(record) is not dict or set(record) != {'exercise_id', 'session_id', 'journal'}
                or record['exercise_id'] != self.exercise_id
                or type(record['session_id']) is not str or not 1 <= len(record['session_id']) <= 120
                or type(record['journal']) is not str):
            raise SchoolLessonOwnershipRejected('invalid assigned journal')
        relative = Path(record['journal'])
        root = self.path.parent.resolve()
        journal = (root / relative).resolve()
        if (relative.is_absolute() or '..' in relative.parts or
                not journal.is_relative_to(root) or (root / relative).is_symlink()
                or not journal.is_file()):
            raise SchoolLessonOwnershipRejected('private journal unavailable')
        owner = data['owner']
        return TrustedJournalRecord(owner['school_id'], owner['student_id'],
                                    record['session_id'], journal)


class ReadCursor(Protocol):
    def fetchone(self) -> Mapping[str, object] | None: ...


class ReadConnection(Protocol):
    def execute(self, query: str, params: tuple[str, ...]) -> ReadCursor: ...


class SchoolLessonOwnershipRejected(Exception):
    """No active, same-school learner/lesson/journal binding exists."""


OWNERSHIP_QUERY = """
SELECT st.school_id, st.student_id, se.session_id
  FROM auth_identity ai
  JOIN person p ON p.person_id = ai.person_id
  JOIN student st ON st.person_id = p.person_id
  JOIN school sc ON sc.school_id = st.school_id
  JOIN session se ON se.session_id = %s AND se.school_id = st.school_id
  JOIN session_participation sp ON sp.session_id = se.session_id
                               AND sp.student_id = st.student_id
 WHERE ai.provider_key = %s
   AND ai.provider_subject = %s
   AND ai.status = 'active'
   AND ai.valid_from <= now()
   AND (ai.valid_to IS NULL OR now() < ai.valid_to)
   AND p.status = 'active'
   AND st.current_status = 'active'
   AND sc.status = 'active'
   AND st.school_id = %s
   AND st.student_id = %s
   AND sp.participant_role = 'student'
   AND (sp.person_id IS NULL OR sp.person_id = p.person_id)
   AND EXISTS (
       SELECT 1 FROM person_role_assignment ra
        WHERE ra.school_id = st.school_id
          AND ra.person_id = p.person_id
          AND ra.role_key = 'member'
          AND ra.scope_type = 'school'
          AND ra.scope_id IS NULL
          AND ra.status = 'active'
          AND ra.valid_from <= now()
          AND (ra.valid_to IS NULL OR now() < ra.valid_to)
   )
 LIMIT 1
"""


class SchoolSpotReadRepository:
    """Resolve identity to an existing lesson with a read-only SQL connection."""

    def __init__(self, connection: ReadConnection, journals: TrustedJournalSource):
        self._connection = connection
        self._journals = journals

    def resolve(self, identity: VerifiedProviderIdentity) -> tuple[VerifiedSchoolSession, LearnerLessonBinding]:
        if not isinstance(identity, VerifiedProviderIdentity) or not all(
            isinstance(value, str) and value.strip()
            for value in (identity.provider_key, identity.provider_subject, identity.csrf_token, identity.origin)
        ):
            raise SchoolLessonOwnershipRejected("verified provider identity required")

        record = self._journals.find_existing(identity)
        if (
            not isinstance(record, TrustedJournalRecord)
            or not all(isinstance(value, str) and value.strip() for value in
                       (record.school_id, record.student_id, record.session_id))
            or not isinstance(record.journal_path, Path)
            or not record.journal_path.is_file()
        ):
            raise SchoolLessonOwnershipRejected("existing server-side journal required")

        row = self._connection.execute(
            OWNERSHIP_QUERY,
            (record.session_id, identity.provider_key, identity.provider_subject,
             record.school_id, record.student_id),
        ).fetchone()
        if not row or (
            str(row.get("school_id")) != record.school_id
            or str(row.get("student_id")) != record.student_id
            or str(row.get("session_id")) != record.session_id
        ):
            raise SchoolLessonOwnershipRejected("learner does not own this school lesson")

        try:
            load_store_class()(record.journal_path, record.session_id).resume()
        except (LocalSpotSessionUnavailable, ValueError, OSError) as exc:
            raise SchoolLessonOwnershipRejected("journal does not match lesson") from exc

        return (
            VerifiedSchoolSession(record.school_id, record.student_id, identity.csrf_token, identity.origin),
            LearnerLessonBinding(record.school_id, record.student_id, record.session_id, record.journal_path),
        )
