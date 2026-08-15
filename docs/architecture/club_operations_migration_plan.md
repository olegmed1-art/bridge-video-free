# Club Operations migration plan

This file reserves the reviewed implementation sequence for Club Operations. The production schema remains unchanged until migration 0020 is tested through the existing migration pipeline.

1. 0020 Club Operations core: club membership, contacts/preferences, services/prices, packages/entitlements, club events/bookings, financial ledger, communications, admin tasks and document references.
2. Runtime permissions test: app may write interactive club state but cannot DELETE/DDL; reader remains read-only; worker has only explicitly required background write capability.
3. 0021+ AuthIdentity and object-level authorization after the core semantics are stable.
4. Member API and UI are not opened before authorization and pilot identity import are verified.
