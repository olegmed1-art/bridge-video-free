# Controlled-onboarding app runtime smoke request

This marker requests the production Neon app runtime smoke after aligning the smoke contract with migration 0102.

Expected result:

- BRIDGE_APP_DATABASE_URL is configured for bridge_school_app_principal.
- app principal inherits bridge_school_app.
- app can read the school registry.
- app cannot INSERT or DELETE public.person.
- app can UPDATE existing public.person rows.
- app cannot write worker/source tables or operational health policy.
