-- Metadata-only postcondition. Included inside the DDL transaction and by replay verification.
DO $books_assert$
DECLARE
    owner_id oid := (SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user);
    public_id oid := pg_catalog.to_regnamespace('public');
    tabnames constant text[] := ARRAY['books_authority_object','books_authority_state','books_authority_state_audit','books_authority_attempt','books_authority_phase']::text[];
    expected_cols constant jsonb := $json_cols${"books_authority_object": [["object_id", "text", true, ""], ["kind", "text", true, ""], ["body", "jsonb", true, ""]], "books_authority_state": [["object_id", "text", true, ""], ["revision", "bigint", true, ""], ["status", "text", true, ""], ["audit_actor", "text", true, ""], ["changed_at", "timestamp with time zone", true, "clock_timestamp()"]], "books_authority_state_audit": [["object_id", "text", true, ""], ["revision", "bigint", true, ""], ["status", "text", true, ""], ["audit_actor", "text", true, ""], ["changed_at", "timestamp with time zone", true, ""]], "books_authority_attempt": [["attempt_id", "uuid", true, ""], ["grant_id", "text", true, ""], ["phase", "text", true, ""], ["principal", "text", true, ""], ["target", "text", true, ""], ["school_id", "uuid", true, ""], ["status", "text", true, ""], ["created_at", "timestamp with time zone", true, "clock_timestamp()"]], "books_authority_phase": [["grant_id", "text", true, ""], ["phase", "text", true, ""], ["receipt", "jsonb", true, ""]]}$json_cols$::jsonb;
    expected_checks constant jsonb := $json_checks${"books_authority_object_kind_check": ["books_authority_object", "kind = ANY (ARRAY['policy'::text, 'principal'::text, 'grant'::text, 'catalog'::text, 'verifier'::text, 'validation'::text, 'catalog_dependency'::text])"], "books_authority_object_body_check": ["books_authority_object", "jsonb_typeof(body) = 'object'::text"], "books_authority_state_revision_check": ["books_authority_state", "revision >= 0"], "books_authority_state_status_check": ["books_authority_state", "status = ANY (ARRAY['active'::text, 'revoked'::text])"], "books_authority_attempt_status_check": ["books_authority_attempt", "status = ANY (ARRAY['unresolved'::text, 'success'::text])"], "books_authority_phase_phase_check": ["books_authority_phase", "phase = ANY (ARRAY['IMPORT_STAGED'::text, 'DELIVER_RECORDED_OUTBOX'::text, 'ACTIVATE_REVIEWED_WORLD'::text, 'DELIVER_PUBLISHED_OUTBOX'::text])"], "books_authority_phase_receipt_check": ["books_authority_phase", "jsonb_typeof(receipt) = 'object'::text"]}$json_checks$::jsonb;
    expected_pks constant jsonb := $json_pks${"books_authority_object": ["object_id"], "books_authority_state": ["object_id"], "books_authority_state_audit": ["object_id", "revision"], "books_authority_attempt": ["attempt_id"], "books_authority_phase": ["grant_id", "phase"]}$json_pks$::jsonb;
    expected_bodies constant jsonb := $json_bodies${"books_authority_immutable": "BEGIN\n    RAISE EXCEPTION 'BOOK_AUTHORITY_IMMUTABLE';\nEND;", "books_authority_state_transition": "BEGIN\n    IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'BOOK_AUTHORITY_STATE_DELETE'; END IF;\n    IF TG_OP = 'UPDATE' AND\n       (NEW.object_id <> OLD.object_id OR OLD.status <> 'active'\n        OR NEW.status <> 'revoked' OR NEW.revision <> OLD.revision + 1) THEN\n        RAISE EXCEPTION 'BOOK_AUTHORITY_STATE_TRANSITION';\n    END IF;\n    NEW.changed_at := clock_timestamp();\n    RETURN NEW;\nEND;", "books_authority_state_log": "BEGIN\n    INSERT INTO public.books_authority_state_audit VALUES\n        (NEW.object_id,NEW.revision,NEW.status,NEW.audit_actor,NEW.changed_at);\n    RETURN NEW;\nEND;"}$json_bodies$::jsonb;
    expected_triggers constant jsonb := $json_triggers$[["books_authority_audit_immutable", "books_authority_state_audit", "books_authority_immutable", 27], ["books_authority_object_immutable", "books_authority_object", "books_authority_immutable", 27], ["books_authority_phase_immutable", "books_authority_phase", "books_authority_immutable", 27], ["books_authority_state_guard", "books_authority_state", "books_authority_state_transition", 31], ["books_authority_state_history", "books_authority_state", "books_authority_state_log", 21]]$json_triggers$::jsonb;
    expected_fks constant jsonb := $json_fks$[["books_authority_attempt", "grant_id"], ["books_authority_phase", "grant_id"], ["books_authority_state", "object_id"], ["books_authority_state_audit", "object_id"]]$json_fks$::jsonb;
    got jsonb; item record; row_ record; table_id oid; principal_id oid;
    reader_acl_count integer; count_ integer;
