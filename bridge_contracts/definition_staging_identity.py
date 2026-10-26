"""Exact legacy/v2 staging identities; no data normalization or authority."""
import re

def definition_staging_keys(logical_key,payload_hash):
    if type(logical_key) is not str or not logical_key.strip():
        raise ValueError('DEFINITION_LOGICAL_KEY_REQUIRED')
    if type(payload_hash) is not str or re.fullmatch(r'[0-9a-f]{64}',payload_hash) is None:
        raise ValueError('DEFINITION_PAYLOAD_HASH_REQUIRED')
    suffix=':sha256:'+payload_hash
    versioned=logical_key if logical_key.endswith(suffix) else logical_key+suffix
    # Keep exact previously valid legacy identities; never prefer one row when
    # both identities are present. The native selector rejects that ambiguity.
    return tuple(dict.fromkeys((logical_key,versioned)))

__all__=['definition_staging_keys']
