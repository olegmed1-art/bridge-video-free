# Controlled onboarding policy

Evidence Gate round 2 for `0102_identity_controlled_onboarding` after aligning the historical runtime-principal contract with the new onboarding invariant.

Policy under test: a newly materialized school Person atomically receives active Student status, active standard ClubMembership, and active personal-cabinet/member authorization. Student status, club membership, and portal authorization can later be restricted or restored independently without deleting the Person or history. `AuthIdentity` is not invented by onboarding; it remains a separately verified provider credential binding.

The controlled Identity Import apply path must be fail-closed, idempotent, auditable, unavailable to ordinary runtime roles, and must distinguish creation of a new Person from linking an already-existing Person.
