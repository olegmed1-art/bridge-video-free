# Controlled onboarding policy

Evidence Gate round 3 for migrations `0102_identity_controlled_onboarding` and `0103_auth_portal_actor_context_signature_fix` on the corrected current main tree.

Required policy:
- creation of a new school Person atomically creates an active Student, an active standard ClubMembership, and active personal-cabinet/member authorization;
- Student status, club membership, and portal authorization can later be restricted or restored independently without deleting Person or history;
- `AuthIdentity` remains a separately verified provider credential binding and is not fabricated during onboarding;
- controlled Identity Import apply is fail-closed, idempotent, append-audited, and inaccessible to ordinary runtime roles;
- personal-cabinet actor context requires active `member` authorization and retains the signed transaction/backend-bound actor-context protocol.
