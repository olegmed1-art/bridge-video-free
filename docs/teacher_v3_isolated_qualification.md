# Teacher v3 isolated qualification

Test branch only. No production route, live reader, source registration or activation is installed. Do not merge or deploy this branch without the separate production-admission review. L1's fixed registry is unchanged. The existing BML owner accepted the exact target pins for qualified local integration, as recorded in teacher_v3_shared_pins.json. This is not an assertion that new bytes have been installed in BML or that READY/FULL_CALL is admitted.

The actual entry point is teacher_v04_integration.answer_entry_order with explicit test_only=True. Its bounded synthetic Axx/QJxx case invokes the two real L1 procedural evaluators, returns the representative low S2 with trace and SRC-0022/SKILL-0003/0004 provenance, and rereads eligibility before returning. Missing data, invalid identity or revocation withhold the answer. It does not prove an optimal spot, a whole deal, or production authority.

BMLReadyPackageReader remains a rejecting adapter. All ten first-response FULL_CALL answers remain ABSTAIN. Literal source registration or a hash does not create runtime readiness. There is no WORLD fallback, L1 registry expansion, database connection, replay or card execution.

This version fixes explicit NT constraint composition and missing/invalid observations in L1. It checks typed integers/flags, finite denomination enums, missing versus measured zero, valid T/10 cards, and profile isolation before context parsing. Exact NT actions are filtered before ranking; unrelated suit actions and the prior conflict behavior remain. Independent local I2 closed I2-02/03 within this explicit-observation contract; it did not certify every bridge meaning or grant production admission.

The portable qualifier verifies all 23 source hashes and executes 273 genuine tests: 76 original Teacher, 151 context-completed L1, 9 unchanged BML, 4 identity, 33 safety. Independent AST counts, collected tests, actual body calls and all setup/call/teardown results must agree. Three historical positive fixtures now supply previously omitted observations; the exact old incomplete inputs remain negative tests. There are no skip/xfail/deselection exceptions. Failed historical runs and v2 byte history are retained in the integrator's versioned delivery, not rewritten as successes.

CI is one push-only job on the exact named test branch, capped at 20 minutes. It sparse-checks out only the pinned package/test infrastructure, installs pytest 8.4.2, and does not import application initializers. The observational audit guard denies attempted network/process/file-write activity during tests, but is not an OS sandbox. No secrets, environments, production credentials or provider jobs are requested by this workflow. Checkout does not persist its credentials.

Local reproduction from the repository root requires an existing pytest 8.4.2 site directory:

    python -I -S -B -X utf8 tools/teacher_v3_isolated_qualification.py --pytest-site /absolute/existing/site-packages --output qualification-results/NEW_RUN.json

Use a new report filename. Test-only acceptance cannot be changed by caller flags. The server-owned catalog reader, accepted query mapping, actual knowledge/runtime IDs and executable method/scope/payload/source bindings remain separate missing contracts. FULL_CALL may not be enabled by accepting these shared file pins.
