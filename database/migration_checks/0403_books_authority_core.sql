\set ON_ERROR_STOP on
\if :{?migration_checksum}
\else
\echo BOOKS_AUTHORITY_CHECKSUM_REQUIRED
\quit 3
\endif
\if :{?books_expected_database}
\else
\echo BOOKS_AUTHORITY_EXPECTED_TARGET_REQUIRED
\quit 3
\endif
\if :{?books_expected_owner}
\else
\echo BOOKS_AUTHORITY_EXPECTED_OWNER_REQUIRED
\quit 3
\endif
BEGIN READ ONLY;
SET LOCAL statement_timeout='5s';
SET LOCAL lock_timeout='1s';
SET LOCAL search_path=pg_catalog;
SELECT set_config('books.authority_expected_checksum', :'migration_checksum',true) AS books_checksum,
       set_config('books.authority_expected_database', :'books_expected_database',true) AS books_database,
       set_config('books.authority_expected_owner', :'books_expected_owner',true) AS books_owner \gset
\ir ../migrations/0403_books_authority_core/002_assert.sql
COMMIT;
\echo BOOKS_AUTHORITY_REPLAY_VERIFIED_NO_REGRANT
