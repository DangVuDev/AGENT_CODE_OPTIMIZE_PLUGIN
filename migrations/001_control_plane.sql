BEGIN;

CREATE SCHEMA IF NOT EXISTS optimizer_control;

CREATE TABLE IF NOT EXISTS optimizer_control.schema_migrations (
    version bigint PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now(),
    checksum text NOT NULL
);

CREATE TABLE IF NOT EXISTS optimizer_control.cases (
    tenant_id text NOT NULL,
    case_id text NOT NULL,
    thread_id text NOT NULL,
    lane text NOT NULL CHECK (lane IN ('manual', 'automatic')),
    status text NOT NULL,
    current_node text,
    cancellation_requested_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, case_id),
    UNIQUE (tenant_id, thread_id)
);

CREATE TABLE IF NOT EXISTS optimizer_control.node_intents (
    tenant_id text NOT NULL,
    idempotency_key text NOT NULL,
    case_id text NOT NULL,
    node_id text NOT NULL,
    input_digest text NOT NULL,
    status text NOT NULL CHECK (status IN ('pending', 'completed', 'unknown', 'failed')),
    output_artifact_type text,
    output_artifact_id text,
    output_digest text,
    lease_owner text,
    lease_expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, idempotency_key),
    FOREIGN KEY (tenant_id, case_id)
        REFERENCES optimizer_control.cases (tenant_id, case_id)
);

CREATE TABLE IF NOT EXISTS optimizer_control.outbox (
    event_id uuid PRIMARY KEY,
    tenant_id text NOT NULL,
    case_id text NOT NULL,
    topic text NOT NULL,
    payload_ref text NOT NULL,
    payload_digest text NOT NULL,
    available_at timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz,
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error_ref text,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, case_id)
        REFERENCES optimizer_control.cases (tenant_id, case_id)
);

CREATE INDEX IF NOT EXISTS idx_cases_status
    ON optimizer_control.cases (tenant_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_node_intents_lease
    ON optimizer_control.node_intents (status, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_outbox_available
    ON optimizer_control.outbox (available_at)
    WHERE delivered_at IS NULL;

COMMIT;
