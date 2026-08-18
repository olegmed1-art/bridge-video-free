# Controlled onboarding policy

CI evidence gate for migration `0102_identity_controlled_onboarding` and regression `094_identity_controlled_onboarding`.

Policy under test: a newly materialized school Person is atomically created as an active Student, an active standard ClubMembership, and an active personal-cabinet/member user. Those three access dimensions can later be restricted or restored independently. External AuthIdentity is still bound only after provider verification; onboarding never fabricates a credential.
