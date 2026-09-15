BEGIN;

CREATE TABLE IF NOT EXISTS optimizer_control.model_call_deferrals (
    tenant_id text NOT NULL,
    deferral_id text NOT NULL,
    case_id text NOT NULL,
    thread_id text,
    node_id text NOT NULL,
    idempotency_key text NOT NULL,
    request_digest text NOT NULL,
    status text NOT NULL CHECK (status IN ('scheduled', 'running', 'succeeded', 'failed')),
    role text NOT NULL CHECK (role IN ('generator', 'judge')),
    prompt_version text NOT NULL,
    primary_model_id text NOT NULL,
    failures jsonb NOT NULL,
    retry_after_seconds double precision NOT NULL CHECK (retry_after_seconds >= 0),
    available_at timestamptz NOT NULL,
    lease_owner text,
    lease_expires_at timestamptz,
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error_ref text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, deferral_id),
    FOREIGN KEY (tenant_id, case_id)
        REFERENCES optimizer_control.cases (tenant_id, case_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_model_call_deferrals_idempotency
    ON optimizer_control.model_call_deferrals (tenant_id, idempotency_key)
    WHERE status IN ('scheduled', 'running');

CREATE INDEX IF NOT EXISTS idx_model_call_deferrals_due
    ON optimizer_control.model_call_deferrals (tenant_id, status, available_at, lease_expires_at)
    WHERE status = 'scheduled';

COMMIT;
