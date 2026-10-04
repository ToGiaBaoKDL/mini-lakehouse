SELECT format('CREATE ROLE t0_trading LOGIN PASSWORD %L', :'application_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 't0_trading') \gexec

SELECT format('ALTER ROLE t0_trading WITH LOGIN CONNECTION LIMIT 20 PASSWORD %L', :'application_password') \gexec

SELECT 'CREATE DATABASE t0_trading OWNER t0_trading'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 't0_trading') \gexec

REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
REVOKE CONNECT ON DATABASE t0_trading FROM PUBLIC;
GRANT CONNECT ON DATABASE t0_trading TO t0_trading;

SELECT 'GRANT CONNECT ON DATABASE t0_trading TO lakehouse_monitor'
WHERE EXISTS (SELECT FROM pg_roles WHERE rolname = 'lakehouse_monitor') \gexec

\connect t0_trading

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO t0_trading;

SET ROLE t0_trading;

-- Paper only. Checkpoint CAS and immutable operation inserts share one transaction.
CREATE TABLE IF NOT EXISTS paper_sessions (
    trade_date date NOT NULL,
    account_sha256 text NOT NULL CHECK (account_sha256 ~ '^[0-9a-f]{64}$'),
    revision bigint NOT NULL CHECK (revision >= 0),
    state_sha256 text NOT NULL CHECK (state_sha256 ~ '^[0-9a-f]{64}$'),
    state_json text NOT NULL,
    PRIMARY KEY (trade_date, account_sha256)
);

-- The capability currently owns one account. A refreshed snapshot must not create a second
-- independent cash/inventory pool within the same session.
CREATE UNIQUE INDEX IF NOT EXISTS paper_sessions_one_account_per_day ON paper_sessions (trade_date);

CREATE TABLE IF NOT EXISTS paper_operations (
    trade_date date NOT NULL,
    account_sha256 text NOT NULL,
    sequence bigint NOT NULL CHECK (sequence > 0),
    operation_sha256 text NOT NULL CHECK (operation_sha256 ~ '^[0-9a-f]{64}$'),
    operation_json text NOT NULL,
    PRIMARY KEY (trade_date, account_sha256, sequence),
    UNIQUE (trade_date, account_sha256, operation_sha256),
    FOREIGN KEY (trade_date, account_sha256) REFERENCES paper_sessions (trade_date, account_sha256)
);

-- Shadow delivery is independent of paper admission and never authorizes capital.
-- This capability has one configured Telegram destination. All senders share its gate.
CREATE TABLE IF NOT EXISTS shadow_delivery_control (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    invalidated_through timestamptz,
    cooldown_until timestamptz NOT NULL DEFAULT '-infinity'
);
INSERT INTO shadow_delivery_control (singleton) VALUES (true) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS shadow_deliveries (
    selection_id text PRIMARY KEY CHECK (selection_id ~ '^[0-9a-f]{64}$'),
    symbol text NOT NULL,
    decision_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL CHECK (expires_at > decision_at),
    payload_json text NOT NULL,
    payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    status text NOT NULL CHECK (status IN ('PENDING', 'SENDING', 'SENT', 'EXPIRED', 'FAILED', 'UNKNOWN')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    lease_token uuid,
    lease_until timestamptz,
    provider_message_id text,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (symbol, decision_at),
    CHECK ((status = 'SENDING') = (lease_token IS NOT NULL)),
    CHECK ((status = 'SENDING') = (lease_until IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS shadow_deliveries_pending
    ON shadow_deliveries (available_at, decision_at) WHERE status = 'PENDING';
CREATE INDEX IF NOT EXISTS shadow_deliveries_leases
    ON shadow_deliveries (lease_until) WHERE status = 'SENDING';

RESET ROLE;
