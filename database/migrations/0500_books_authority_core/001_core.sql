CREATE TABLE public.books_authority_object (
    object_id text PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('policy','principal','grant','catalog','verifier','validation','catalog_dependency')),
    body jsonb NOT NULL CHECK (jsonb_typeof(body) = 'object')
);
CREATE TABLE public.books_authority_state (
    object_id text PRIMARY KEY REFERENCES public.books_authority_object,
    revision bigint NOT NULL CHECK (revision >= 0),
    status text NOT NULL CHECK (status IN ('active','revoked')),
    audit_actor text NOT NULL,
    changed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE public.books_authority_state_audit (
    object_id text NOT NULL REFERENCES public.books_authority_object,
    revision bigint NOT NULL,
    status text NOT NULL,
    audit_actor text NOT NULL,
    changed_at timestamptz NOT NULL,
    PRIMARY KEY (object_id, revision)
);
CREATE TABLE public.books_authority_attempt (
    attempt_id uuid PRIMARY KEY,
    grant_id text NOT NULL REFERENCES public.books_authority_object,
    phase text NOT NULL,
    principal text NOT NULL,
    target text NOT NULL,
    school_id uuid NOT NULL,
    status text NOT NULL CHECK (status IN ('unresolved','success')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE public.books_authority_phase (
    grant_id text NOT NULL REFERENCES public.books_authority_object,
    phase text NOT NULL CHECK (phase IN ('IMPORT_STAGED','DELIVER_RECORDED_OUTBOX',
                                        'ACTIVATE_REVIEWED_WORLD','DELIVER_PUBLISHED_OUTBOX')),
    receipt jsonb NOT NULL CHECK (jsonb_typeof(receipt) = 'object'),
    PRIMARY KEY (grant_id, phase)
);
-- 0005's public default ACL gives bridge_school_reader SELECT on future tables.
-- Remove it only from the five tables created above, in this same transaction.
REVOKE SELECT ON TABLE public.books_authority_object, public.books_authority_state,
    public.books_authority_state_audit, public.books_authority_attempt,
    public.books_authority_phase FROM bridge_school_reader;
CREATE FUNCTION public.books_authority_immutable() RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'BOOK_AUTHORITY_IMMUTABLE';
END;
$$;
CREATE TRIGGER books_authority_object_immutable BEFORE UPDATE OR DELETE
ON public.books_authority_object FOR EACH ROW EXECUTE FUNCTION public.books_authority_immutable();
CREATE TRIGGER books_authority_phase_immutable BEFORE UPDATE OR DELETE
ON public.books_authority_phase FOR EACH ROW EXECUTE FUNCTION public.books_authority_immutable();
CREATE TRIGGER books_authority_audit_immutable BEFORE UPDATE OR DELETE
ON public.books_authority_state_audit FOR EACH ROW EXECUTE FUNCTION public.books_authority_immutable();

CREATE FUNCTION public.books_authority_state_transition() RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'BOOK_AUTHORITY_STATE_DELETE'; END IF;
    IF TG_OP = 'UPDATE' AND
       (NEW.object_id <> OLD.object_id OR OLD.status <> 'active'
        OR NEW.status <> 'revoked' OR NEW.revision <> OLD.revision + 1) THEN
        RAISE EXCEPTION 'BOOK_AUTHORITY_STATE_TRANSITION';
    END IF;
    NEW.changed_at := clock_timestamp();
    RETURN NEW;
END;
$$;
CREATE FUNCTION public.books_authority_state_log() RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
BEGIN
    INSERT INTO public.books_authority_state_audit VALUES
        (NEW.object_id,NEW.revision,NEW.status,NEW.audit_actor,NEW.changed_at);
    RETURN NEW;
END;
$$;
CREATE TRIGGER books_authority_state_guard BEFORE INSERT OR UPDATE OR DELETE
ON public.books_authority_state FOR EACH ROW EXECUTE FUNCTION public.books_authority_state_transition();
CREATE TRIGGER books_authority_state_history AFTER INSERT OR UPDATE
ON public.books_authority_state FOR EACH ROW EXECUTE FUNCTION public.books_authority_state_log();


REVOKE ALL ON TABLE public.books_authority_object, public.books_authority_state, public.books_authority_state_audit, public.books_authority_attempt, public.books_authority_phase FROM PUBLIC;
REVOKE ALL ON FUNCTION public.books_authority_immutable(), public.books_authority_state_transition(), public.books_authority_state_log() FROM PUBLIC;
