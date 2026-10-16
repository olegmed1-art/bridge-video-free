\set ON_ERROR_STOP on
BEGIN;

-- Preparation only: reject SQL NULL before the existing receipt replay path.
-- CREATE OR REPLACE retains the function identity, owner and existing ACL.
DO $migration$
DECLARE
    proc regprocedure := 'autopilot.accept_role_dispatch_codex_ack(text,text,boolean,text,integer,text,bigint,text,text,bigint,text,bigint,jsonb)'::regprocedure;
    original text;
    anchor constant text := $anchor$       OR p_payload_fingerprint !~ '^[0-9a-f]{64}$'$anchor$;
    replacement constant text := $replacement$       OR p_payload_fingerprint IS NULL -- ACK_FINGERPRINT_NOT_NULL_V1
       OR p_payload_fingerprint !~ '^[0-9a-f]{64}$'$replacement$;
BEGIN
    IF NOT EXISTS (SELECT FROM public.schema_migration
                   WHERE migration_key='0362_autopilot_target_pr_codex_context') THEN
        RAISE EXCEPTION 'AUTOPILOT_ACK_FINGERPRINT_REQUIRES_0362';
    END IF;
    original := pg_get_functiondef(proc);
    IF strpos(original,'ACK_FINGERPRINT_NOT_NULL_V1')>0
       OR strpos(original,'TARGET_PR_CODEX_CONTEXT_V1')=0
       OR (length(original)-length(replace(original,anchor,'')))/length(anchor)<>1 THEN
        RAISE EXCEPTION 'AUTOPILOT_ACK_FINGERPRINT_SOURCE_DRIFT';
    END IF;
    EXECUTE replace(original,anchor,replacement);
END $migration$;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0401_autopilot_codex_ack_fingerprint_not_null');
COMMIT;
