\set ON_ERROR_STOP on
BEGIN;

-- Forward-only repair: 0329 is checksum-protected historical migration.
-- In PostgreSQL's C locale, [:alnum:] does not classify every Unicode letter.
-- Numeric range suffixes such as 10-12я must not borrow the safe-range
-- exemption, and "рука" inside "Порука" must not be read as a hand label.
-- CREATE OR REPLACE retains the function identity, owner, and existing ACL.
DO $migration$
DECLARE
    proc regprocedure := 'bidding.contains_forbidden_hidden_value(jsonb)'::regprocedure;
    original text;
    old_russian constant text := '(?:^|[^[:alnum:]_])(?:рука|карты)';
    new_russian constant text := '(?:^|[^[:alnum:]_А-Яа-яЁё])(?:рука|карты)';
    old_boundary constant text := '($|[^[:alnum:]_])';
    new_boundary constant text := '($|[[:space:],./;:!?()])';
BEGIN
    IF NOT EXISTS (SELECT FROM public.schema_migration
                   WHERE migration_key='0329_workflow_video_canon_ai_promotion') THEN
        RAISE EXCEPTION 'VIDEO_CANON_FIREWALL_REQUIRES_0329';
    END IF;
    original := pg_get_functiondef(proc);
    IF (length(original)-length(replace(original,old_russian,'')))/length(old_russian)<>4
       OR (length(original)-length(replace(original,old_boundary,'')))/length(old_boundary)<>3 THEN
        RAISE EXCEPTION 'VIDEO_CANON_FIREWALL_SOURCE_DRIFT';
    END IF;
    EXECUTE replace(replace(original,old_russian,new_russian),old_boundary,new_boundary);
END $migration$;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0402_video_canon_firewall_unicode_boundary');
COMMIT;
