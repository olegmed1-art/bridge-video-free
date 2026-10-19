"""Server-only explicit configuration. No runtime role or credential is created.

Keep disabled until the existing native PHP session/CSRF contract is known.
The resolver must return AnonymousPracticeBinding using a server-owned journal;
never derive owner_ref, origin or journal_path from browser identity headers.
"""
from bridge_school_api.anonymous_practice_adapter import AnonymousPracticeConfig

CONFIG = AnonymousPracticeConfig(
    enabled=False,
    resolve_session=None,       # Established host session/CSRF resolver, not admin.
    allowed_origins=frozenset(),  # Explicit canonical origin from the host contract.
)