BEGIN
    IF current_database() IS DISTINCT FROM current_setting('books.authority_expected_database',true)
       OR current_user IS DISTINCT FROM current_setting('books.authority_expected_owner',true)
       OR current_user<>session_user
       OR owner_id IS DISTINCT FROM (SELECT datdba FROM pg_catalog.pg_database WHERE datname=current_database()) THEN
        RAISE EXCEPTION 'BOOKS_AUTHORITY_TARGET_OR_OWNER_MISMATCH';
    END IF;
    IF current_setting('books.authority_expected_checksum',true) !~ '^[0-9a-f]{64}$'
       OR NOT EXISTS (SELECT FROM public.schema_migration WHERE migration_key='0403_books_authority_core'
                     AND checksum=current_setting('books.authority_expected_checksum',true)) THEN
        RAISE EXCEPTION 'BOOKS_AUTHORITY_PROVENANCE_MISMATCH';
    END IF;
    IF (SELECT count(*) FROM pg_catalog.pg_class WHERE relnamespace=public_id AND relname=ANY(tabnames))<>5 THEN
        RAISE EXCEPTION 'BOOKS_AUTHORITY_PARTIAL_SCHEMA';
    END IF;
    IF EXISTS (SELECT FROM pg_catalog.pg_class c WHERE c.relnamespace=public_id AND c.relname=ANY(tabnames)
               AND (c.relkind<>'r' OR c.relpersistence<>'p' OR c.relowner<>owner_id
                    OR c.relrowsecurity OR c.relforcerowsecurity OR c.relispartition OR c.reloptions IS NOT NULL))
       OR EXISTS (SELECT FROM pg_catalog.pg_inherits i JOIN pg_catalog.pg_class c ON c.oid=i.inhrelid
                  WHERE c.relnamespace=public_id AND c.relname=ANY(tabnames))
       OR EXISTS (SELECT FROM pg_catalog.pg_policy p JOIN pg_catalog.pg_class c ON c.oid=p.polrelid
                  WHERE c.relnamespace=public_id AND c.relname=ANY(tabnames)) THEN
        RAISE EXCEPTION 'BOOKS_AUTHORITY_TABLE_DEFINITION_DRIFT';
    END IF;
    FOR item IN SELECT key,value FROM pg_catalog.jsonb_each(expected_cols) LOOP
        table_id:=pg_catalog.to_regclass('public.'||item.key);
        SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),
                            a.attnotnull,COALESCE(pg_catalog.pg_get_expr(d.adbin,d.adrelid),'')) ORDER BY a.attnum)
          INTO got FROM pg_catalog.pg_attribute a LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
         WHERE a.attrelid=table_id AND a.attnum>0 AND NOT a.attisdropped;
        IF got IS DISTINCT FROM item.value OR EXISTS (SELECT FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
             WHERE a.attrelid=table_id AND a.attnum>0 AND (a.attisdropped OR a.attidentity<>'' OR a.attgenerated<>'' OR a.attcollation<>t.typcollation)) THEN
            RAISE EXCEPTION 'BOOKS_AUTHORITY_COLUMN_DRIFT';
        END IF;
        SELECT pg_catalog.to_jsonb(ARRAY(SELECT a.attname::text FROM pg_catalog.pg_constraint k
             CROSS JOIN LATERAL pg_catalog.unnest(k.conkey) WITH ORDINALITY n(attnum,ord)
             JOIN pg_catalog.pg_attribute a ON a.attrelid=k.conrelid AND a.attnum=n.attnum
             WHERE k.conrelid=table_id AND k.contype='p' ORDER BY n.ord)) INTO got;
        IF got IS DISTINCT FROM expected_pks->item.key OR
           (SELECT count(*)FROM pg_catalog.pg_constraint WHERE conrelid=table_id AND contype='p')<>1 THEN
            RAISE EXCEPTION 'BOOKS_AUTHORITY_PRIMARY_KEY_DRIFT';
        END IF;
    END LOOP;
    IF (SELECT count(*) FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid=k.conrelid
         WHERE c.relnamespace=public_id AND c.relname=ANY(tabnames) AND k.contype='c')<>7 THEN
        RAISE EXCEPTION 'BOOKS_AUTHORITY_CHECK_DRIFT';
    END IF;
    FOR item IN SELECT key,value FROM pg_catalog.jsonb_each(expected_checks) LOOP
        SELECT pg_catalog.jsonb_agg(pg_catalog.btrim(pg_catalog.pg_get_expr(k.conbin,k.conrelid),'()')) INTO got
          FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid=k.conrelid
          WHERE c.relnamespace=public_id AND c.relname=item.value->>0 AND k.contype='c'
           AND k.conname=item.key AND k.convalidated AND NOT k.connoinherit;
        IF got IS DISTINCT FROM pg_catalog.jsonb_build_array(pg_catalog.btrim(item.value->>1,'()')) THEN
            RAISE EXCEPTION 'BOOKS_AUTHORITY_CHECK_DRIFT';
        END IF;
    END LOOP;
    SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(c.relname,a.attname) ORDER BY c.relname)
      INTO got FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid=k.conrelid
      JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.conkey[1]
     WHERE c.relnamespace=public_id AND c.relname=ANY(tabnames) AND k.contype='f'
       AND k.confrelid=pg_catalog.to_regclass('public.books_authority_object') AND k.confkey=ARRAY[1]::smallint[]
       AND pg_catalog.array_length(k.conkey,1)=1 AND k.confupdtype='a' AND k.confdeltype='a' AND k.confmatchtype='s';
    IF got IS DISTINCT FROM expected_fks THEN RAISE EXCEPTION 'BOOKS_AUTHORITY_FOREIGN_KEY_DRIFT'; END IF;
    IF (SELECT count(*)FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid=k.conrelid
         WHERE c.relnamespace=public_id AND c.relname=ANY(tabnames) AND k.contype='f')<>4
       OR EXISTS (SELECT FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid=k.conrelid
                   WHERE c.relnamespace=public_id AND c.relname=ANY(tabnames)
                     AND (k.contype IN ('u','x') OR k.condeferrable OR k.condeferred OR NOT k.convalidated))
       OR EXISTS (SELECT FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indrelid
                   WHERE c.relnamespace=public_id AND c.relname=ANY(tabnames)
                     AND (NOT i.indisprimary OR NOT i.indisvalid OR NOT i.indisready OR NOT i.indimmediate)) THEN
        RAISE EXCEPTION 'BOOKS_AUTHORITY_CONSTRAINT_OR_INDEX_DRIFT';
    END IF;
    IF (SELECT count(*) FROM pg_catalog.pg_proc WHERE pronamespace=public_id AND proname IN
          ('books_authority_immutable','books_authority_state_transition','books_authority_state_log'))<>3 THEN
        RAISE EXCEPTION 'BOOKS_AUTHORITY_FUNCTION_DRIFT';
    END IF;
    FOR item IN SELECT key,value FROM pg_catalog.jsonb_each(expected_bodies) LOOP
        SELECT p.* INTO row_ FROM pg_catalog.pg_proc p WHERE p.pronamespace=public_id AND p.proname=item.key;
        IF row_.pronargs<>0 OR row_.prorettype<>'pg_catalog.trigger'::pg_catalog.regtype OR row_.proretset OR row_.proowner<>owner_id
           OR row_.prolang<>(SELECT oid FROM pg_catalog.pg_language WHERE lanname='plpgsql') OR row_.prokind<>'f'
           OR row_.prosecdef OR row_.proisstrict OR row_.proleakproof OR row_.provolatile<>'v' OR row_.proparallel<>'u'
           OR row_.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']::text[]
           OR pg_catalog.btrim(pg_catalog.replace(row_.prosrc,E'\r\n',E'\n')) IS DISTINCT FROM item.value#>>'{}' THEN
            RAISE EXCEPTION 'BOOKS_AUTHORITY_FUNCTION_DRIFT';
        END IF;
    END LOOP;
    SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(t.tgname,c.relname,p.proname,t.tgtype) ORDER BY t.tgname)
      INTO got FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
     WHERE c.relnamespace=public_id AND c.relname=ANY(tabnames) AND NOT t.tgisinternal
       AND p.pronamespace=public_id AND t.tgenabled='O' AND t.tgnargs=0 AND t.tgargs=''::bytea AND t.tgqual IS NULL
       AND t.tgattr=''::pg_catalog.int2vector AND t.tgconstraint=0 AND NOT t.tgdeferrable AND NOT t.tginitdeferred;
    IF got IS DISTINCT FROM expected_triggers OR
       (SELECT count(*)FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
         WHERE c.relnamespace=public_id AND c.relname=ANY(tabnames) AND NOT t.tgisinternal)<>5
       OR EXISTS (SELECT FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
                   WHERE c.relnamespace=public_id AND c.relname=ANY(tabnames) AND t.tgenabled<>'O') THEN
        RAISE EXCEPTION 'BOOKS_AUTHORITY_TRIGGER_DRIFT';
    END IF;
    principal_id:=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname='bridge_school_worker_principal');
    IF EXISTS (SELECT FROM pg_catalog.pg_class c CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(c.relacl,pg_catalog.acldefault('r',c.relowner))) x
                WHERE c.relnamespace=public_id AND c.relname=ANY(tabnames) AND x.grantee<>owner_id
                  AND NOT (principal_id IS NOT NULL AND x.grantee=principal_id AND c.relname<>'books_authority_state_audit' AND x.privilege_type='SELECT' AND NOT x.is_grantable))
       OR EXISTS (SELECT FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
                   CROSS JOIN LATERAL pg_catalog.aclexplode(a.attacl) x
                   WHERE c.relnamespace=public_id AND c.relname=ANY(tabnames)
                     AND a.attnum>0 AND NOT a.attisdropped AND x.grantee<>owner_id)
       OR EXISTS (SELECT FROM pg_catalog.pg_proc p CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) x
                   WHERE p.pronamespace=public_id AND p.proname IN('books_authority_immutable','books_authority_state_transition','books_authority_state_log')
                     AND x.grantee<>owner_id) THEN
        RAISE EXCEPTION 'BOOKS_AUTHORITY_ACL_DRIFT';
    END IF;
    SELECT count(*) INTO reader_acl_count FROM pg_catalog.pg_class c
      CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(c.relacl,pg_catalog.acldefault('r',c.relowner))) x
     WHERE c.relnamespace=public_id AND c.relname=ANY(tabnames) AND x.grantee=principal_id AND x.privilege_type='SELECT';
    IF reader_acl_count NOT IN (0,4) THEN RAISE EXCEPTION 'BOOKS_AUTHORITY_PARTIAL_READER_ACL'; END IF;
    PERFORM pg_catalog.set_config('books.authority_reader_state',CASE reader_acl_count WHEN 0 THEN 'CORE_ONLY' ELSE 'READER_SELECT_PRESENT' END,true);
END $books_assert$;
