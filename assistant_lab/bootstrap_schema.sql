-- Assistant Lab v1.1: short-lived bootstrap secret delivery for the existing OCI VM.
-- The ticket table is owner-only. Vercel gets EXECUTE on one SECURITY DEFINER
-- function which accepts only a SHA-256 token digest and returns a bounded payload.

CREATE TABLE IF NOT EXISTS assistant_lab.bootstrap_ticket (
    token_sha256 text PRIMARY KEY
        CHECK (token_sha256 ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    claim_count smallint NOT NULL DEFAULT 0
        CHECK (claim_count BETWEEN 0 AND 3),
    last_claimed_at timestamptz,
    payload_json jsonb NOT NULL,
    purpose text NOT NULL DEFAULT 'OCI_ASSISTANT_LAB_BOOTSTRAP'
);

REVOKE ALL ON assistant_lab.bootstrap_ticket FROM PUBLIC, bridge_school_app, assistant_lab_worker;

CREATE OR REPLACE FUNCTION assistant_lab.claim_bootstrap_ticket(p_token_sha256 text)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, assistant_lab
AS $$
DECLARE
    v_payload jsonb;
BEGIN
    UPDATE assistant_lab.bootstrap_ticket
    SET claim_count = claim_count + 1,
        last_claimed_at = now()
    WHERE token_sha256 = p_token_sha256
      AND revoked_at IS NULL
      AND expires_at > now()
      AND claim_count < 3
    RETURNING payload_json INTO v_payload;
    RETURN v_payload;
END;
$$;

REVOKE ALL ON FUNCTION assistant_lab.claim_bootstrap_ticket(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION assistant_lab.claim_bootstrap_ticket(text) TO bridge_school_app;
