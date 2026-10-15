\set ON_ERROR_STOP on
-- Run only in a disposable video_rehearsal database after 0061 and canonical
-- synthetic queue setup. Supply -v job_id=... -v valid_output_json='...'.
-- The owner role must be able to call the private terminal gate.
\if :{?job_id}
\else
\echo job_id is required
\quit 2
\endif
\if :{?valid_output_json}
\else
\echo valid_output_json is required
\quit 2
\endif

BEGIN;
DO $scope$
BEGIN
    IF current_database() <> 'video_rehearsal'
       OR inet_server_addr() IS DISTINCT FROM '127.0.0.1'::inet THEN
        RAISE EXCEPTION 'SYNTHETIC_LOOPBACK_DATABASE_REQUIRED';
    END IF;
END $scope$;

CREATE TEMP TABLE one_job_terminal_fixture ON COMMIT DROP AS
SELECT :'job_id'::uuid AS job_id, :'valid_output_json'::jsonb AS output;

DO $negative$
DECLARE
    v_job_id uuid;
    v_valid jsonb;
    v_bad jsonb;
    v_manifest jsonb;
    v_receipt jsonb;
    v_manifest_sha text;
    v_evidence_sha text;
    v_rejected boolean;
    v_case text;
BEGIN
    SELECT job_id,output INTO STRICT v_job_id,v_valid FROM one_job_terminal_fixture;
    -- A bad base fixture must fail the test before any negative case runs.
    PERFORM video_queue.assert_one_job_terminal_evidence_v1(v_job_id,v_valid);
    FOREACH v_case IN ARRAY ARRAY[
        'no_manifest','no_drive_ids','no_artifact_hashes',
        'no_terminal_receipt','revision_only_manifest_with_valid_digests'
    ] LOOP
        IF v_case='no_manifest' THEN
            v_bad := v_valid - 'artifact_manifest';
        ELSIF v_case='no_drive_ids' THEN
            v_bad := v_valid - 'master_pdf_drive_id' - 'ai_done_drive_id';
        ELSIF v_case='no_artifact_hashes' THEN
            v_bad := v_valid - 'master_pdf_sha256' - 'ai_done_sha256';
        ELSIF v_case='no_terminal_receipt' THEN
            v_bad := v_valid - 'terminal_receipt';
        ELSE
            -- Rehash a fake manifest that has 0058 revision fields but no
            -- Drive IDs, locators, MIME types, parent IDs, or content hashes.
            v_manifest := jsonb_set(v_valid->'artifact_manifest','{artifacts}',
                jsonb_build_array(
                    jsonb_build_object('modified_time','2026-10-15T00:00:00Z',
                                       'version','1','size_bytes',1),
                    jsonb_build_object('modified_time','2026-10-15T00:00:00Z',
                                       'version','1','size_bytes',1)));
            v_manifest_sha := encode(public.digest(convert_to(
                video_queue.canonical_json_text(v_manifest),'UTF8'),'sha256'),'hex');
            v_receipt := jsonb_set(v_valid->'terminal_receipt',
                '{artifact_manifest_sha256}',to_jsonb(v_manifest_sha));
            v_evidence_sha := encode(public.digest(convert_to(
                video_queue.canonical_json_text(v_receipt-'evidence_sha256'),
                'UTF8'),'sha256'),'hex');
            v_receipt := jsonb_set(v_receipt,'{evidence_sha256}',
                                   to_jsonb(v_evidence_sha));
            v_bad := v_valid || jsonb_build_object(
                'artifact_manifest',v_manifest,
                'artifact_manifest_sha256',v_manifest_sha,
                'terminal_receipt',v_receipt,
                'terminal_evidence_sha256',v_evidence_sha);
        END IF;
        v_rejected := false;
        BEGIN
            PERFORM video_queue.assert_one_job_terminal_evidence_v1(v_job_id,v_bad);
        EXCEPTION WHEN OTHERS THEN
            v_rejected := true;
        END;
        IF NOT v_rejected THEN
            RAISE EXCEPTION 'ONE_JOB_TERMINAL_NEGATIVE_ACCEPTED: %',v_case;
        END IF;
    END LOOP;
END $negative$;
ROLLBACK;
